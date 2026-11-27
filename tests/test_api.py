"""FastAPI endpoint tests using TestClient with mocked engine calls."""

from fastapi.testclient import TestClient

from beast_scraper.main import app, state


def test_health_and_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DB_PATH", str(tmp_path / "api_cache.db"))
    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setenv("ADMIN_API_KEY", "admin-test")
    from beast_scraper.config import get_settings

    get_settings.cache_clear()

    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

        async def fake_scrape(req):
            return {
                "url": req.url,
                "final_url": req.url,
                "status_code": 200,
                "engine": "mock",
                "cached": False,
                "proxy_used": None,
                "title": "Mock Title",
                "author": None,
                "date": None,
                "description": None,
                "format": req.format,
                "content": "# Mock Markdown",
                "links": None,
                "images": None,
                "screenshot_base64": None,
                "elapsed_ms": 5,
            }

        monkeypatch.setattr(state.web, "scrape", fake_scrape)

        r1 = client.get("/scrape", params={"url": "https://example.com"})
        assert r1.status_code == 200
        assert r1.json()["cached"] is False
        assert r1.json()["content"] == "# Mock Markdown"

        # Second call hits SQLite cache
        r2 = client.get("/scrape", params={"url": "https://example.com"})
        assert r2.status_code == 200
        assert r2.json()["cached"] is True

        # Admin proxy registration
        p = client.post(
            "/admin/proxies", json={"proxies": ["us|http://u:p@1.2.3.4:8000"]}, headers={"X-API-Key": "admin-test"}
        )
        assert p.status_code == 200
        assert p.json()["added"] == 1

        # Mocked search (/google alias)
        async def fake_search(**kwargs):
            return {
                "query": kwargs["query"],
                "provider": "mock",
                "count": 1,
                "organic_results": [{"position": 1, "title": "Hit", "link": "https://example.com", "snippet": "Desc"}],
            }

        monkeypatch.setattr(state.search, "search", fake_search)
        sr = client.get("/google", params={"query": "python"})
        assert sr.status_code == 200
        assert sr.json()["organic_results"][0]["title"] == "Hit"

        # Mocked papers search
        async def fake_papers(query, limit):
            return {"query": query, "count": 1, "papers": [{"title": "Paper 1"}]}

        monkeypatch.setattr(state.papers, "search", fake_papers)
        pr = client.get("/papers/search", params={"query": "rag"})
        assert pr.status_code == 200
        assert pr.json()["papers"][0]["title"] == "Paper 1"

        # Mocked YouTube transcript
        async def fake_transcript(url, languages, fmt):
            return {"video_id": "dQw4w9WgXcQ", "text": "hello", "formatted": "- `[00:00]` hello"}

        monkeypatch.setattr(state.youtube, "transcript", fake_transcript)
        yr = client.get("/youtube/transcript", params={"url": "dQw4w9WgXcQ", "format": "markdown"})
        assert yr.status_code == 200
        assert yr.json()["video_id"] == "dQw4w9WgXcQ"


def test_api_key_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DB_PATH", str(tmp_path / "auth_cache.db"))
    monkeypatch.setenv("API_KEY", "secret-123")
    from beast_scraper.config import get_settings

    get_settings.cache_clear()

    with TestClient(app) as client:
        assert client.get("/search", params={"query": "test"}).status_code == 401
        assert client.get("/search", params={"query": "test", "api_key": "wrong"}).status_code == 401

        async def fake_search(**kwargs):
            return {"query": kwargs["query"], "provider": "mock", "count": 0, "organic_results": []}

        monkeypatch.setattr(state.search, "search", fake_search)
        assert client.get("/search", params={"query": "test", "api_key": "secret-123"}).status_code == 200
        assert client.get("/search", params={"query": "test"}, headers={"X-API-Key": "secret-123"}).status_code == 200
