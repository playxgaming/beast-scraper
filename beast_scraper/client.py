"""Lightweight Python SDK client for BeastScraper (with Scrapingdog-style convenience methods)."""

from typing import Any

import httpx


class BeastScraperClient:
    """Synchronous HTTP client for a running BeastScraper server."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key} if self.api_key else {}

    def scrape(
        self,
        url: str,
        *,
        dynamic: bool = False,
        format: str = "markdown",
        engine: str = "auto",
        country: str | None = None,
        premium: bool = False,
        wait: int = 0,
        wait_for: str | None = None,
        extract_links: bool = False,
        extract_images: bool = False,
        screenshot: bool = False,
        bypass_cache: bool = False,
    ) -> dict[str, Any]:
        payload = {
            "url": url,
            "dynamic": dynamic,
            "format": format,
            "engine": engine,
            "country": country,
            "premium": premium,
            "wait": wait,
            "wait_for": wait_for,
            "extract_links": extract_links,
            "extract_images": extract_images,
            "screenshot": screenshot,
            "bypass_cache": bypass_cache,
        }
        with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
            r = client.post(f"{self.base_url}/scrape", json=payload)
            r.raise_for_status()
            return r.json()

    def crawl(self, url: str, *, max_pages: int = 10, max_depth: int = 2, dynamic: bool = False) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout * 3, headers=self._headers()) as client:
            r = client.post(
                f"{self.base_url}/crawl",
                json={"url": url, "max_pages": max_pages, "max_depth": max_depth, "dynamic": dynamic},
            )
            r.raise_for_status()
            return r.json()

    def search(self, query: str, *, results: int = 10, search_type: str = "web") -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
            r = client.get(
                f"{self.base_url}/search",
                params={"query": query, "results": results, "search_type": search_type},
            )
            r.raise_for_status()
            return r.json()

    def search_papers(self, query: str, *, limit: int = 10) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
            r = client.get(f"{self.base_url}/papers/search", params={"query": query, "limit": limit})
            r.raise_for_status()
            return r.json()

    def parse_paper(
        self, identifier: str, *, page_chunks: bool = False, max_pages: int | None = None
    ) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout * 2, headers=self._headers()) as client:
            r = client.post(
                f"{self.base_url}/papers/parse",
                json={"identifier": identifier, "page_chunks": page_chunks, "max_pages": max_pages},
            )
            r.raise_for_status()
            return r.json()

    def youtube_info(self, url: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
            r = client.get(f"{self.base_url}/youtube/info", params={"url": url})
            r.raise_for_status()
            return r.json()

    def youtube_transcript(self, url: str, *, lang: str = "en", format: str = "markdown") -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
            r = client.get(
                f"{self.base_url}/youtube/transcript",
                params={"url": url, "lang": lang, "format": format},
            )
            r.raise_for_status()
            return r.json()
