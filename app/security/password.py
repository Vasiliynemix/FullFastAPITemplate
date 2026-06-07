"""
Хеширование паролей.

Используем pwdlib с argon2 (современная замена passlib). argon2id устойчив к
GPU/ASIC-перебору. Проверка через verify в постоянном времени.

ВАЖНО про производительность: argon2 — намеренно тяжёлая CPU-bound операция
(десятки мс). Вызванная напрямую в async-ручке, она блокирует event loop воркера
на всё время хеша — под массовым login/register это сериализует ВЕСЬ воркер
(встают и не связанные с auth запросы). Поэтому в горячем пути (AuthService)
используем async-версии: они уносят хеш в пул потоков через anyio.to_thread.
argon2-cffi отпускает GIL на время вычисления, так что потоки реально
параллелятся по ядрам. Синхронные версии оставлены для тестов/скриптов/CLI.
"""

from __future__ import annotations

import anyio
from pwdlib import PasswordHash

# recommended() = argon2id с разумными параметрами по умолчанию
_hasher = PasswordHash.recommended()


def hash_password(plain: str) -> str:
    """Синхронный хеш. Не вызывать в async-ручках под нагрузкой — см. hash_password_async."""
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str | None) -> bool:
    """Синхронная проверка. В async-ручках использовать verify_password_async."""
    if not hashed:
        return False
    return _hasher.verify(plain, hashed)


async def hash_password_async(plain: str) -> str:
    """Хеширование в пуле потоков — не блокирует event loop."""
    return await anyio.to_thread.run_sync(_hasher.hash, plain)


async def verify_password_async(plain: str, hashed: str | None) -> bool:
    """Проверка пароля в пуле потоков — не блокирует event loop."""
    if not hashed:
        return False
    return await anyio.to_thread.run_sync(_hasher.verify, plain, hashed)
