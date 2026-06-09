"""Снимок конфига в /health/ready не должен утекать секреты (эндпоинт открытый)."""

from __future__ import annotations

import json

from app.api.v1.health import _active_config
from app.core.config import settings


def test_active_config_has_no_secrets(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret_key", "SUPER_SECRET_JWT_123")
    monkeypatch.setattr(settings, "global_api_key", "SECRET_API_KEY_456")
    monkeypatch.setattr(settings, "postgres_password", "SECRET_DB_PW_789")

    blob = json.dumps(_active_config(), ensure_ascii=False)

    # ни один секрет не попал в снимок
    assert "SUPER_SECRET_JWT_123" not in blob
    assert "SECRET_API_KEY_456" not in blob
    assert "SECRET_DB_PW_789" not in blob


def test_active_config_safe_fields_and_no_stack_fingerprint():
    cfg = _active_config()
    assert "environment" in cfg
    assert set(cfg["auth"]) == {"jwt", "global_api_key", "token_transport", "validate_session"}
    # тип брокера/хранилища НЕ раскрываем (фингерпринт стека)
    assert "type" not in cfg["broker"]
    assert isinstance(cfg["broker"]["enabled"], bool)
