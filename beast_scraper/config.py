"""Application settings loaded from environment variables or .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration for the BeastScraper API server."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8000
    api_key: str = ""
    admin_api_key: str = ""
    public_mode: bool = False
    api_requests_per_minute: int = Field(60, ge=1, le=10000)
    max_concurrent_requests: int = Field(2, ge=1, le=50)
    browser_enabled: bool = True
    web_max_mb: int = Field(5, ge=1, le=50)
    openalex_api_key: str = ""
    pdf_layout_enabled: bool = False

    cache_db_path: Path = Path("data/cache.db")
    cache_ttl_seconds: int = 3600
    rate_limit_per_domain_rpm: int = 30

    playwright_headless: bool = True
    request_timeout_seconds: int = Field(30, ge=1, le=120)
    max_crawl_pages: int = Field(25, ge=1, le=100)
    pdf_max_mb: int = Field(30, ge=1, le=100)

    searxng_url: str = ""

    proxies: str = ""
    proxy_template: str = ""
    proxy_file: str = ""
    proxies_premium_only: bool = False

    allow_private_urls: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return cached settings singleton."""
    return Settings()
