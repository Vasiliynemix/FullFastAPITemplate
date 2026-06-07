"""Тесты декораторов retry / cached / logged."""

from __future__ import annotations

import pytest

from app.decorators.logging import logged
from app.decorators.retry import retry

pytestmark = pytest.mark.asyncio


async def test_retry_succeeds_after_failures():
    calls = {"n": 0}

    @retry(attempts=3, base_delay=0.0, exceptions=(ValueError,))
    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("boom")
        return "ok"

    assert await flaky() == "ok"
    assert calls["n"] == 3


async def test_retry_reraises_after_exhaustion():
    @retry(attempts=2, base_delay=0.0, exceptions=(ValueError,))
    async def always_fail() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError):
        await always_fail()


async def test_logged_passes_through_result():
    @logged("test.op")
    async def op(x: int) -> int:
        return x * 2

    assert await op(21) == 42
