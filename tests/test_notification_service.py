"""Тесты NotificationService — бизнес-логика поверх MessagesClient (MockTransport)."""

from __future__ import annotations

import httpx
import pytest

from app.clients.messages import MessagesClient, TaskState
from app.exceptions.base import ServerException
from app.services.notification import NotificationService

pytestmark = pytest.mark.asyncio


def _service(handler) -> NotificationService:
    client = MessagesClient(
        base_url="https://api.test", api_key="K", transport=httpx.MockTransport(handler)
    )
    return NotificationService(messages=client)


async def test_send_success_returns_task_id():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": True, "data": {"task_id": "01J3K9"}})

    svc = _service(handler)
    result = await svc.send("+79991234567", "hi")
    assert result.task_id == "01J3K9"


async def test_send_invalid_phone_maps_to_400():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": False, "error_code": "INVALID_PHONE", "message": "bad phone"}
        )

    svc = _service(handler)
    with pytest.raises(ServerException) as exc:
        await svc.send("+70000000000", "hi")
    assert exc.value.status_code == 400


async def test_status_not_found_maps_to_404():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": False, "error_code": "TASK_NOT_FOUND", "message": "nope"}
        )

    svc = _service(handler)
    with pytest.raises(ServerException) as exc:
        await svc.status("missing")
    assert exc.value.status_code == 404


async def test_cancel_conflict_maps_to_409():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": False, "error_code": "CANNOT_CANCEL", "message": "not pending"}
        )

    svc = _service(handler)
    with pytest.raises(ServerException) as exc:
        await svc.cancel("01J3K9")
    assert exc.value.status_code == 409


async def test_status_returns_typed_model():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": True, "data": {"task_id": "01J3K9", "state": "read"}},
        )

    svc = _service(handler)
    st = await svc.status("01J3K9")
    assert st.state is TaskState.READ
