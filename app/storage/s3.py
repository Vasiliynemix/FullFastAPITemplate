"""
S3-совместимое хранилище (aioboto3).

Работает с любым S3-совместимым бэкендом: AWS S3, MinIO, Yandex Object Storage, Ceph
и т.п. — выбор задаётся endpoint_url (для AWS оставить пустым). aioboto3 — async-обёртка
над boto3; клиент живёт долго (открывается в connect(), закрывается в close()).

Ключевые приёмы под нагрузку:
* stream() — потоковое чтение тела ответа (не тянем весь объект в память);
* presigned_url() — клиент скачивает/заливает напрямую из S3, минуя наш бэкенд
  (offload трафика). Если задан S3_PUBLIC_URL (публичный бакет/CDN) — отдаём прямую ссылку.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.storage.base import AbstractStorage, ObjectNotFoundError

logger = get_logger("storage.s3")

# Коды ошибок botocore, означающие «объекта нет»
_NOT_FOUND_CODES = {"NoSuchKey", "NoSuchBucket", "404"}


class S3Storage(AbstractStorage):
    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None,
        region: str,
        access_key: str,
        secret_key: str,
        use_ssl: bool,
        public_url: str = "",
        default_expire: int = 3600,
    ) -> None:
        self._bucket = bucket
        self._endpoint_url = endpoint_url or None
        self._region = region
        self._access_key = access_key
        self._secret_key = secret_key
        self._use_ssl = use_ssl
        self._public_url = public_url.rstrip("/")
        self._default_expire = default_expire
        self._stack: AsyncExitStack | None = None
        self._client: Any = None

    async def connect(self) -> None:
        import aioboto3

        self._stack = AsyncExitStack()
        session = aioboto3.Session()
        self._client = await self._stack.enter_async_context(
            session.client(
                "s3",
                endpoint_url=self._endpoint_url,
                region_name=self._region,
                aws_access_key_id=self._access_key or None,
                aws_secret_access_key=self._secret_key or None,
                use_ssl=self._use_ssl,
            )
        )
        logger.info("storage_connected", backend="s3", bucket=self._bucket)

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self._client = None
            logger.info("storage_disconnected", backend="s3")

    def _c(self) -> Any:
        if self._client is None:
            raise RuntimeError("S3Storage не подключён (вызовите connect() в lifespan)")
        return self._client

    async def healthcheck(self) -> bool:
        # head_bucket — дешёвая проверка доступности бакета/кредов
        try:
            await self._c().head_bucket(Bucket=self._bucket)
            return True
        except Exception:
            return False

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        from botocore.exceptions import ClientError

        if isinstance(exc, ClientError):
            return exc.response.get("Error", {}).get("Code") in _NOT_FOUND_CODES
        return False

    async def put(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
        params: dict[str, Any] = {"Bucket": self._bucket, "Key": key, "Body": data}
        if content_type:
            params["ContentType"] = content_type
        await self._c().put_object(**params)

    async def get(self, key: str) -> bytes:
        try:
            resp = await self._c().get_object(Bucket=self._bucket, Key=key)
            async with resp["Body"] as body:
                return await body.read()
        except Exception as exc:
            if self._is_not_found(exc):
                raise ObjectNotFoundError(key) from exc
            raise

    async def stream(self, key: str, *, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
        try:
            resp = await self._c().get_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            if self._is_not_found(exc):
                raise ObjectNotFoundError(key) from exc
            raise
        async with resp["Body"] as body:
            async for chunk in body.iter_chunks(chunk_size):
                yield chunk

    async def delete(self, key: str) -> None:
        # S3 delete отсутствующего ключа не ошибка — идемпотентно
        await self._c().delete_object(Bucket=self._bucket, Key=key)

    async def exists(self, key: str) -> bool:
        try:
            await self._c().head_object(Bucket=self._bucket, Key=key)
            return True
        except Exception as exc:
            if self._is_not_found(exc):
                return False
            raise

    async def list(self, prefix: str = "") -> list[str]:
        keys: list[str] = []
        paginator = self._c().get_paginator("list_objects_v2")
        async for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return keys

    async def presigned_url(self, key: str, *, expires: int = 3600, method: str = "GET") -> str:
        # Публичный бакет/CDN — отдаём прямую ссылку без подписи
        if self._public_url:
            return f"{self._public_url}/{key}"
        operation = "get_object" if method.upper() == "GET" else "put_object"
        return await self._c().generate_presigned_url(
            operation,
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires or self._default_expire,
        )


def build_s3_storage() -> S3Storage:
    return S3Storage(
        bucket=settings.s3_bucket,
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        access_key=settings.s3_access_key_id,
        secret_key=settings.s3_secret_access_key,
        use_ssl=settings.s3_use_ssl,
        public_url=settings.s3_public_url,
        default_expire=settings.storage_presign_expire,
    )
