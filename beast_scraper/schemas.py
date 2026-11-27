"""Pydantic schemas for BeastScraper request and response payloads."""

from typing import Any, Literal

from pydantic import BaseModel, Field

ScrapeFormat = Literal["markdown", "html", "text", "json"]
ScrapeEngine = Literal["auto", "http", "playwright", "crawl4ai", "scrapling"]


class ScrapeRequest(BaseModel):
    """Request body for POST /scrape."""

    url: str = Field(..., description="Target webpage URL")
    dynamic: bool = Field(False, description="Use headless Chromium for JS rendering")
    engine: ScrapeEngine = Field("auto", description="Fetching engine to use")
    format: ScrapeFormat = Field("markdown", description="Primary content format")
    wait: int = Field(0, ge=0, le=35000, description="Extra wait time in ms after load")
    wait_for: str | None = Field(None, description="CSS selector to wait for in browser mode")
    css_selector: str | None = Field(None, description="Optional CSS selector to scope extraction")
    country: str | None = Field(None, description="ISO country code for proxy geo-targeting")
    premium: bool = Field(False, description="Prefer configured proxy pool (Scrapingdog compatibility)")
    proxy: str | None = Field(None, description="Per-request explicit proxy URL override")
    custom_headers: dict[str, str] | None = Field(None, description="Custom HTTP headers")
    extract_links: bool = Field(False, description="Include extracted page links")
    extract_images: bool = Field(False, description="Include extracted image URLs")
    screenshot: bool = Field(False, description="Return base64 PNG screenshot (uses Playwright)")
    bypass_cache: bool = Field(False, description="Skip SQLite response cache")


class ScrapeResponse(BaseModel):
    """Unified response for /scrape."""

    url: str
    final_url: str | None = None
    status_code: int
    engine: str
    cached: bool = False
    proxy_used: str | None = None
    title: str | None = None
    author: str | None = None
    date: str | None = None
    description: str | None = None
    format: ScrapeFormat = "markdown"
    content: str | dict[str, Any]
    links: list[dict[str, str]] | None = None
    images: list[dict[str, str]] | None = None
    screenshot_base64: str | None = None
    elapsed_ms: int = 0


class CrawlRequest(BaseModel):
    """Request body for POST /crawl."""

    url: str = Field(..., description="Seed URL to start crawling")
    max_pages: int = Field(10, ge=1, le=100)
    max_depth: int = Field(2, ge=0, le=5)
    same_domain: bool = Field(True, description="Stay on seed hostname")
    dynamic: bool = Field(False, description="Render pages with Playwright")
    format: Literal["markdown", "text", "html"] = "markdown"
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)
    concurrency: int = Field(4, ge=1, le=10)
    country: str | None = None


class PaperParseRequest(BaseModel):
    """Request body for POST /papers/parse."""

    identifier: str = Field(..., description="arXiv ID (e.g. 2301.07041), DOI, or direct PDF URL")
    page_chunks: bool = Field(False, description="Return page-by-page chunks with metadata")
    max_pages: int | None = Field(None, ge=1, le=300, description="Optional max pages to extract")
    bypass_cache: bool = False


class ProxyAddRequest(BaseModel):
    """Request body to register proxies at runtime."""

    proxies: list[str] = Field(..., description="Proxy URLs or `country|url` entries")
    replace: bool = Field(False, description="Replace existing static proxy pool")
