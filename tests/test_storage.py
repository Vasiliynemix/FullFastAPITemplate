"""Юнит-тесты S3-хранилища без сети: фабрика, presigned по public_url, маппинг ошибок."""

from __future__ import annotations

import pytest

from app.storage.factory import get_storage, reset_storage
from app.storage.s3 import S3Storage


def _s3(**kw) -> S3Storage:
    defaults: dict = {
        "bucket": "b",
        "endpoint_url": None,
        "region": "us-east-1",
        "access_key": "",
        "secret_key": "",
        "use_ssl": True,
    }
    defaults.update(kw)
    return S3Storage(**defaults)


def test_factory_builds_s3():
    reset_storage()
    assert isinstance(get_storage(), S3Storage)
    reset_storage()


async def test_presigned_public_url_without_client():
    # Если задан публичный URL — presigned_url отдаёт прямую ссылку, клиент S3 не нужен
    s = _s3(public_url="https://cdn.example.com")
    assert await s.presigned_url("img/a.png") == "https://cdn.example.com/img/a.png"


def test_is_not_found_maps_client_error():
    from botocore.exceptions import ClientError

    not_found = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
    other = ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")
    assert S3Storage._is_not_found(not_found) is True
    assert S3Storage._is_not_found(other) is False
    assert S3Storage._is_not_found(ValueError("x")) is False


def test_operations_require_connect():
    s = _s3()
    with pytest.raises(RuntimeError, match="не подключ"):
        s._c()
