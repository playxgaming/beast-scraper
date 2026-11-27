"""Bounded downloads with validation before every redirect."""

import asyncio
from urllib.parse import urljoin

import httpx

from beast_scraper.core.rate_limit import validate_target_url


async def download_url(
    url: str,
    *,
    max_bytes: int,
    timeout: float = 30,
    allow_private: bool = False,
    headers: dict[str, str] | None = None,
    proxy: str | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout, proxy=proxy, trust_env=False) as client:
        request_headers = dict(headers or {})
        for _ in range(11):
            url = await asyncio.to_thread(validate_target_url, url, allow_private)
            async with client.stream("GET", url, headers=request_headers, follow_redirects=False) as response:
                if response.status_code in {301, 302, 303, 307, 308} and response.headers.get("location"):
                    target = urljoin(str(response.url), response.headers["location"])
                    old, new = httpx.URL(url), httpx.URL(target)
                    if (new.scheme, new.host, new.port) != (old.scheme, old.host, old.port):
                        request_headers = {
                            k: v
                            for k, v in request_headers.items()
                            if k.lower() not in {"authorization", "cookie", "proxy-authorization"}
                        }
                    url = target
                    continue
                if int(response.headers.get("content-length", "0")) > max_bytes:
                    raise ValueError("Response exceeds the configured download size limit.")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise ValueError("Response exceeds the configured download size limit.")
                return httpx.Response(
                    response.status_code,
                    headers={
                        k: v
                        for k, v in response.headers.items()
                        if k.lower() not in {"content-encoding", "content-length"}
                    },
                    content=bytes(body),
                    request=response.request,
                )
    raise ValueError("Too many redirects.")
