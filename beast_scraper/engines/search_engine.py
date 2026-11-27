"""Web search / SERP engine using self-hosted SearXNG (if configured) with DDGS fallback."""

import asyncio
from typing import Any

import httpx
from ddgs import DDGS

from beast_scraper.core.proxy_manager import ProxyManager


class SearchEngine:
    """Queries local SearXNG JSON API or falls back to DuckDuckGo via ddgs."""

    def __init__(self, proxy_manager: ProxyManager, searxng_url: str = "") -> None:
        self.proxy_manager = proxy_manager
        self.searxng_url = searxng_url.strip().rstrip("/")

    async def search(
        self,
        query: str,
        max_results: int = 10,
        search_type: str = "web",
        region: str = "wt-wt",
        timelimit: str | None = None,
        country: str | None = None,
    ) -> dict[str, Any]:
        # 1. Prefer self-hosted SearXNG if configured and reachable
        if self.searxng_url:
            try:
                categories = "news" if search_type == "news" else "general"
                async with httpx.AsyncClient(timeout=12.0) as client:
                    resp = await client.get(
                        f"{self.searxng_url}/search",
                        params={"q": query, "format": "json", "categories": categories},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        organic = [
                            {
                                "position": i + 1,
                                "title": r.get("title"),
                                "link": r.get("url"),
                                "snippet": r.get("content"),
                                "engines": r.get("engines"),
                            }
                            for i, r in enumerate(data.get("results", [])[:max_results])
                        ]
                        return {
                            "query": query,
                            "provider": "searxng",
                            "count": len(organic),
                            "organic_results": organic,
                        }
            except Exception:
                pass

        # 2. Fall back to ddgs (zero external infra required)
        proxy_url = self.proxy_manager.acquire(country=country)

        def _run_ddgs() -> list[dict[str, Any]]:
            with DDGS(proxy=proxy_url) as ddgs:
                if search_type == "news":
                    rows = list(
                        ddgs.news(
                            query,
                            region=region,
                            timelimit=timelimit,
                            max_results=max_results,
                        )
                    )
                    return [
                        {
                            "position": i + 1,
                            "title": r.get("title"),
                            "link": r.get("url"),
                            "snippet": r.get("body"),
                            "date": r.get("date"),
                            "source": r.get("source"),
                        }
                        for i, r in enumerate(rows)
                    ]

                rows = list(
                    ddgs.text(
                        query,
                        region=region,
                        timelimit=timelimit,
                        max_results=max_results,
                    )
                )
                return [
                    {
                        "position": i + 1,
                        "title": r.get("title"),
                        "link": r.get("href"),
                        "snippet": r.get("body"),
                    }
                    for i, r in enumerate(rows)
                ]

        organic = await asyncio.to_thread(_run_ddgs)
        return {
            "query": query,
            "provider": "ddgs",
            "count": len(organic),
            "organic_results": organic,
        }
