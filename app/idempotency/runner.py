"""
Хелпер idempotent(): оборачивает обработчик в логику Idempotency-Key одной строкой.

Без него каждая ручка дублировала бы ~12 строк (try_acquire / replay / save / release).
Семантика заголовка `Idempotency-Key` (см. app/idempotency/store.py):

* ключ не передан        -> просто выполнить produce() (идемпотентность отключена для вызова);
* ключ свободен          -> выполнить produce(), сохранить ответ под ключом (TTL ~ сутки);
* ключ с готовым ответом  -> вернуть его БЕЗ повторного выполнения (replay);
* ключ занят in-flight    -> 409 ConflictError (параллельный запрос ещё выполняется).

Важно: при replay конверт восстанавливается из кэша через model_validate, а НЕ через
success() — это рехидрация уже собранного ответа, success() переобернул бы данные заново
(единственное легальное исключение из «собирай только хелпером», см.
tests/test_response_contract.py). При ошибке produce() ключ отпускается — ретрай возможен.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.exceptions.base import ConflictError
from app.idempotency.store import get_idempotency_store
from app.schemas.response import SuccessResponse

T = TypeVar("T")


async def idempotent(
    key: str | None,
    model: type[SuccessResponse[T]],
    produce: Callable[[], Awaitable[SuccessResponse[T]]],
) -> SuccessResponse[T]:
    """
    Выполнить produce() идемпотентно по ключу Idempotency-Key.

    model — конкретный тип конверта (например SuccessResponse[AccountRead]); нужен для
    рехидрации сохранённого ответа из кэша. produce — корутина, собирающая свежий ответ.
    """
    if not key:
        return await produce()

    store = get_idempotency_store()
    hit = await store.try_acquire(key)
    if hit.in_progress:
        raise ConflictError("A request with this Idempotency-Key is already in progress")
    if hit.found and hit.response is not None:
        return model.model_validate(hit.response)

    try:
        response = await produce()
    except Exception:
        await store.release(key)  # не «залипаем» на ключе — клиент сможет повторить
        raise
    await store.save_response(key, response.model_dump(mode="json"))
    return response
