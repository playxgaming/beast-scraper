"""Proxy rotation, geo-targeting, and health tracking for residential/ISP/datacenter proxies."""

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass
class ProxyEntry:
    url: str
    country: str | None = None
    failures: int = 0
    successes: int = 0
    cooldown_until: float = 0.0

    @property
    def masked(self) -> str:
        parsed = urlparse(self.url)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host}{port}"


class ProxyManager:
    """Manages static proxy pools, geo-tagged entries, and rotating gateway templates."""

    def __init__(
        self,
        proxies_raw: str = "",
        proxy_template: str = "",
        proxy_file: str = "",
        premium_only: bool = False,
    ) -> None:
        self.proxy_template = proxy_template.strip()
        self.premium_only = premium_only
        self._entries: list[ProxyEntry] = []
        self._cursor = 0

        if proxies_raw.strip():
            self.add_many([p.strip() for p in proxies_raw.split(",") if p.strip()])
        if proxy_file.strip():
            p = Path(proxy_file.strip())
            if p.exists():
                lines = [
                    ln.strip()
                    for ln in p.read_text(encoding="utf-8").splitlines()
                    if ln.strip() and not ln.strip().startswith("#")
                ]
                self.add_many(lines)

    @staticmethod
    def _parse_spec(spec: str) -> ProxyEntry:
        # Supports `us|http://user:pass@host:port` or plain `http://...`
        if "|" in spec and not spec.startswith(("http://", "https://", "socks5://", "socks4://")):
            country, url = spec.split("|", 1)
            return ProxyEntry(url=url.strip(), country=country.strip().lower() or None)
        return ProxyEntry(url=spec.strip())

    def add_many(self, specs: list[str], replace: bool = False) -> int:
        parsed_entries = [self._parse_spec(spec) for spec in specs]
        for entry in parsed_entries:
            parsed = urlparse(entry.url)
            if parsed.scheme not in {"http", "https", "socks5", "socks5h", "socks4"} or not parsed.hostname:
                raise ValueError("A valid HTTP(S) or SOCKS proxy URL is required.")
            try:
                parsed.port
            except ValueError as exc:
                raise ValueError("Invalid proxy port.") from exc
        if replace:
            self._entries.clear()
            self._cursor = 0
        existing = {(e.country, e.url) for e in self._entries}
        added = 0
        for entry in parsed_entries:
            if (entry.country, entry.url) not in existing:
                self._entries.append(entry)
                existing.add((entry.country, entry.url))
                added += 1
        return added

    def has_proxies(self) -> bool:
        return bool(self._entries or self.proxy_template)

    def acquire(
        self,
        country: str | None = None,
        premium: bool = False,
        explicit_proxy: str | None = None,
    ) -> str | None:
        """Select the next healthy proxy URL, or None for direct connection."""
        if explicit_proxy:
            return explicit_proxy.strip()

        if self.premium_only and not premium and not country:
            return None

        target_country = country.lower().strip() if country else None
        now = time.monotonic()

        # 1. Try matching static pool entries
        candidates = [
            e
            for e in self._entries
            if e.cooldown_until <= now and (target_country is None or e.country == target_country or e.country is None)
        ]
        # Prefer exact country match when country is specified
        if target_country:
            exact = [e for e in candidates if e.country == target_country]
            if exact:
                candidates = exact

        if candidates:
            entry = candidates[self._cursor % len(candidates)]
            self._cursor += 1
            return entry.url

        # 2. Fall back to rotating gateway template (e.g. Bright Data / Smartproxy / IPRoyal)
        if self.proxy_template:
            return self.proxy_template.replace("{country}", target_country or "us").replace(
                "{session}", uuid.uuid4().hex[:10]
            )

        return None

    def report_success(self, proxy_url: str | None) -> None:
        if not proxy_url:
            return
        for e in self._entries:
            if e.url == proxy_url:
                e.successes += 1
                e.failures = 0
                e.cooldown_until = 0.0

    def report_failure(self, proxy_url: str | None, cooldown_seconds: float = 60.0) -> None:
        if not proxy_url:
            return
        for e in self._entries:
            if e.url == proxy_url:
                e.failures += 1
                if e.failures >= 2:
                    e.cooldown_until = time.monotonic() + cooldown_seconds

    def status(self) -> dict:
        now = time.monotonic()
        return {
            "static_count": len(self._entries),
            "template_configured": bool(self.proxy_template),
            "premium_only": self.premium_only,
            "proxies": [
                {
                    "proxy": e.masked,
                    "country": e.country,
                    "successes": e.successes,
                    "failures": e.failures,
                    "cooling_down": e.cooldown_until > now,
                }
                for e in self._entries
            ],
        }
