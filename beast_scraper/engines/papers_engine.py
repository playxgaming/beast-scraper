"""Research paper search (OpenAlex + arXiv + Semantic Scholar) and PDF-to-Markdown parsing."""

import asyncio
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import httpx
import pymupdf

from beast_scraper.core.http import download_url

_ARXIV_ID_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|^)(\d{4}\.\d{4,5}(?:v\d+)?|[a-z\-]+/\d{7})", re.I)


def undo_inverted_index(inv_index: dict[str, list[int]] | None) -> str | None:
    """Reconstruct plaintext abstract from OpenAlex abstract_inverted_index."""
    if not inv_index:
        return None
    words = [(pos, word) for word, positions in inv_index.items() for pos in positions]
    words.sort(key=lambda pair: pair[0])
    return " ".join(word for _, word in words)


def _normalize_title(title: str | None) -> str:
    if not title:
        return ""
    return re.sub(r"[^a-z0-9]+", "", title.lower())


def build_bibtex(paper: dict[str, Any]) -> str:
    authors = " and ".join(paper.get("authors") or ["Unknown"])
    year = str(paper.get("year") or "n.d.")
    first_author = (paper.get("authors") or ["paper"])[0].split()[-1].lower()
    key = re.sub(r"[^a-z0-9]", "", f"{first_author}{year}")
    title = (paper.get("title") or "Untitled").replace("{", "").replace("}", "")
    lines = [
        f"@article{{{key},",
        f"  title={{{title}}},",
        f"  author={{{authors}}},",
        f"  year={{{year}}},",
    ]
    if paper.get("venue"):
        lines.append(f"  journal={{{paper['venue']}}},")
    if paper.get("doi"):
        lines.append(f"  doi={{{paper['doi']}}},")
    if paper.get("arxiv_id"):
        lines.append(f"  eprint={{{paper['arxiv_id']}}},")
        lines.append("  archivePrefix={arXiv},")
    if paper.get("url"):
        lines.append(f"  url={{{paper['url']}}},")
    lines.append("}")
    return "\n".join(lines)


class PapersEngine:
    """Queries OpenAlex, arXiv, and Semantic Scholar, and parses open-access PDFs to Markdown."""

    def __init__(
        self,
        pdf_max_mb: int = 30,
        allow_private_urls: bool = False,
        openalex_api_key: str = "",
        pdf_layout_enabled: bool = False,
    ) -> None:
        self.pdf_max_bytes = pdf_max_mb * 1024 * 1024
        self.allow_private_urls = allow_private_urls
        self.openalex_api_key = openalex_api_key
        self.pdf_layout_enabled = pdf_layout_enabled

    async def _search_openalex(self, client: httpx.AsyncClient, query: str, limit: int) -> list[dict[str, Any]]:
        resp = await client.get(
            "https://api.openalex.org/works",
            params={
                "search": query,
                "select": "id,title,doi,publication_year,cited_by_count,open_access,abstract_inverted_index,authorships,primary_location",
                "per-page": limit,
                **({"api_key": self.openalex_api_key} if self.openalex_api_key else {}),
            },
        )
        resp.raise_for_status()
        out = []
        for w in resp.json().get("results", []):
            oa = w.get("open_access") or {}
            loc = w.get("primary_location") or {}
            src = loc.get("source") or {}
            doi = (w.get("doi") or "").replace("https://doi.org/", "") or None
            out.append(
                {
                    "source": "openalex",
                    "title": w.get("title"),
                    "year": w.get("publication_year"),
                    "venue": src.get("display_name"),
                    "citations": w.get("cited_by_count", 0),
                    "doi": doi,
                    "arxiv_id": None,
                    "authors": [
                        a["author"]["display_name"]
                        for a in w.get("authorships", [])
                        if a.get("author", {}).get("display_name")
                    ][:10],
                    "is_oa": bool(oa.get("is_oa")),
                    "pdf_url": loc.get("pdf_url") or oa.get("oa_url"),
                    "url": w.get("doi") or w.get("id"),
                    "abstract": undo_inverted_index(w.get("abstract_inverted_index")),
                }
            )
        return out

    async def _search_arxiv(self, client: httpx.AsyncClient, query: str, limit: int) -> list[dict[str, Any]]:
        resp = await client.get(
            "https://export.arxiv.org/api/query",
            params={"search_query": f"all:{query}", "start": 0, "max_results": limit},
        )
        resp.raise_for_status()
        ns = {"a": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(resp.text)
        out = []
        for entry in root.findall("a:entry", ns):
            abs_url = entry.findtext("a:id", "", ns).strip()
            aid = abs_url.split("/abs/")[-1]
            published = entry.findtext("a:published", "", ns)
            out.append(
                {
                    "source": "arxiv",
                    "title": " ".join(entry.findtext("a:title", "", ns).split()),
                    "year": int(published[:4]) if published[:4].isdigit() else None,
                    "venue": "arXiv",
                    "citations": None,
                    "doi": None,
                    "arxiv_id": aid,
                    "authors": [
                        a.findtext("a:name", "", ns).strip()
                        for a in entry.findall("a:author", ns)
                        if a.findtext("a:name", "", ns)
                    ][:10],
                    "is_oa": True,
                    "pdf_url": f"https://arxiv.org/pdf/{aid}.pdf",
                    "url": abs_url,
                    "abstract": " ".join(entry.findtext("a:summary", "", ns).split()),
                }
            )
        return out

    async def _search_semantic_scholar(self, client: httpx.AsyncClient, query: str, limit: int) -> list[dict[str, Any]]:
        resp = await client.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={
                "query": query,
                "limit": limit,
                "fields": "title,year,venue,citationCount,authors,externalIds,openAccessPdf,abstract,url",
            },
        )
        resp.raise_for_status()
        out = []
        for p in resp.json().get("data", []):
            ext = p.get("externalIds") or {}
            oa_pdf = p.get("openAccessPdf") or {}
            out.append(
                {
                    "source": "semantic_scholar",
                    "title": p.get("title"),
                    "year": p.get("year"),
                    "venue": p.get("venue") or None,
                    "citations": p.get("citationCount"),
                    "doi": ext.get("DOI"),
                    "arxiv_id": ext.get("ArXiv"),
                    "authors": [a.get("name") for a in p.get("authors", []) if a.get("name")][:10],
                    "is_oa": bool(oa_pdf.get("url") or ext.get("ArXiv")),
                    "pdf_url": oa_pdf.get("url")
                    or (f"https://arxiv.org/pdf/{ext['ArXiv']}.pdf" if ext.get("ArXiv") else None),
                    "url": p.get("url"),
                    "abstract": p.get("abstract"),
                }
            )
        return out

    async def search(self, query: str, limit: int = 10) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=20.0,
            headers={"User-Agent": "BeastScraper/0.1 (mailto:local@localhost)"},
            follow_redirects=True,
        ) as client:
            results = await asyncio.gather(
                self._search_openalex(client, query, limit),
                self._search_arxiv(client, query, limit),
                self._search_semantic_scholar(client, query, limit),
                return_exceptions=True,
            )

        merged: list[dict[str, Any]] = []
        errors: dict[str, str] = {}
        source_names = ["openalex", "arxiv", "semantic_scholar"]
        seen_keys: dict[str, dict[str, Any]] = {}

        for name, item in zip(source_names, results):
            if isinstance(item, Exception):
                errors[name] = "Provider unavailable or rate limited."
                continue
            for p in item:
                dedup_key = (
                    f"doi:{p['doi'].lower()}"
                    if p.get("doi")
                    else f"arxiv:{p['arxiv_id'].split('v')[0]}"
                    if p.get("arxiv_id")
                    else f"title:{_normalize_title(p.get('title'))}"
                )
                if dedup_key in seen_keys:
                    existing = seen_keys[dedup_key]
                    # Enrich missing fields across sources
                    for field in ("pdf_url", "doi", "arxiv_id", "abstract", "citations", "venue"):
                        if not existing.get(field) and p.get(field):
                            existing[field] = p[field]
                    if p.get("is_oa"):
                        existing["is_oa"] = True
                    if name not in existing["sources"]:
                        existing["sources"].append(name)
                else:
                    p["sources"] = [name]
                    p["bibtex"] = build_bibtex(p)
                    seen_keys[dedup_key] = p
                    merged.append(p)

        merged.sort(key=lambda x: (bool(x.get("pdf_url")), x.get("citations") or 0), reverse=True)
        papers = merged[:limit]
        for paper in papers:
            paper["bibtex"] = build_bibtex(paper)
        return {
            "query": query,
            "count": len(papers),
            "papers": papers,
            "source_errors": errors or None,
        }

    async def resolve_and_parse(
        self,
        identifier: str,
        page_chunks: bool = False,
        max_pages: int | None = None,
    ) -> dict[str, Any]:
        ident = identifier.strip()
        metadata: dict[str, Any] = {"identifier": ident}
        pdf_url: str | None = None

        arxiv_match = _ARXIV_ID_RE.search(ident)
        if arxiv_match:
            aid = arxiv_match.group(1)
            pdf_url = f"https://arxiv.org/pdf/{aid}.pdf"
            metadata["arxiv_id"] = aid
        elif ident.lower().startswith(("http://", "https://")) and not ident.lower().startswith("https://doi.org/"):
            pdf_url = ident
        else:
            # Resolve DOI or title via OpenAlex
            doi = ident.replace("https://doi.org/", "")
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                r = await client.get(
                    f"https://api.openalex.org/works/https://doi.org/{doi}"
                    if "/" in doi
                    else "https://api.openalex.org/works",
                    params={
                        **({} if "/" in doi else {"search": ident, "per-page": 1}),
                        **({"api_key": self.openalex_api_key} if self.openalex_api_key else {}),
                    },
                )
                if r.status_code == 200:
                    data = r.json()
                    work = data if "/" in doi else (data.get("results") or [None])[0]
                    if work:
                        oa = work.get("open_access") or {}
                        loc = work.get("primary_location") or {}
                        pdf_url = loc.get("pdf_url") or oa.get("oa_url")
                        metadata.update(
                            {
                                "title": work.get("title"),
                                "year": work.get("publication_year"),
                                "doi": (work.get("doi") or "").replace("https://doi.org/", "") or None,
                                "authors": [
                                    a["author"]["display_name"]
                                    for a in work.get("authorships", [])
                                    if a.get("author", {}).get("display_name")
                                ],
                                "abstract": undo_inverted_index(work.get("abstract_inverted_index")),
                            }
                        )

        if not pdf_url:
            raise ValueError(
                f"Could not find an open-access PDF URL for '{identifier}'. Try passing a direct PDF URL or arXiv ID."
            )

        resp = await download_url(
            pdf_url,
            max_bytes=self.pdf_max_bytes,
            timeout=60.0,
            allow_private=self.allow_private_urls,
        )
        resp.raise_for_status()
        if not resp.content.lstrip().startswith(b"%PDF-"):
            raise ValueError("The URL did not return a PDF file.")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path = Path(tmp.name)

        try:

            def parse_pdf():
                with pymupdf.open(tmp_path) as doc:
                    pages_arg = list(range(min(max_pages or doc.page_count, doc.page_count)))
                    if not self.pdf_layout_enabled:
                        chunks = [{"metadata": {"page": i + 1}, "text": doc[i].get_text(sort=True)} for i in pages_arg]
                        return (
                            chunks
                            if page_chunks
                            else "\n\n".join(f"## Page {c['metadata']['page']}\n\n{c['text']}" for c in chunks)
                        )
                    try:
                        import pymupdf4llm
                    except ImportError as exc:
                        raise ValueError("Layout extraction requires the optional [pdf] extra.") from exc
                    return pymupdf4llm.to_markdown(doc, pages=pages_arg, page_chunks=page_chunks)

            extracted = await asyncio.to_thread(parse_pdf)
        finally:
            tmp_path.unlink(missing_ok=True)

        metadata["pdf_url"] = pdf_url
        metadata["bibtex"] = build_bibtex(metadata)
        if page_chunks:
            metadata["pages"] = extracted
        else:
            metadata["markdown"] = extracted
        return metadata
