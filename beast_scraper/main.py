"""FastAPI application entrypoint for BeastScraper."""

import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from beast_scraper.config import Settings, get_settings
from beast_scraper.core.api_limits import APILimitsMiddleware
from beast_scraper.core.cache import AsyncSQLiteCache
from beast_scraper.core.proxy_manager import ProxyManager
from beast_scraper.core.rate_limit import DomainRateLimiter
from beast_scraper.engines.papers_engine import PapersEngine
from beast_scraper.engines.search_engine import SearchEngine
from beast_scraper.engines.web_engine import (
    HAS_CURL_CFFI,
    HAS_PLAYWRIGHT,
    PlaywrightPool,
    WebScraperEngine,
)
from beast_scraper.engines.youtube_engine import YouTubeEngine
from beast_scraper.schemas import (
    CrawlRequest,
    PaperParseRequest,
    ProxyAddRequest,
    ScrapeEngine,
    ScrapeFormat,
    ScrapeRequest,
)

STATIC_INDEX = Path(__file__).parent / "static" / "index.html"


class AppState:
    settings: Settings
    cache: AsyncSQLiteCache
    proxy_manager: ProxyManager
    rate_limiter: DomainRateLimiter
    browser_pool: PlaywrightPool
    web: WebScraperEngine
    papers: PapersEngine
    youtube: YouTubeEngine
    search: SearchEngine


state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.public_mode and settings.allow_private_urls:
        raise RuntimeError("PUBLIC_MODE requires ALLOW_PRIVATE_URLS=false.")
    state.settings = settings
    state.cache = AsyncSQLiteCache(settings.cache_db_path, settings.cache_ttl_seconds)
    await state.cache.init()

    state.proxy_manager = ProxyManager(
        proxies_raw=settings.proxies,
        proxy_template=settings.proxy_template,
        proxy_file=settings.proxy_file,
        premium_only=settings.proxies_premium_only,
    )
    state.rate_limiter = DomainRateLimiter(settings.rate_limit_per_domain_rpm)
    state.browser_pool = PlaywrightPool(headless=settings.playwright_headless)
    state.web = WebScraperEngine(
        browser_pool=state.browser_pool,
        proxy_manager=state.proxy_manager,
        rate_limiter=state.rate_limiter,
        timeout_seconds=settings.request_timeout_seconds,
        allow_private_urls=settings.allow_private_urls,
        public_mode=settings.public_mode,
        browser_enabled=settings.browser_enabled,
        web_max_mb=settings.web_max_mb,
    )
    state.papers = PapersEngine(
        pdf_max_mb=settings.pdf_max_mb,
        allow_private_urls=settings.allow_private_urls,
        openalex_api_key=settings.openalex_api_key,
        pdf_layout_enabled=settings.pdf_layout_enabled,
    )
    state.youtube = YouTubeEngine(proxy_manager=state.proxy_manager)
    state.search = SearchEngine(
        proxy_manager=state.proxy_manager,
        searxng_url=settings.searxng_url,
    )

    try:
        yield
    finally:
        try:
            await state.browser_pool.close()
        finally:
            await state.cache.close()


app = FastAPI(
    title="BeastScraper API",
    version="0.1.0",
    description="Self-hosted scraping API for Web, Crawling, Search, Research Papers, and YouTube.",
    lifespan=lifespan,
)
app.add_middleware(APILimitsMiddleware)


async def verify_api_key(
    api_key: str | None = Query(None, description="API key (Scrapingdog-compatible query param)"),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> None:
    expected = state.settings.api_key.strip()
    if not expected:
        return
    provided = x_api_key or api_key
    if not provided or not secrets.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


async def verify_admin_key(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> None:
    expected = (state.settings.admin_api_key or state.settings.api_key).strip()
    if not expected:
        raise HTTPException(
            status_code=403, detail="Admin endpoints require ADMIN_API_KEY or API_KEY to be configured."
        )
    if not x_api_key or not secrets.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing admin key.")


@app.get("/", include_in_schema=False)
async def home():
    if STATIC_INDEX.exists():
        return FileResponse(STATIC_INDEX)
    return JSONResponse({"name": "BeastScraper", "docs": "/docs"})


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "capabilities": {
            "curl_cffi": HAS_CURL_CFFI,
            "playwright": HAS_PLAYWRIGHT and state.settings.browser_enabled,
            "searxng_configured": bool(state.settings.searxng_url),
            "proxies_configured": state.proxy_manager.has_proxies(),
        },
        "auth_required": bool(state.settings.api_key),
    }


async def _run_scrape(req: ScrapeRequest) -> dict[str, Any]:
    cache_params = req.model_dump(exclude={"bypass_cache", "proxy"})
    cacheable = not req.proxy and not req.custom_headers
    if not req.bypass_cache and cacheable:
        cached = await state.cache.get("scrape", cache_params)
        if cached is not None:
            cached["cached"] = True
            return cached
    try:
        result = await state.web.scrape(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="Scrape failed. The upstream service may be unavailable or rate limited."
        ) from exc

    if cacheable:
        await state.cache.set("scrape", cache_params, result)
    return result


@app.get("/scrape", dependencies=[Depends(verify_api_key)])
async def scrape_get(
    url: str = Query(..., description="Target webpage URL"),
    dynamic: bool = Query(False),
    engine: ScrapeEngine = Query("auto"),
    format: ScrapeFormat = Query("markdown"),
    wait: int = Query(0, ge=0, le=35000),
    wait_for: str | None = Query(None),
    css_selector: str | None = Query(None),
    country: str | None = Query(None),
    premium: bool = Query(False),
    proxy: str | None = Query(None),
    extract_links: bool = Query(False),
    extract_images: bool = Query(False),
    screenshot: bool = Query(False),
    bypass_cache: bool = Query(False),
) -> dict[str, Any]:
    req = ScrapeRequest(
        url=url,
        dynamic=dynamic,
        engine=engine,
        format=format,
        wait=wait,
        wait_for=wait_for,
        css_selector=css_selector,
        country=country,
        premium=premium,
        proxy=proxy,
        extract_links=extract_links,
        extract_images=extract_images,
        screenshot=screenshot,
        bypass_cache=bypass_cache,
    )
    return await _run_scrape(req)


@app.post("/scrape", dependencies=[Depends(verify_api_key)])
async def scrape_post(req: ScrapeRequest) -> dict[str, Any]:
    return await _run_scrape(req)


@app.post("/crawl", dependencies=[Depends(verify_api_key)])
async def crawl_post(req: CrawlRequest) -> dict[str, Any]:
    try:
        return await state.web.crawl(req, hard_max_pages=state.settings.max_crawl_pages)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="Crawl failed. The upstream service may be unavailable or rate limited."
        ) from exc


@app.get("/google", dependencies=[Depends(verify_api_key)])
@app.get("/search", dependencies=[Depends(verify_api_key)])
async def search_get(
    query: str = Query(..., description="Search query string"),
    results: int = Query(10, ge=1, le=50),
    search_type: str = Query("web", pattern="^(web|news)$"),
    region: str = Query("wt-wt"),
    timelimit: str | None = Query(None, pattern="^(d|w|m|y)$"),
    country: str | None = Query(None),
    bypass_cache: bool = Query(False),
) -> dict[str, Any]:
    params = {
        "query": query,
        "results": results,
        "search_type": search_type,
        "region": region,
        "timelimit": timelimit,
        "country": country,
    }
    if not bypass_cache:
        cached = await state.cache.get("search", params)
        if cached is not None:
            cached["cached"] = True
            return cached
    try:
        data = await state.search.search(
            query=query,
            max_results=results,
            search_type=search_type,
            region=region,
            timelimit=timelimit,
            country=country,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="Search failed. The upstream service may be unavailable or rate limited."
        ) from exc

    data["cached"] = False
    await state.cache.set("search", params, data)
    return data


@app.get("/papers/search", dependencies=[Depends(verify_api_key)])
async def papers_search(
    query: str = Query(..., description="Research topic, title, or author"),
    limit: int = Query(10, ge=1, le=25),
    bypass_cache: bool = Query(False),
) -> dict[str, Any]:
    params = {"query": query, "limit": limit}
    if not bypass_cache:
        cached = await state.cache.get("papers_search", params)
        if cached is not None:
            cached["cached"] = True
            return cached
    try:
        data = await state.papers.search(query=query, limit=limit)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="Paper search failed. The upstream service may be unavailable or rate limited."
        ) from exc

    data["cached"] = False
    await state.cache.set("papers_search", params, data)
    return data


async def _parse_paper(req: PaperParseRequest) -> dict[str, Any]:
    params = req.model_dump(exclude={"bypass_cache"})
    if not req.bypass_cache:
        cached = await state.cache.get("papers_parse", params)
        if cached is not None:
            cached["cached"] = True
            return cached
    try:
        data = await state.papers.resolve_and_parse(
            identifier=req.identifier,
            page_chunks=req.page_chunks,
            max_pages=req.max_pages,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="Paper parse failed. The upstream service may be unavailable or rate limited."
        ) from exc

    data["cached"] = False
    await state.cache.set("papers_parse", params, data)
    return data


@app.get("/papers/parse", dependencies=[Depends(verify_api_key)])
async def papers_parse_get(
    identifier: str = Query(..., description="arXiv ID, DOI, or PDF URL"),
    page_chunks: bool = Query(False),
    max_pages: int | None = Query(None, ge=1, le=300),
    bypass_cache: bool = Query(False),
) -> dict[str, Any]:
    return await _parse_paper(
        PaperParseRequest(
            identifier=identifier,
            page_chunks=page_chunks,
            max_pages=max_pages,
            bypass_cache=bypass_cache,
        )
    )


@app.post("/papers/parse", dependencies=[Depends(verify_api_key)])
async def papers_parse_post(req: PaperParseRequest) -> dict[str, Any]:
    return await _parse_paper(req)


@app.get("/youtube/info", dependencies=[Depends(verify_api_key)])
async def yt_info(
    url: str = Query(..., description="YouTube video/playlist URL or 11-char ID"),
    country: str | None = Query(None),
    bypass_cache: bool = Query(False),
) -> dict[str, Any]:
    params = {"url": url, "country": country}
    if not bypass_cache:
        cached = await state.cache.get("yt_info", params)
        if cached is not None:
            cached["cached"] = True
            return cached
    try:
        data = await state.youtube.info(url, country=country)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="YouTube info failed. The upstream service may be unavailable or rate limited."
        ) from exc
    data["cached"] = False
    await state.cache.set("yt_info", params, data)
    return data


@app.get("/youtube/transcript", dependencies=[Depends(verify_api_key)])
async def yt_transcript(
    url: str = Query(..., description="YouTube video URL or ID"),
    lang: str = Query("en", description="Comma-separated preferred languages, e.g. en,hi"),
    format: str = Query("json", pattern="^(json|text|markdown|srt)$"),
    bypass_cache: bool = Query(False),
) -> dict[str, Any]:
    langs = [x.strip() for x in lang.split(",") if x.strip()] or ["en"]
    params = {"url": url, "langs": langs, "format": format}
    if not bypass_cache:
        cached = await state.cache.get("yt_transcript", params)
        if cached is not None:
            cached["cached"] = True
            return cached
    try:
        data = await state.youtube.transcript(url, languages=langs, fmt=format)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="Transcript fetch failed. The upstream service may be unavailable or rate limited."
        ) from exc
    data["cached"] = False
    await state.cache.set("yt_transcript", params, data)
    return data


@app.get("/youtube/search", dependencies=[Depends(verify_api_key)])
async def yt_search(
    query: str = Query(...),
    limit: int = Query(10, ge=1, le=25),
    country: str | None = Query(None),
    bypass_cache: bool = Query(False),
) -> dict[str, Any]:
    params = {"query": query, "limit": limit, "country": country}
    if not bypass_cache:
        cached = await state.cache.get("yt_search", params)
        if cached is not None:
            cached["cached"] = True
            return cached
    try:
        data = await state.youtube.search(query=query, limit=limit, country=country)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="YouTube search failed. The upstream service may be unavailable or rate limited."
        ) from exc
    data["cached"] = False
    await state.cache.set("yt_search", params, data)
    return data


@app.get("/admin/proxies", dependencies=[Depends(verify_admin_key)])
async def get_proxies() -> dict[str, Any]:
    return state.proxy_manager.status()


@app.post("/admin/proxies", dependencies=[Depends(verify_admin_key)])
async def add_proxies(req: ProxyAddRequest) -> dict[str, Any]:
    try:
        added = state.proxy_manager.add_many(req.proxies, replace=req.replace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"added": added, "status": state.proxy_manager.status()}


@app.delete("/admin/cache", dependencies=[Depends(verify_admin_key)])
async def clear_cache(namespace: str | None = Query(None)) -> dict[str, int]:
    deleted = await state.cache.clear(namespace=namespace)
    return {"deleted": deleted}


def run() -> None:
    settings = get_settings()
    uvicorn.run("beast_scraper.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
