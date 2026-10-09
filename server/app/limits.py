"""
Guards against a misbehaving pipeline: a cap on request bodies (every route) and
a per-account limit on uploads (/analyze).

The upload limit is counted in this process. With several server replicas each
counts on its own, so the effective limit is replicas × MERGECLEAR_UPLOADS_PER_MINUTE.
"""
import collections
import threading
import time

from app.config import MAX_UPLOAD_BYTES, UPLOADS_PER_MINUTE


class _TooLarge(Exception):
    pass


class BodySizeLimit:
    """ASGI middleware: 413 for bodies over `max_bytes`, declared (Content-Length) or streamed (chunked)."""

    def __init__(self, app, max_bytes: int = MAX_UPLOAD_BYTES):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.max_bytes:
            return await self.app(scope, receive, send)
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            return await self._refuse(send)

        received, started = 0, False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _TooLarge()
            return message

        async def tracking_send(message):
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if not started:
                await self._refuse(send)

    async def _refuse(self, send):
        limit_mb = self.max_bytes / 1_000_000
        body = (f'{{"error": "request body over the {limit_mb:g} MB limit '
                f'(server setting MERGECLEAR_MAX_UPLOAD_MB)"}}').encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


class RateLimit:
    """Sliding one-minute window per key; `check` returns 0 when allowed, else seconds to wait."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits = collections.defaultdict(collections.deque)
        self._lock = threading.Lock()

    def check(self, key, now: float = None) -> int:
        if self.per_minute <= 0:
            return 0
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return max(1, int(hits[0] + 60 - now) + 1)
            hits.append(now)
            return 0

    def reset(self):
        with self._lock:
            self._hits.clear()


uploads = RateLimit(UPLOADS_PER_MINUTE)
