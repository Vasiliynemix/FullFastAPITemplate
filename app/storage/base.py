"""
Абстракция объектного хранилища (S3-совместимое).

Сервисы зависят от интерфейса AbstractStorage, а не от конкретного бэкенда —
так же, как с кэшем (AbstractCache) и брокером (AbstractBroker). Реализация —
S3Storage (см. app/storage/s3.py), собирается фабрикой app/storage/factory.py.

Ключ (key) — это «путь» объекта внутри бакета, например "uploads/ab12.jpg".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class ObjectNotFoundError(Exception):
    """Объекта с таким ключом нет в хранилище."""

    def __init__(self, key: str) -> None:
        super().__init__(f"Object not found: {key}")
        self.key = key


class AbstractStorage(ABC):
    """Контракт объектного хранилища. Все методы async."""

    # connect/close нужны бэкендам с долгоживущим клиентом (S3 их переопределяет).
    # По умолчанию — no-op, чтобы простому бэкенду не требовалось их реализовывать.
    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    @abstractmethod
    async def put(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
        """Сохранить объект (перезаписывает существующий)."""

    @abstractmethod
    async def get(self, key: str) -> bytes:
        """Прочитать объект целиком. ObjectNotFoundError, если ключа нет."""

    @abstractmethod
    def stream(self, key: str, *, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
        """
        Потоковое чтение объекта по чанкам — память O(chunk_size), а не O(файла).
        Реализуется async-генератором; вызывается как `async for c in storage.stream(key)`.
        """
        ...

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Удалить объект. Отсутствие ключа — не ошибка (идемпотентно)."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Есть ли объект с таким ключом."""

    @abstractmethod
    async def list(self, prefix: str = "") -> list[str]:
        """Ключи объектов с заданным префиксом."""

    @abstractmethod
    async def presigned_url(self, key: str, *, expires: int = 3600, method: str = "GET") -> str:
        """
        URL, по которому клиент может напрямую скачать (GET) или загрузить (PUT) объект,
        минуя наш бэкенд — подписанная временная ссылка S3 (offload трафика на S3).
        Для приватных объектов отдавайте файл через download-эндпоинт (см. /files).
        """
