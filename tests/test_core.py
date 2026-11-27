"""Offline unit tests for core utilities and parser helpers."""

import pytest

from beast_scraper.core.cache import AsyncSQLiteCache
from beast_scraper.core.proxy_manager import ProxyManager
from beast_scraper.core.rate_limit import validate_target_url
from beast_scraper.engines.papers_engine import build_bibtex, undo_inverted_index
from beast_scraper.engines.web_engine import _extract_structured
from beast_scraper.engines.youtube_engine import extract_video_id


def test_undo_inverted_index():
    inv = {"Attention": [0], "Is": [1], "All": [2], "You": [3], "Need": [4]}
    assert undo_inverted_index(inv) == "Attention Is All You Need"
    assert undo_inverted_index(None) is None


def test_build_bibtex():
    bib = build_bibtex(
        {
            "title": "Attention Is All You Need",
            "authors": ["Ashish Vaswani", "Noam Shazeer"],
            "year": 2017,
            "arxiv_id": "1706.03762",
        }
    )
    assert "@article{vaswani2017," in bib
    assert "eprint={1706.03762}" in bib


def test_extract_video_id():
    assert extract_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10") == "dQw4w9WgXcQ"
    assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    with pytest.raises(ValueError):
        extract_video_id("https://example.com/not-youtube")


def test_proxy_manager_geo_and_cooldown():
    pm = ProxyManager(
        proxies_raw="us|http://u:p@1.1.1.1:8000,in|http://u:p@2.2.2.2:8000",
        proxy_template="http://u-{country}-{session}:p@gate.example.com:7777",
    )
    assert pm.acquire(country="in") == "http://u:p@2.2.2.2:8000"
    assert pm.acquire(country="us") == "http://u:p@1.1.1.1:8000"

    # Trigger cooldown on US proxy -> falls back to template
    pm.report_failure("http://u:p@1.1.1.1:8000")
    pm.report_failure("http://u:p@1.1.1.1:8000")
    fallback = pm.acquire(country="us")
    assert fallback is not None and "u-us-" in fallback


def test_validate_target_url_blocks_private():
    assert validate_target_url("https://example.com/page") == "https://example.com/page"
    with pytest.raises(ValueError):
        validate_target_url("http://127.0.0.1:8000/admin")
    assert validate_target_url("http://127.0.0.1:8000/admin", allow_private=True).startswith("http://127.0.0.1")


def test_extract_structured_html():
    html = """
    <html>
      <head>
        <title>Sample Article</title>
        <meta name="description" content="A test description">
      </head>
      <body>
        <article>
          <h1>Main Heading</h1>
          <p>Hello world paragraph with enough text to extract cleanly.</p>
          <a href="/docs">Docs</a>
          <img src="/logo.png" alt="Logo">
        </article>
      </body>
    </html>
    """
    res = _extract_structured(
        html=html,
        base_url="https://example.com/post",
        fmt="json",
        css_selector=None,
        want_links=True,
        want_images=True,
    )
    assert res["title"] == "Main Heading"
    assert res["description"] == "A test description"
    assert res["links"] == [{"text": "Docs", "url": "https://example.com/docs"}]
    assert res["images"] == [{"alt": "Logo", "url": "https://example.com/logo.png"}]
    assert res["content"]["headings"][0]["text"] == "Main Heading"


@pytest.mark.asyncio
async def test_sqlite_cache_roundtrip(tmp_path):
    cache = AsyncSQLiteCache(tmp_path / "test_cache.db", default_ttl=60)
    await cache.init()
    try:
        params = {"url": "https://example.com"}
        assert await cache.get("scrape", params) is None
        await cache.set("scrape", params, {"status_code": 200})
        hit = await cache.get("scrape", params)
        assert hit == {"status_code": 200}
        assert (await cache.stats())["active_entries"] == 1
        assert await cache.clear("scrape") == 1
    finally:
        await cache.close()
