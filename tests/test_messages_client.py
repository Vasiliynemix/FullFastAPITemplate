"""Тесты MessagesClient — внешний конверт -> наш ApiResponse[T] (через MockTransport)."""

from __future__ import annotations

import httpx
import pytest

from app.clients.messages import CreateTaskResult, MessagesClient, TaskState, TaskStatus

pytestmark = pytest.mark.asyncio


def _client(handler) -> MessagesClient:
    return MessagesClient(
        base_url="https://api.test",
        api_key="KEY",
        transport=httpx.MockTransport(handler),
    )


async def test_create_task_success_and_api_key_header():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["key"] = req.headers.get("x-api-key")
        return httpx.Response(200, json={"status": True, "data": {"task_id": "01J3K9"}})

    async with _client(handler) as c:
        res = await c.create_task("+79991234567", "hello")

    assert seen["key"] == "KEY"  # авторизация ушла в заголовке
    assert res.status is True
    assert isinstance(res.data, CreateTaskResult)
    assert res.data.task_id == "01J3K9"


async def test_create_task_app_error_keeps_error_code():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": False, "error_code": "INVALID_PHONE", "message": "phone must be E.164"},
        )

    async with _client(handler) as c:
        res = await c.create_task("+70000000000", "hi")

    assert res.status is False
    assert res.error.code == "INVALID_PHONE"
    assert res.error.message == "phone must be E.164"


async def test_create_task_client_side_validation_no_network():
    called = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json={"status": True, "data": {"task_id": "x"}})

    async with _client(handler) as c:
        res = await c.create_task("123", "hi")  # не E.164

    assert res.status is False
    assert res.error.code == "INVALID_INPUT"
    assert called["n"] == 0  # запрос не ушёл


async def test_create_task_text_too_long_client_side():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": True, "data": {"task_id": "x"}})

    async with _client(handler) as c:
        res = await c.create_task("+79991234567", "a" * 4001)
    assert res.status is False
    assert res.error.code == "INVALID_INPUT"


async def test_get_status_delivered_parses_timestamp():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": True,
                "data": {
                    "task_id": "01J3K9",
                    "state": "delivered",
                    "delivered_at": "2025-05-01T14:32:00Z",
                },
            },
        )

    async with _client(handler) as c:
        res = await c.get_status("01J3K9")

    assert res.status is True
    assert isinstance(res.data, TaskStatus)
    assert res.data.state is TaskState.DELIVERED
    assert res.data.delivered_at is not None
    assert res.data.is_terminal is True


async def test_get_status_failed_has_error_fields():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": True,
                "data": {
                    "task_id": "01J3K9",
                    "state": "failed",
                    "error_code": "RECIPIENT_NOT_REGISTERED",
                    "error_message": "recipient not registered",
                },
            },
        )

    async with _client(handler) as c:
        res = await c.get_status("01J3K9")

    assert res.status is True
    assert res.data.state is TaskState.FAILED
    assert res.data.error_code == "RECIPIENT_NOT_REGISTERED"


async def test_get_status_not_found():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": False, "error_code": "TASK_NOT_FOUND", "message": "task not found"}
        )

    async with _client(handler) as c:
        res = await c.get_status("missing")

    assert res.status is False
    assert res.error.code == "TASK_NOT_FOUND"


async def test_cancel_success_data_null():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": True, "data": None})

    async with _client(handler) as c:
        res = await c.cancel("01J3K9")

    assert res.status is True
    assert res.data is None


async def test_cancel_cannot_cancel():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": False,
                "error_code": "CANNOT_CANCEL",
                "message": "task is not in pending state",
            },
        )

    async with _client(handler) as c:
        res = await c.cancel("01J3K9")

    assert res.status is False
    assert res.error.code == "CANNOT_CANCEL"


async def test_http_error_with_error_code_in_body():
    # На случай, если внешний API отдаёт ошибку с HTTP 4xx (а не 200)
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404, json={"status": False, "error_code": "TASK_NOT_FOUND", "message": "task not found"}
        )

    async with _client(handler) as c:
        res = await c.get_status("missing")

    assert res.status is False
    assert res.error.code == "TASK_NOT_FOUND"
    assert res.error.upstream_status == 404
