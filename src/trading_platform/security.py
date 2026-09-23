import os
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse


@dataclass(frozen=True)
class SecurityConfig:
    require_auth: bool
    api_key: str | None
    max_body_bytes: int
    mutation_rate_per_minute: int

    @classmethod
    def from_env(cls) -> "SecurityConfig":
        key = os.getenv("TRADING_API_KEY")
        require = os.getenv("TRADING_REQUIRE_AUTH", "0").lower() in {"1", "true", "yes"}
        if require and not key:
            raise RuntimeError("TRADING_REQUIRE_AUTH=1 requires TRADING_API_KEY")
        return cls(
            require_auth=require,
            api_key=key,
            max_body_bytes=int(os.getenv("TRADING_MAX_BODY_BYTES", "2097152")),
            mutation_rate_per_minute=int(os.getenv("TRADING_MUTATION_RATE_PER_MINUTE", "120")),
        )


class MutationRateLimiter:
    def __init__(self, limit: int):
        self.limit = max(1, limit)
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - 60.0
        with self._lock:
            events = self._events[key]
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True


def install_security_middleware(app, config: SecurityConfig) -> MutationRateLimiter:
    limiter = MutationRateLimiter(config.mutation_rate_per_minute)
    unsafe = {"POST", "PUT", "PATCH", "DELETE"}

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > config.max_body_bytes:
                    return JSONResponse(status_code=413, content={"detail": "request body too large"})
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "invalid content-length"})

        if request.method in unsafe:
            client = request.client.host if request.client else "unknown"
            if not limiter.allow(client):
                return JSONResponse(status_code=429, content={"detail": "mutation rate limit exceeded"})
            if config.require_auth:
                authorization = request.headers.get("authorization", "")
                expected = f"Bearer {config.api_key}"
                if not secrets.compare_digest(authorization, expected):
                    return JSONResponse(status_code=401, content={"detail": "authentication required"})

        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'"
        return response

    return limiter
