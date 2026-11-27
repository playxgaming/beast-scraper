"""Process-wide limits for small public deployments."""

import time
from collections import deque

from starlette.responses import JSONResponse


class APILimitsMiddleware:
    """Bound request bodies, request rate and concurrent API operations."""

    def __init__(self, app):
        self.app = app
        self.requests = deque()
        self.active = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in {
            "/",
            "/health",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/favicon.ico",
        }:
            return await self.app(scope, receive, send)

        from beast_scraper.config import get_settings

        settings = get_settings()
        now = time.monotonic()
        while self.requests and self.requests[0] <= now - 60:
            self.requests.popleft()
        if len(self.requests) >= settings.api_requests_per_minute or self.active >= settings.max_concurrent_requests:
            response = JSONResponse(
                {"detail": "API is busy. Retry shortly."}, status_code=429, headers={"Retry-After": "60"}
            )
            return await response(scope, receive, send)
        self.requests.append(now)
        self.active += 1
        try:
            # Buffer at most 64 KiB, including chunked bodies, before passing to FastAPI.
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 65536:
                    return await JSONResponse({"detail": "Request body too large."}, status_code=413)(
                        scope, receive, send
                    )
                if not message.get("more_body", False):
                    break

            delivered = False

            async def buffered_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            await self.app(scope, buffered_receive, send)
        finally:
            self.active -= 1
