"""Per-domain async rate limiting and SSRF URL safety validation."""

import asyncio
import ipaddress
import socket
import time
from collections import defaultdict
from urllib.parse import urlparse


class DomainRateLimiter:
    """Enforces a minimum interval between requests to the same hostname."""

    def __init__(self, requests_per_minute: int = 30) -> None:
        self.rpm = requests_per_minute
        self.min_interval = 60.0 / requests_per_minute if requests_per_minute > 0 else 0.0
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_request: dict[str, float] = {}

    async def acquire(self, url: str) -> None:
        if self.min_interval <= 0:
            return
        host = (urlparse(url).hostname or "").lower()
        if not host:
            return
        async with self._locks[host]:
            now = time.monotonic()
            last = self._last_request.get(host)
            if last is not None:
                wait = self.min_interval - (now - last)
                if wait > 0:
                    await asyncio.sleep(wait)
            self._last_request[host] = time.monotonic()


def validate_target_url(url: str, allow_private: bool = False) -> str:
    """Ensure URL is http/https and optionally block loopback/private SSRF targets."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("A valid HTTP(S) URL is required.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Credentials in target URLs are not allowed.")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("Invalid URL port.") from exc

    if allow_private:
        return parsed.geturl()

    host = parsed.hostname.lower()
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        raise ValueError(f"Blocked private/local hostname: {host}")

    try:
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_global or ip.is_multicast:
                raise ValueError(f"Blocked non-public IP address {ip} for host {host}")
    except socket.gaierror as exc:
        raise ValueError("Target hostname could not be resolved.") from exc

    return parsed.geturl()
