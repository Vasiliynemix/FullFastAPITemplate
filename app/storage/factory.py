"""
Фабрика хранилища + общий singleton (как у брокера).

Реализация — S3-совместимое хранилище. Абстракция AbstractStorage сохранена: если
понадобится другой бэкенд (например GCS), добавляется новый класс и ветка здесь.
S3-клиент (aioboto3) импортируется лениво. Подключение/закрытие — в lifespan и воркере.
"""

from __future__ import annotations

from app.storage.base import AbstractStorage

_storage: AbstractStorage | None = None


def build_storage() -> AbstractStorage:
    from app.storage.s3 import build_s3_storage

    return build_s3_storage()


def get_storage() -> AbstractStorage:
    """Singleton хранилища на процесс. Инициализируется в lifespan."""
    global _storage
    if _storage is None:
        _storage = build_storage()
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None
