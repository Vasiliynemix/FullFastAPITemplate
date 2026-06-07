"""
Демонстрация объектного хранилища (AbstractStorage): загрузка/скачивание файлов.

Показывает типовой сценарий работы с S3:
* upload  — принять файл, положить в хранилище под сгенерированным ключом;
* presigned URL — выдать клиенту ссылку на ПРЯМОЕ скачивание из S3 (offload трафика);
* download — потоковая отдача через наш бэкенд (для приватных объектов);
* delete  — удалить объект.

Ручки работают через абстракцию AbstractStorage и не зависят от конкретного бэкенда.
"""

from __future__ import annotations

import mimetypes
import uuid
from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, File, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.deps import StorageDep
from app.exceptions.base import NotFoundError
from app.schemas.response import EmptyResponse, SuccessResponse, empty, success
from app.storage.base import ObjectNotFoundError

router = APIRouter()


class FileUploaded(BaseModel):
    key: str  # ключ объекта в хранилище (сохраните его, чтобы потом скачать/удалить)
    url: str  # ссылка на скачивание (presigned для S3 / download-эндпоинт для local)
    size: int
    content_type: str | None = None


@router.post("", response_model=SuccessResponse[FileUploaded], status_code=status.HTTP_201_CREATED)
async def upload_file(
    storage: StorageDep,
    file: Annotated[UploadFile, File()],
) -> SuccessResponse[FileUploaded]:
    # Ключ генерируем сами (не доверяем имени файла клиента), сохраняя расширение.
    # ВНИМАНИЕ: для очень больших файлов используйте multipart upload S3, а не read() в память.
    suffix = PurePosixPath(file.filename or "").suffix
    key = f"uploads/{uuid.uuid4().hex}{suffix}"
    data = await file.read()
    await storage.put(key, data, content_type=file.content_type)

    url = await storage.presigned_url(key)
    return success(FileUploaded(key=key, url=url, size=len(data), content_type=file.content_type))


@router.get("/url/{key:path}", response_model=SuccessResponse[dict])
async def file_url(key: str, storage: StorageDep) -> SuccessResponse[dict]:
    """Presigned-ссылка на прямое скачивание (для S3 — минуя наш бэкенд)."""
    if not await storage.exists(key):
        raise NotFoundError("File not found")
    return success({"url": await storage.presigned_url(key)})


@router.get("/download/{key:path}")
async def download_file(key: str, storage: StorageDep) -> StreamingResponse:
    """Потоковая отдача файла через бэкенд (память O(chunk), а не O(файла))."""
    if not await storage.exists(key):
        raise NotFoundError("File not found")
    media_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
    return StreamingResponse(storage.stream(key), media_type=media_type)


@router.delete("/{key:path}", response_model=EmptyResponse)
async def delete_file(key: str, storage: StorageDep) -> EmptyResponse:
    try:
        await storage.delete(key)
    except ObjectNotFoundError:  # на всякий случай: delete идемпотентен, но не глотаем тихо
        raise NotFoundError("File not found") from None
    return empty()
