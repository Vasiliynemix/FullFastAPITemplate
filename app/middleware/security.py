"""
Security-заголовки на уровне приложения (дублируют Nginx как defense-in-depth).

В проде основной набор заголовков выставляет Nginx; здесь — минимальный страховой
набор, чтобы заголовки были даже при прямом обращении к приложению.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
]


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend(_HEADERS)
            await send(message)

        await self.app(scope, receive, send_wrapper)
