"""
Shared secret giữa Next.js và FastAPI (SURVEY bước 5): backend từ chối request thiếu/sai header
X-Internal-Secret. Chỉ frontend (biết BACKEND_SHARED_SECRET) gọi được backend → các header do
frontend đặt (X-Client-IP cho rate limit) đáng tin.

Ngoại lệ: /health gọi từ chính container (127.0.0.1 / ::1) — Docker HEALTHCHECK.
Middleware ASGI thuần (không BaseHTTPMiddleware) → không đụng tới luồng SSE.
"""
from __future__ import annotations

import hmac
import json
from typing import Iterable

HEADER = b"x-internal-secret"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
MIN_LEN = 32


class InternalSecretMiddleware:
    def __init__(self, app, secret: str, local_exempt_paths: Iterable[str] = ("/health",)):
        self.app = app
        self.secret = (secret or "").encode()
        self.local_exempt = set(local_exempt_paths)
        if not self.secret:
            print("[Secret] BACKEND_SHARED_SECRET chưa đặt — KHÔNG kiểm header (chỉ chấp nhận khi dev)")
        elif len(self.secret) < MIN_LEN:
            print(f"[Secret] BACKEND_SHARED_SECRET ngắn hơn {MIN_LEN} ký tự — nên đặt dài hơn")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.secret or self._allowed(scope):
            await self.app(scope, receive, send)
            return
        body = json.dumps({"detail": "Forbidden"}).encode()
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    def _allowed(self, scope) -> bool:
        client = (scope.get("client") or ("",))[0]
        if scope.get("path") in self.local_exempt and client in LOOPBACK:
            return True
        provided = b""
        for k, v in scope.get("headers") or []:
            if k.lower() == HEADER:
                provided = v
                break
        return hmac.compare_digest(provided, self.secret)
