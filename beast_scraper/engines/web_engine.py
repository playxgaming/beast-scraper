"""Tiered web scraping, structured HTML/Markdown extraction, Playwright pooling, and BFS site crawler."""

import asyncio
import base64
import json
import re
import time
from collections import deque
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from markdownify import markdownify as md_convert
from trafilatura import bare_extraction
from trafilatura import extract as trafilatura_extract

from beast_scraper.core.http import download_url
from beast_scraper.core.proxy_manager import ProxyManager
from beast_scraper.core.rate_limit import DomainRateLimiter, validate_target_url
from beast_scraper.schemas import CrawlRequest, ScrapeRequest

try:
    from curl_cffi.requests import AsyncSession as CurlAsyncSession

    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

try:
    from playwright.async_api import Browser, Playwright, async_playwright

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class PlaywrightPool:
    """Lazy-initialized shared Chromium browser instance for dynamic scraping."""

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def get_browser(self) -> Browser:
        if not HAS_PLAYWRIGHT:
            raise RuntimeError(
                "Playwright is not installed. Run `pip install playwright && playwright install chromium`."
            )
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(headless=self.headless)
            return self._browser

    async def close(self) -> None:
        async with self._lock:
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            if self._pw is not None:
                await self._pw.stop()
                self._pw = None


def _looks_js_dependent(html: str, extracted_text: str | None) -> bool:
    """Heuristic to detect empty SPA shells that require browser rendering."""
    if extracted_text and len(extracted_text.strip()) >= 180:
        return False
    lower = html.lower()
    spa_markers = (
        'id="root"',
        'id="app"',
        'id="__next"',
        "enable javascript",
        "you need to enable javascript",
    )
    return any(marker in lower for marker in spa_markers)


def _extract_structured(
    html: str,
    base_url: str,
    fmt: str,
    css_selector: str | None,
    want_links: bool,
    want_images: bool,
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    title = soup.title.string.strip() if soup.title and soup.title.string else None
    meta_desc = None
    desc_tag = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    if desc_tag and desc_tag.get("content"):
        meta_desc = str(desc_tag["content"]).strip()

    target_html = html
    if css_selector:
        selected = soup.select(css_selector)
        target_html = "\n".join(str(el) for el in selected)

    doc = None
    if not css_selector:
        doc = bare_extraction(html, url=base_url, with_metadata=True)

    author = getattr(doc, "author", None) if doc else None
    date = getattr(doc, "date", None) if doc else None
    if doc and getattr(doc, "title", None):
        title = doc.title

    links = None
    if want_links:
        seen_links: set[str] = set()
        links = []
        for a in soup.find_all("a", href=True):
            abs_url = urljoin(base_url, a["href"].strip())
            if abs_url.startswith(("http://", "https://")) and abs_url not in seen_links:
                seen_links.add(abs_url)
                links.append({"text": a.get_text(" ", strip=True), "url": abs_url})

    images = None
    if want_images:
        seen_imgs: set[str] = set()
        images = []
        for img in soup.find_all("img", src=True):
            abs_src = urljoin(base_url, img["src"].strip())
            if abs_src not in seen_imgs:
                seen_imgs.add(abs_src)
                images.append({"alt": img.get("alt", "").strip(), "url": abs_src})

    if fmt == "html":
        content: Any = target_html
    elif fmt == "text":
        if doc and getattr(doc, "text", None) and not css_selector:
            content = doc.text
        else:
            scoped = BeautifulSoup(target_html, "lxml")
            for tag in scoped(["script", "style", "noscript"]):
                tag.decompose()
            content = scoped.get_text("\n", strip=True)
    elif fmt == "json":
        json_ld = []
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                json_ld.append(json.loads(script.string or "{}"))
            except Exception:
                pass
        headings = [
            {"level": h.name, "text": h.get_text(" ", strip=True)}
            for h in soup.find_all(["h1", "h2", "h3"])
            if h.get_text(strip=True)
        ]
        md_text = (
            trafilatura_extract(target_html, url=base_url, output_format="markdown", include_formatting=True)
            or md_convert(target_html, heading_style="ATX").strip()
        )
        content = {
            "title": title,
            "description": meta_desc,
            "author": author,
            "date": date,
            "headings": headings,
            "json_ld": json_ld,
            "markdown": md_text,
        }
    else:  # markdown
        if css_selector:
            content = md_convert(target_html, heading_style="ATX").strip()
        else:
            content = trafilatura_extract(
                html,
                url=base_url,
                output_format="markdown",
                with_metadata=True,
                include_formatting=True,
                include_links=True,
            )
            if not content:
                scoped = BeautifulSoup(html, "lxml")
                for tag in scoped(["script", "style", "noscript"]):
                    tag.decompose()
                content = md_convert(str(scoped), heading_style="ATX").strip()

    return {
        "title": title,
        "author": author,
        "date": date,
        "description": meta_desc,
        "content": content,
        "links": links,
        "images": images,
        "plain_text": getattr(doc, "text", None) if doc else None,
    }


class WebScraperEngine:
    """Multi-tier web scraping and crawling engine."""

    def __init__(
        self,
        browser_pool: PlaywrightPool,
        proxy_manager: ProxyManager,
        rate_limiter: DomainRateLimiter,
        timeout_seconds: int = 30,
        allow_private_urls: bool = False,
        public_mode: bool = False,
        browser_enabled: bool = True,
        web_max_mb: int = 5,
    ) -> None:
        self.browser_pool = browser_pool
        self.proxy_manager = proxy_manager
        self.rate_limiter = rate_limiter
        self.timeout = timeout_seconds
        self.allow_private_urls = allow_private_urls
        self.public_mode = public_mode
        self.browser_enabled = browser_enabled
        self.max_bytes = web_max_mb * 1024 * 1024

    async def _fetch_http(self, url: str, headers: dict[str, str], proxy_url: str | None) -> tuple[int, str, str]:
        if HAS_CURL_CFFI and not self.public_mode:
            proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
            async with CurlAsyncSession(impersonate="chrome", timeout=self.timeout) as session:
                for _ in range(11):
                    url = await asyncio.to_thread(validate_target_url, url, self.allow_private_urls)
                    async with session.stream(
                        "GET", url, headers=headers, proxies=proxies, allow_redirects=False
                    ) as resp:
                        if resp.status_code in {301, 302, 303, 307, 308} and resp.headers.get("location"):
                            target = urljoin(str(resp.url), resp.headers["location"])
                            if (urlparse(target).scheme, urlparse(target).netloc) != (
                                urlparse(url).scheme,
                                urlparse(url).netloc,
                            ):
                                headers = {
                                    k: v
                                    for k, v in headers.items()
                                    if k.lower() not in {"authorization", "cookie", "proxy-authorization"}
                                }
                            url = target
                            continue
                        body = bytearray()
                        async for chunk in resp.aiter_content():
                            body.extend(chunk)
                            if len(body) > self.max_bytes:
                                raise ValueError("Response exceeds the configured download size limit.")
                        return resp.status_code, str(resp.url), body.decode(resp.encoding or "utf-8", errors="replace")
                raise ValueError("Too many redirects.")

        resp = await download_url(
            url,
            max_bytes=self.max_bytes,
            timeout=self.timeout,
            allow_private=self.allow_private_urls,
            headers=headers,
            proxy=proxy_url,
        )
        return resp.status_code, str(resp.url), resp.text

    async def _fetch_playwright(
        self,
        url: str,
        headers: dict[str, str],
        proxy_url: str | None,
        wait_ms: int,
        wait_for: str | None,
        screenshot: bool,
    ) -> tuple[int, str, str, str | None]:
        if not self.browser_enabled:
            raise ValueError(
                "Browser rendering is disabled on this instance. Use engine=http or run locally with BROWSER_ENABLED=true."
            )
        browser = await self.browser_pool.get_browser()
        context_kwargs: dict[str, Any] = {
            "user_agent": headers.get("User-Agent", DEFAULT_HEADERS["User-Agent"]),
            "extra_http_headers": {k: v for k, v in headers.items() if k.lower() != "user-agent"},
            "service_workers": "block",
            "accept_downloads": False,
        }
        if proxy_url:
            context_kwargs["proxy"] = {"server": proxy_url}

        context = await browser.new_context(**context_kwargs)
        try:

            async def guard_route(route):
                if route.request.url.startswith("data:"):
                    await route.continue_()
                    return
                try:
                    await asyncio.to_thread(validate_target_url, route.request.url, self.allow_private_urls)
                except ValueError:
                    await route.abort("blockedbyclient")
                    return
                await route.continue_()

            await context.route("**/*", guard_route)
            page = await context.new_page()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            if wait_for:
                await page.wait_for_selector(wait_for, timeout=self.timeout * 1000)
            else:
                try:
                    await page.wait_for_load_state("networkidle", timeout=min(self.timeout * 1000, 8000))
                except Exception:
                    pass
            if wait_ms > 0:
                await page.wait_for_timeout(wait_ms)

            html = await page.content()
            if len(html.encode("utf-8")) > self.max_bytes:
                raise ValueError("Page exceeds the configured download size limit.")
            final_url = page.url
            status = response.status if response else 200
            shot_b64 = None
            if screenshot:
                png = await page.screenshot(full_page=True)
                shot_b64 = base64.b64encode(png).decode("ascii")
            return status, final_url, html, shot_b64
        finally:
            await context.close()

    async def _fetch_crawl4ai(self, url: str) -> tuple[int, str, str]:
        try:
            from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig
            from crawl4ai.content_filter_strategy import PruningContentFilterLXML
            from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
        except ImportError as exc:
            raise RuntimeError("crawl4ai is not installed. Install optional extra with `pip install .[pro]`.") from exc

        md_gen = DefaultMarkdownGenerator(
            content_filter=PruningContentFilterLXML(threshold=0.48, threshold_type="fixed")
        )
        cfg = CrawlerRunConfig(markdown_generator=md_gen, cache_mode=CacheMode.BYPASS)
        async with AsyncWebCrawler() as crawler:
            res = await crawler.arun(url=url, config=cfg)
            if not res.success:
                raise RuntimeError(res.error_message or "Crawl4AI failed")
            return res.status_code or 200, res.url or url, res.html or ""

    async def _fetch_scrapling(self, url: str, dynamic: bool) -> tuple[int, str, str]:
        try:
            from scrapling.fetchers import DynamicFetcher, Fetcher
        except ImportError as exc:
            raise RuntimeError("scrapling is not installed. Install optional extra with `pip install .[pro]`.") from exc

        if dynamic:
            page = await asyncio.to_thread(DynamicFetcher.fetch, url, headless=True, network_idle=True)
        else:
            page = await asyncio.to_thread(Fetcher.get, url)
        html = page.html_content if hasattr(page, "html_content") else str(page.body)
        return getattr(page, "status", 200), getattr(page, "url", url), html

    async def scrape(self, req: ScrapeRequest) -> dict[str, Any]:
        t0 = time.perf_counter()
        url = await asyncio.to_thread(validate_target_url, req.url, self.allow_private_urls)
        if self.public_mode and (req.proxy or req.engine in {"crawl4ai", "scrapling"}):
            raise ValueError("Custom proxies and optional adapters are disabled in public mode.")
        await self.rate_limiter.acquire(url)

        headers = {**DEFAULT_HEADERS, **(req.custom_headers or {})}
        proxy_url = self.proxy_manager.acquire(
            country=req.country,
            premium=req.premium,
            explicit_proxy=req.proxy,
        )

        engine_used = req.engine
        shot_b64: str | None = None

        try:
            if req.engine == "crawl4ai":
                status, final_url, html = await self._fetch_crawl4ai(url)
            elif req.engine == "scrapling":
                status, final_url, html = await self._fetch_scrapling(url, req.dynamic)
            elif req.engine == "playwright" or req.dynamic or req.screenshot or req.wait_for:
                engine_used = "playwright"
                status, final_url, html, shot_b64 = await self._fetch_playwright(
                    url, headers, proxy_url, req.wait, req.wait_for, req.screenshot
                )
            elif req.engine == "http":
                engine_used = "curl_cffi" if HAS_CURL_CFFI and not self.public_mode else "httpx"
                status, final_url, html = await self._fetch_http(url, headers, proxy_url)
            else:
                # Auto mode: Tier 1 fast HTTP -> Tier 2 Playwright if SPA shell or blocked
                engine_used = "curl_cffi" if HAS_CURL_CFFI and not self.public_mode else "httpx"
                status, final_url, html = await self._fetch_http(url, headers, proxy_url)
                if status in (403, 429) or _looks_js_dependent(html, None):
                    if HAS_PLAYWRIGHT and self.browser_enabled:
                        engine_used = "playwright(auto-fallback)"
                        status, final_url, html, shot_b64 = await self._fetch_playwright(
                            url, headers, proxy_url, req.wait, req.wait_for, req.screenshot
                        )

            self.proxy_manager.report_success(proxy_url)
        except Exception:
            self.proxy_manager.report_failure(proxy_url)
            raise

        extracted = _extract_structured(
            html=html,
            base_url=final_url or url,
            fmt=req.format,
            css_selector=req.css_selector,
            want_links=req.extract_links,
            want_images=req.extract_images,
        )

        # Auto-fallback if Tier 1 HTTP succeeded with 200 but yielded an empty JS shell
        if (
            req.engine == "auto"
            and not engine_used.startswith("playwright")
            and HAS_PLAYWRIGHT
            and self.browser_enabled
            and _looks_js_dependent(html, extracted.get("plain_text"))
        ):
            engine_used = "playwright(spa-fallback)"
            status, final_url, html, shot_b64 = await self._fetch_playwright(
                url, headers, proxy_url, req.wait, req.wait_for, req.screenshot
            )
            extracted = _extract_structured(
                html=html,
                base_url=final_url or url,
                fmt=req.format,
                css_selector=req.css_selector,
                want_links=req.extract_links,
                want_images=req.extract_images,
            )

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "url": url,
            "final_url": final_url,
            "status_code": status,
            "engine": engine_used,
            "cached": False,
            "proxy_used": urlparse(proxy_url).hostname if proxy_url else None,
            "title": extracted["title"],
            "author": extracted["author"],
            "date": extracted["date"],
            "description": extracted["description"],
            "format": req.format,
            "content": extracted["content"],
            "links": extracted["links"],
            "images": extracted["images"],
            "screenshot_base64": shot_b64,
            "elapsed_ms": elapsed_ms,
        }

    async def crawl(self, req: CrawlRequest, hard_max_pages: int = 100) -> dict[str, Any]:
        seed_url = await asyncio.to_thread(validate_target_url, req.url, self.allow_private_urls)
        seed_host = (urlparse(seed_url).hostname or "").lower()
        max_pages = min(req.max_pages, hard_max_pages)

        include_res = [re.compile(p) for p in req.include_patterns]
        exclude_res = [re.compile(p) for p in req.exclude_patterns]

        def _allowed(candidate: str) -> bool:
            parsed = urlparse(candidate)
            if parsed.scheme not in {"http", "https"}:
                return False
            if req.same_domain and (parsed.hostname or "").lower() != seed_host:
                return False
            if include_res and not any(rx.search(candidate) for rx in include_res):
                return False
            if any(rx.search(candidate) for rx in exclude_res):
                return False
            return True

        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(seed_url, 0)])
        pages: list[dict[str, Any]] = []
        sem = asyncio.Semaphore(req.concurrency)

        while queue and len(visited) < max_pages:
            batch: list[tuple[str, int]] = []
            while queue and len(visited) < max_pages and len(batch) < req.concurrency:
                u, depth = queue.popleft()
                norm = u.split("#", 1)[0]
                if norm in visited:
                    continue
                visited.add(norm)
                batch.append((norm, depth))

            if not batch:
                break

            async def _worker(target: str, depth: int) -> tuple[int, dict[str, Any] | None]:
                async with sem:
                    try:
                        res = await self.scrape(
                            ScrapeRequest(
                                url=target,
                                dynamic=req.dynamic,
                                format=req.format,
                                country=req.country,
                                extract_links=depth < req.max_depth,
                            )
                        )
                        return depth, res
                    except Exception as exc:
                        return depth, {"url": target, "error": str(exc)}

            results = await asyncio.gather(*(_worker(u, d) for u, d in batch))
            for depth, res in results:
                if not res:
                    continue
                pages.append(
                    {
                        "url": res.get("url"),
                        "depth": depth,
                        "status_code": res.get("status_code"),
                        "title": res.get("title"),
                        "content": res.get("content"),
                        "error": res.get("error"),
                    }
                )
                if depth < req.max_depth and res.get("links"):
                    for link in res["links"]:
                        next_url = link["url"].split("#", 1)[0]
                        if next_url not in visited and _allowed(next_url):
                            queue.append((next_url, depth + 1))

        return {
            "seed_url": seed_url,
            "pages_crawled": len(pages),
            "pages": pages,
        }
