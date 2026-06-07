"""Абстракция кэша. Сервисы зависят от интерфейса, а не от Redis напрямую."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AbstractCache(ABC):
    @abstractmethod
    async def get(self, key: str) -> Any | None: ...

    @abstractmethod
    async def set(self, key: str, value: Any, *, ttl: int | None = None) -> None: ...

    @abstractmethod
    async def delete(self, *keys: str) -> int: ...

    @abstractmethod
    async def exists(self, key: str) -> bool: ...

    @abstractmethod
    async def incr(self, key: str, *, amount: int = 1, ttl: int | None = None) -> int: ...
