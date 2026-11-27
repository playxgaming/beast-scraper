"""Regression coverage for the API's public deployment boundaries."""

import asyncio
import socket

import httpx
import pymupdf
import pytest
from fastapi.testclient import TestClient

from beast_scraper.core.api_limits import APILimitsMiddleware
from beast_scraper.core.http import download_url
from beast_scraper.core.proxy_manager import ProxyManager
from beast_scraper.core.rate_limit import DomainRateLimiter, validate_target_url
from beast_scraper.engines.papers_engine import PapersEngine
from beast_scraper.engines.web_engine import PlaywrightPool, WebScraperEngine
from beast_scraper.engines.youtube_engine import extract_video_id, validate_youtube_url
from beast_scraper.main import app, state
from beast_scraper.schemas import CrawlRequest, ScrapeRequest


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "100.64.0.1", "::1", "fc00::1"])
def test_dns_private_targets_blocked(monkeypatch, ip):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a: [(socket.AF_INET, 1, 6, "", (ip, 80))])
    with pytest.raises(ValueError, match="non-public"):
        validate_target_url("https://attacker.example")


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "ftp://example.com", "https://u:p@example.com", "http://example.com:wrong"]
)
def test_invalid_targets_rejected(url):
    with pytest.raises(ValueError):
        validate_target_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/a.pdf",
        "https://evil.example/?v=dQw4w9WgXcQ",
        "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ",
    ],
)
def test_youtube_cannot_fetch_arbitrary_urls(url):
    with pytest.raises(ValueError):
        validate_youtube_url(url)
    with pytest.raises(ValueError):
        extract_video_id(url)


def test_anonymous_admin_disabled_and_health_redacted():
    with TestClient(app) as client:
        assert client.delete("/admin/cache").status_code == 403
        assert client.post("/admin/proxies", json={"proxies": ["http://example.com:80"]}).status_code == 403
        assert "proxies" not in client.get("/health").json()
        assert "BeastScraper API" in client.get("/").text


def test_admin_key_separate_from_api_key(monkeypatch):
    monkeypatch.setenv("API_KEY", "reader")
    monkeypatch.setenv("ADMIN_API_KEY", "administrator")
    with TestClient(app) as client:
        assert client.get("/admin/proxies", headers={"X-API-Key": "reader"}).status_code == 401
        assert client.get("/admin/proxies", headers={"X-API-Key": "administrator"}).status_code == 200


def test_error_does_not_disclose_proxy_credentials(monkeypatch):
    with TestClient(app) as client:

        async def fail(**kwargs):
            raise RuntimeError("http://user:private-password@proxy.example")

        monkeypatch.setattr(state.search, "search", fail)
        response = client.get("/search", params={"query": "test"})
        assert response.status_code == 502
        assert "private-password" not in response.text


def test_custom_headers_not_written_to_shared_cache(monkeypatch):
    with TestClient(app) as client:
        count = 0

        async def scrape(req):
            nonlocal count
            count += 1
            return {"content": "private account", "cached": False}

        monkeypatch.setattr(state.web, "scrape", scrape)
        payload = {"url": "https://example.com", "custom_headers": {"Authorization": "Bearer private"}}
        for _ in range(2):
            assert client.post("/scrape", json=payload).status_code == 200
        assert count == 2


@pytest.mark.asyncio
async def test_redirect_private_target_blocked(monkeypatch):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a: [(2, 1, 6, "", ("93.184.216.34", 80))])
    with pytest.raises(ValueError, match="private/local"):
        await download_url("https://public.example", max_bytes=1000)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_download_limit_without_content_length(monkeypatch):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"a" * 11))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=transport))
    with pytest.raises(ValueError, match="size limit"):
        await download_url("https://example.com", max_bytes=10, allow_private=True)


@pytest.mark.asyncio
async def test_pdf_rejects_private_download():
    with pytest.raises(ValueError, match="private/local"):
        await PapersEngine().resolve_and_parse("http://127.0.0.1/private.pdf")


@pytest.mark.asyncio
async def test_pdf_max_pages_clamped_to_document(monkeypatch):
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "One page paper")
        pdf = doc.tobytes()

    async def fake_download(url, **kwargs):
        return httpx.Response(200, content=pdf, request=httpx.Request("GET", url))

    monkeypatch.setattr("beast_scraper.engines.papers_engine.download_url", fake_download)
    result = await PapersEngine().resolve_and_parse("https://example.com/paper.pdf", max_pages=5)
    assert "One page paper" in result["markdown"]


@pytest.mark.asyncio
async def test_crawl_uses_full_page_budget(monkeypatch):
    engine = WebScraperEngine(PlaywrightPool(), ProxyManager(), DomainRateLimiter(0), allow_private_urls=True)

    async def scrape(req):
        return {"url": req.url, "content": "ok", "links": [{"url": f"https://example.com/{i}"} for i in range(6)]}

    monkeypatch.setattr(engine, "scrape", scrape)
    result = await engine.crawl(CrawlRequest(url="https://example.com", max_pages=5, max_depth=1, concurrency=4))
    assert result["pages_crawled"] == 5


@pytest.mark.asyncio
async def test_public_mode_blocks_proxy_override_and_browser():
    engine = WebScraperEngine(
        PlaywrightPool(),
        ProxyManager(),
        DomainRateLimiter(0),
        allow_private_urls=True,
        public_mode=True,
        browser_enabled=False,
    )
    with pytest.raises(ValueError, match="Custom proxies"):
        await engine.scrape(ScrapeRequest(url="https://example.com", proxy="http://127.0.0.1:9000"))
    with pytest.raises(ValueError, match="Browser rendering"):
        await engine.scrape(ScrapeRequest(url="https://example.com", dynamic=True))


def test_proxy_replacement_is_atomic():
    manager = ProxyManager("http://old.example:80")
    with pytest.raises(ValueError):
        manager.add_many(["http://new.example:80", "invalid"], replace=True)
    assert manager.acquire() == "http://old.example:80"


@pytest.mark.asyncio
async def test_limits_bound_concurrency_rate_and_chunked_body(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(scope, receive, send):
        entered.set()
        await release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    monkeypatch.setenv("MAX_CONCURRENT_REQUESTS", "1")
    monkeypatch.setenv("API_REQUESTS_PER_MINUTE", "2")
    middleware = APILimitsMiddleware(handler)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=middleware), base_url="http://test") as client:
        first = asyncio.create_task(client.get("/scrape"))
        await entered.wait()
        assert (await client.get("/scrape")).status_code == 429
        release.set()
        assert (await first).status_code == 200
        assert (await client.post("/scrape", content=b"x" * 65537)).status_code == 413
        assert (await client.get("/scrape")).status_code == 429
