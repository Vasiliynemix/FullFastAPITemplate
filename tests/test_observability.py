"""Sentry-интеграция: без DSN всё должно быть no-op (Sentry выключен по умолчанию)."""

from __future__ import annotations

from app.core.config import settings
from app.core.observability import init_sentry


def test_sentry_disabled_without_dsn(monkeypatch):
    monkeypatch.setattr(settings, "sentry_dsn", "")
    assert init_sentry() is False  # без DSN — no-op, не падает и не включается
