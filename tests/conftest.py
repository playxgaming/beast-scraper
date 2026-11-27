"""Keep tests independent from developer .env settings and external services."""

import pytest

from beast_scraper.config import get_settings


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    for key, value in {
        "CACHE_DB_PATH": str(tmp_path / "cache.db"),
        "API_KEY": "",
        "ADMIN_API_KEY": "",
        "PUBLIC_MODE": "false",
        "ALLOW_PRIVATE_URLS": "false",
        "PROXIES": "",
        "PROXY_TEMPLATE": "",
        "PROXY_FILE": "",
        "SEARXNG_URL": "",
        "API_REQUESTS_PER_MINUTE": "1000",
        "BROWSER_ENABLED": "true",
        "MAX_CONCURRENT_REQUESTS": "2",
        "PDF_LAYOUT_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
