"""Тесты базового HTTP-клиента (через httpx.MockTransport — без сети)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.clients.auth import (
    ApiKeyHeaderHTTPClient,
    ApiKeyQueryHTTPClient,
    BearerHTTPClient,
    LoginTokenHTTPClient,
)
from app.clients.base import BaseHTTPClient, ExternalAPIError, RetryPolicy
from app.clients.envelope import EnvelopeHTTPClient

pytestmark = pytest.mark.asyncio


class _Item(BaseModel):
    id: int
    name: str


def _client(handler, **kw: Any) -> BaseHTTPClient:
    class _C(BaseHTTPClient):
        base_url = "https://api.test"
        service_name = "test"

    return _C(transport=httpx.MockTransport(handler), **kw)


async def test_get_parses_json():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    async with _client(handler) as c:
        assert await c.get("/ping") == {"ok": True}


async def test_4xx_maps_to_external_error_with_upstream():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "nope"})

    async with _client(handler) as c:
        with pytest.raises(ExternalAPIError) as exc:
            await c.get("/missing")
    assert exc.value.status_code == 502  # наружу — 502
    assert exc.value.upstream_status == 404  # но upstream сохранён
    assert exc.value.upstream_body == {"error": "nope"}


async def test_retries_on_503_then_succeeds():
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    async with _client(handler, retry=RetryPolicy(attempts=3, backoff_base=0.0)) as c:
        assert await c.get("/flaky") == {"ok": True}
    assert calls["n"] == 3


async def test_post_not_retried_by_default():
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    async with _client(handler, retry=RetryPolicy(attempts=3, backoff_base=0.0)) as c:
        with pytest.raises(ExternalAPIError):
            await c.post("/orders", json={})
    assert calls["n"] == 1  # POST не ретраится


async def test_prepare_injects_header_and_query():
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["key"] = request.url.params.get("appid")
        return httpx.Response(200, json={})

    class _Keyed(BaseHTTPClient):
        base_url = "https://api.test"
        service_name = "keyed"

        def default_headers(self) -> dict[str, str]:
            return {"Authorization": "Bearer T"}

        async def prepare(self, method: str, url: str, options: dict[str, Any]) -> None:
            options.setdefault("params", {})["appid"] = "KEY123"

    async with _Keyed(transport=httpx.MockTransport(handler)) as c:
        await c.get("/x")

    assert captured["auth"] == "Bearer T"
    assert captured["key"] == "KEY123"


async def test_timeout_maps_to_external_error():
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom")

    async with _client(handler, retry=RetryPolicy(attempts=2, backoff_base=0.0)) as c:
        with pytest.raises(ExternalAPIError):
            await c.get("/slow")


async def test_model_validation_single():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 1, "name": "x"})

    async with _client(handler) as c:
        item = await c.get("/item", model=_Item)
    assert isinstance(item, _Item)
    assert item.id == 1 and item.name == "x"


async def test_model_validation_list():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])

    async with _client(handler) as c:
        items = await c.get("/items", model=list[_Item])
    assert len(items) == 2
    assert all(isinstance(i, _Item) for i in items)


async def test_model_mismatch_raises_external_error():
    def handler(_: httpx.Request) -> httpx.Response:
        # отсутствует обязательное поле name -> схема не сойдётся
        return httpx.Response(200, json={"id": 1})

    async with _client(handler) as c:
        with pytest.raises(ExternalAPIError) as exc:
            await c.get("/item", model=_Item)
    assert exc.value.status_code == 502
    assert exc.value.upstream_body == {"id": 1}


# --- Единый конверт call() ---
async def test_call_envelope_success():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 1, "name": "x"})

    async with _client(handler) as c:
        resp = await c.call("GET", "/item", model=_Item)
    assert resp.status is True
    assert resp.data == _Item(id=1, name="x")
    assert resp.error is None


async def test_call_envelope_error_not_raised():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"e": "nope"})

    async with _client(handler) as c:
        resp = await c.call("GET", "/item", model=_Item)
    assert resp.status is False
    assert resp.data is None
    assert resp.error.upstream_status == 404
    assert resp.error.upstream_body == {"e": "nope"}


# --- Auth-обёртки/миксины ---
def _wrap(cls, handler, **kw):
    class _C(cls):
        base_url = "https://api.test"
        service_name = "t"

    return _C(transport=httpx.MockTransport(handler), **kw)


async def test_bearer_wrapper_injects_token():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={})

    async with _wrap(BearerHTTPClient, handler, token="TKN") as c:
        await c.get("/x")
    assert seen["auth"] == "Bearer TKN"


async def test_api_key_header_wrapper():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["key"] = req.headers.get("x-secret")
        return httpx.Response(200, json={})

    async with _wrap(ApiKeyHeaderHTTPClient, handler, api_key="K", api_key_header="X-Secret") as c:
        await c.get("/x")
    assert seen["key"] == "K"


async def test_api_key_query_wrapper():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["appid"] = req.url.params.get("appid")
        return httpx.Response(200, json={})

    async with _wrap(ApiKeyQueryHTTPClient, handler, api_key="K", api_key_param="appid") as c:
        await c.get("/x")
    assert seen["appid"] == "K"


async def test_envelope_mixin_custom_field_names():
    # Другой формат конверта: {ok, result, err_code, err_msg} — меняем только имена полей
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/ok":
            return httpx.Response(200, json={"ok": True, "result": {"id": 7, "name": "z"}})
        return httpx.Response(200, json={"ok": False, "err_code": "NOPE", "err_msg": "denied"})

    class CustomClient(EnvelopeHTTPClient):
        base_url = "https://api.test"
        service_name = "custom"
        envelope_status_field = "ok"
        envelope_data_field = "result"
        envelope_error_code_field = "err_code"
        envelope_error_message_field = "err_msg"

    async with CustomClient(transport=httpx.MockTransport(handler)) as c:
        ok = await c.call_envelope("GET", "/ok", data_model=_Item)
        bad = await c.call_envelope("GET", "/bad", data_model=_Item)

    assert ok.status is True and ok.data == _Item(id=7, name="z")
    assert bad.status is False and bad.error.code == "NOPE" and bad.error.message == "denied"


async def test_login_token_flow_and_relogin():
    state = {"logins": 0, "calls": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/login":
            state["logins"] += 1
            return httpx.Response(200, json={"access_token": f"T{state['logins']}"})
        state["calls"] += 1
        # первый защищённый вызов -> 401 (протух), после перелогина -> 200
        if state["calls"] == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"id": 1, "name": "x"})

    class Billing(LoginTokenHTTPClient):
        base_url = "https://api.test"
        service_name = "billing"
        login_path = "/login"

    async with Billing(transport=httpx.MockTransport(handler), username="u", password="p") as c:
        item = await c.get("/protected", model=_Item)

    assert item == _Item(id=1, name="x")
    assert state["logins"] == 2  # начальный логин + перелогин после 401
