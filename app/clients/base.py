"""
Базовый async HTTP-клиент для интеграций с внешними API.

От него наследуются конкретные клиенты. База берёт на себя инфраструктуру:
* пул соединений (один httpx.AsyncClient, keep-alive) — переиспользуется;
* ретраи на временные ошибки (таймауты, 5xx, 429) с экспоненциальным backoff
  и учётом заголовка Retry-After; по умолчанию только идемпотентные методы;
* структурное логирование каждого запроса (метод, путь, статус, latency);
* маппинг ошибок внешнего API в ExternalAPIError (наследник ServerException 502),
  чтобы непойманная ошибка превратилась в чистый ответ нашего API.

ГИБКАЯ АВТОРИЗАЦИЯ (любой формат). Внешние API бывают разные — без авторизации,
ключ в заголовке, ключ в query, Bearer, подпись запроса и т.п. Поэтому база НЕ
навязывает схему, а даёт две точки расширения:
* `default_headers()`  — статические заголовки на каждый запрос (синхронно);
* `prepare(method, url, options)` — асинхронный хук, можно дописать/поменять
  заголовки, query-параметры, тело, подписать запрос, обновить токен и т.д.

См. примеры наследников в app/clients/example.py.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from functools import lru_cache
from time import perf_counter
from typing import Any, TypeVar, overload

import httpx
from pydantic import TypeAdapter, ValidationError

from app.clients.response import ApiError, ApiResponse
from app.core.logging import get_logger
from app.exceptions.base import ServerException
from app.schemas.response import ErrorCode

# Тип ожидаемой модели ответа (для типизации возвращаемого значения)
M = TypeVar("M")


@lru_cache(maxsize=512)
def _type_adapter(tp: Any) -> TypeAdapter[Any]:
    # TypeAdapter валидирует ЛЮБОЙ тип: Model, list[Model], dict[str, Model] и т.п.
    # Кэшируем по типу — построение адаптера не бесплатно (важно для hot paths).
    return TypeAdapter(tp)


class ExternalAPIError(ServerException):
    """
    Ошибка обращения к внешнему API. Наследник ServerException — если не поймать
    в сервисе, глобальный обработчик отдаст клиенту чистый 502 (а не сырой traceback).
    Несёт `upstream_status`/`upstream_body` — сервис может их разобрать и переосмыслить
    (например, upstream 404 превратить в свой NotFoundError).
    """

    def __init__(
        self,
        message: str,
        *,
        upstream_status: int | None = None,
        upstream_body: Any = None,
        url: str | None = None,
    ) -> None:
        super().__init__(status_code=502, message=message, code=ErrorCode.UNAVAILABLE)
        self.upstream_status = upstream_status
        self.upstream_body = upstream_body
        self.url = url


@dataclass(slots=True)
class RetryPolicy:
    attempts: int = 3  # всего попыток (1 = без ретраев)
    backoff_base: float = 0.2  # базовая задержка, сек
    backoff_max: float = 5.0  # потолок задержки, сек
    retry_statuses: frozenset[int] = field(
        default_factory=lambda: frozenset({429, 500, 502, 503, 504})
    )
    # Ретраим по умолчанию только идемпотентные методы (POST/PATCH опасно ретраить
    # без идемпотентности на стороне внешнего API).
    retry_methods: frozenset[str] = field(
        default_factory=lambda: frozenset({"GET", "HEAD", "PUT", "DELETE", "OPTIONS"})
    )


class BaseHTTPClient:
    # --- Класс-уровневые дефолты, переопределяемые в наследниках ---
    base_url: str | None = None
    service_name: str = "external"
    timeout: float = 10.0
    max_connections: int = 100
    max_keepalive: int = 20

    def __init__(
        self,
        base_url: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        retry: RetryPolicy | None = None,
        **httpx_kwargs: Any,
    ) -> None:
        self._retry = retry or RetryPolicy()
        self._log = get_logger(f"httpclient.{self.service_name}")
        self._client = httpx.AsyncClient(
            base_url=base_url or self.base_url or "",
            timeout=timeout or self.timeout,
            headers=headers or {},
            limits=httpx.Limits(
                max_connections=self.max_connections,
                max_keepalive_connections=self.max_keepalive,
            ),
            **httpx_kwargs,
        )

    # ------------------------------------------------------------------
    # Жизненный цикл
    # ------------------------------------------------------------------
    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> BaseHTTPClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Точки расширения для наследников (авторизация любого формата)
    # ------------------------------------------------------------------
    def default_headers(self) -> dict[str, str]:
        """Статические заголовки на каждый запрос. Переопределите при необходимости."""
        return {}

    async def prepare(self, method: str, url: str, options: dict[str, Any]) -> None:
        """
        Асинхронный хук перед запросом. Мутируйте `options` на месте: добавьте
        заголовки (`options["headers"]`), query (`options.setdefault("params", {})`),
        подпишите запрос, обновите токен и т.п. По умолчанию ничего не делает.
        """

    # ------------------------------------------------------------------
    # Ядро
    # ------------------------------------------------------------------
    @overload
    async def request(
        self,
        method: str,
        url: str,
        *,
        model: type[M],
        parse_json: bool = ...,
        retry: RetryPolicy | None = ...,
        **options: Any,
    ) -> M: ...

    @overload
    async def request(
        self,
        method: str,
        url: str,
        *,
        model: None = ...,
        parse_json: bool = ...,
        retry: RetryPolicy | None = ...,
        **options: Any,
    ) -> Any: ...

    async def request(
        self,
        method: str,
        url: str,
        *,
        model: Any = None,
        parse_json: bool = True,
        retry: RetryPolicy | None = None,
        **options: Any,
    ) -> Any:
        method = method.upper()
        policy = retry or self._retry

        # Слияние заголовков: статические дефолты + переданные в вызове
        options["headers"] = {**self.default_headers(), **(options.pop("headers", None) or {})}
        await self.prepare(method, url, options)

        can_retry = method in policy.retry_methods

        attempt = 0
        while True:
            attempt += 1
            start = perf_counter()
            try:
                resp = await self._client.request(method, url, **options)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                latency = round((perf_counter() - start) * 1000, 2)
                self._log.warning(
                    "http_error",
                    method=method,
                    url=url,
                    error=str(exc),
                    attempt=attempt,
                    latency_ms=latency,
                )
                if can_retry and attempt < policy.attempts:
                    await self._backoff(attempt, policy)
                    continue
                raise ExternalAPIError(
                    f"{self.service_name} request failed: {exc}", url=url
                ) from exc

            latency = round((perf_counter() - start) * 1000, 2)

            # Ретраим по «временным» статусам
            if (
                resp.status_code in policy.retry_statuses
                and can_retry
                and attempt < policy.attempts
            ):
                self._log.warning(
                    "http_retry",
                    method=method,
                    url=url,
                    status=resp.status_code,
                    attempt=attempt,
                    latency_ms=latency,
                )
                await self._backoff(attempt, policy, resp)
                continue

            self._log.info(
                "http_request",
                method=method,
                url=url,
                status=resp.status_code,
                latency_ms=latency,
            )

            if resp.status_code >= 400:
                raise ExternalAPIError(
                    f"{self.service_name} returned {resp.status_code}",
                    upstream_status=resp.status_code,
                    upstream_body=self._safe_body(resp),
                    url=url,
                )

            # model задан -> всегда парсим JSON и валидируем в Pydantic
            if model is not None:
                data = resp.json() if resp.content else None
                try:
                    return _type_adapter(model).validate_python(data)
                except ValidationError as exc:
                    self._log.warning(
                        "http_schema_mismatch",
                        method=method,
                        url=url,
                        errors=exc.error_count(),
                    )
                    raise ExternalAPIError(
                        f"{self.service_name} response schema mismatch",
                        upstream_status=resp.status_code,
                        upstream_body=resp.json() if resp.content else None,
                        url=url,
                    ) from exc

            if not parse_json:
                return resp
            return resp.json() if resp.content else None

    # --- Удобные обёртки (с сохранением типа модели) ---
    @overload
    async def get(self, url: str, *, model: type[M], **kw: Any) -> M: ...
    @overload
    async def get(self, url: str, *, model: None = ..., **kw: Any) -> Any: ...
    async def get(self, url: str, *, model: Any = None, **kw: Any) -> Any:
        return await self.request("GET", url, model=model, **kw)

    @overload
    async def post(self, url: str, *, model: type[M], **kw: Any) -> M: ...
    @overload
    async def post(self, url: str, *, model: None = ..., **kw: Any) -> Any: ...
    async def post(self, url: str, *, model: Any = None, **kw: Any) -> Any:
        return await self.request("POST", url, model=model, **kw)

    @overload
    async def put(self, url: str, *, model: type[M], **kw: Any) -> M: ...
    @overload
    async def put(self, url: str, *, model: None = ..., **kw: Any) -> Any: ...
    async def put(self, url: str, *, model: Any = None, **kw: Any) -> Any:
        return await self.request("PUT", url, model=model, **kw)

    @overload
    async def patch(self, url: str, *, model: type[M], **kw: Any) -> M: ...
    @overload
    async def patch(self, url: str, *, model: None = ..., **kw: Any) -> Any: ...
    async def patch(self, url: str, *, model: Any = None, **kw: Any) -> Any:
        return await self.request("PATCH", url, model=model, **kw)

    @overload
    async def delete(self, url: str, *, model: type[M], **kw: Any) -> M: ...
    @overload
    async def delete(self, url: str, *, model: None = ..., **kw: Any) -> Any: ...
    async def delete(self, url: str, *, model: Any = None, **kw: Any) -> Any:
        return await self.request("DELETE", url, model=model, **kw)

    # ------------------------------------------------------------------
    # Единый контракт: ApiResponse[T] (не бросает — кладёт ошибку в конверт)
    # ------------------------------------------------------------------
    @overload
    async def call(self, method: str, url: str, *, model: type[M], **kw: Any) -> ApiResponse[M]: ...
    @overload
    async def call(
        self, method: str, url: str, *, model: None = ..., **kw: Any
    ) -> ApiResponse[Any]: ...
    async def call(
        self, method: str, url: str, *, model: Any = None, **kw: Any
    ) -> ApiResponse[Any]:
        """
        Выполнить запрос и вернуть ЕДИНЫЙ конверт ApiResponse[T].
        Успех -> status=true, data=<провалидированный результат>.
        Ошибка внешнего API -> status=false, error=<детали> (исключение не бросается).
        """
        try:
            data = await self.request(method, url, model=model, **kw)
            return ApiResponse(status=True, data=data)
        except ExternalAPIError as exc:
            return ApiResponse(
                status=False,
                error=ApiError(
                    message=exc.message,
                    upstream_status=exc.upstream_status,
                    upstream_body=exc.upstream_body,
                ),
            )

    # ------------------------------------------------------------------
    @staticmethod
    def _safe_body(resp: httpx.Response) -> Any:
        # Тело ошибки: JSON если можем, иначе обрезанный текст
        try:
            return resp.json()
        except Exception:
            return resp.text[:500]

    async def _backoff(
        self,
        attempt: int,
        policy: RetryPolicy,
        resp: httpx.Response | None = None,
    ) -> None:
        # Уважаем Retry-After от сервера, если прислан
        if resp is not None:
            retry_after = resp.headers.get("retry-after")
            if retry_after and retry_after.isdigit():
                await asyncio.sleep(min(int(retry_after), policy.backoff_max))
                return
        delay = min(policy.backoff_base * 2 ** (attempt - 1), policy.backoff_max)
        # Детерминированный джиттер (без random) разводит «громовое стадо» ретраев
        jitter = delay * 0.1 * (attempt % 3)
        await asyncio.sleep(delay + jitter)
