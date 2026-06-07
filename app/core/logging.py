"""
Структурированное логирование на structlog + stdlib (ProcessorFormatter).

Двойной вывод с РАЗНЫМ форматом на назначение:
* КОНСОЛЬ (stdout) — человекочитаемо (rich-tracebacks) либо JSON (флаг LOG_JSON).
* ФАЙЛ (опционально, LOG_FILE_ENABLED) — всегда JSON, с ротацией по размеру + gzip
  архивов + ограничением их числа (multi-worker-safe через concurrent-log-handler).

Зачем stdlib-слой: чтобы один и тот же лог уходил в несколько обработчиков с разным
форматированием, structlog рендерит не сам, а через stdlib ProcessorFormatter —
каждый handler рендерит по-своему. Сторонние логи (uvicorn и т.п.) тоже проходят
через наш форматтер (foreign_pre_chain).

request_id / trace_id подмешиваются из contextvars в КАЖДУЮ запись; orjson как
сериализатор JSON.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

import orjson
import structlog
from structlog.processors import CallsiteParameter

from app.core.config import settings
from app.core.context import get_request_id, get_trace_id

# Корень проекта — чтобы показывать путь к файлу относительно него (app/...),
# а не абсолютный путь до site-packages. logging.py = app/core/logging.py.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _orjson_dumps(obj: Any, *, default: Any) -> str:
    # structlog ожидает str — декодируем bytes из orjson
    return orjson.dumps(obj, default=default).decode()


def caller_location(pathname: str, lineno: int | None) -> str:
    """
    Форматирует место в коде как `app/services/user.py:95` — путь относительно
    корня проекта (короче и читабельнее абсолютного). Переиспользуется декоратором
    @logged, чтобы указывать на ОПРЕДЕЛЕНИЕ метода.
    """
    try:
        rel = os.path.relpath(pathname, _PROJECT_ROOT)
    except ValueError:  # другой диск/вне дерева — берём имя файла
        rel = os.path.basename(pathname)
    return f"{rel}:{lineno}" if lineno is not None else rel


def _format_caller(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Склеивает добавленные CallsiteParameterAdder поля pathname+lineno в одно
    компактное поле caller. Если caller уже задан явно (например декоратором
    @logged — он указывает на определение метода), не перетираем его.
    """
    pathname = event_dict.pop("pathname", None)
    lineno = event_dict.pop("lineno", None)
    if "caller" in event_dict:
        return event_dict
    if pathname is not None:
        event_dict["caller"] = caller_location(pathname, lineno)
    return event_dict


def _inject_context(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Процессор: добавляет request_id/trace_id и базовые поля сервиса."""
    rid = get_request_id()
    tid = get_trace_id()
    if rid:
        event_dict["request_id"] = rid
    if tid:
        event_dict["trace_id"] = tid
    event_dict.setdefault("service", settings.app_name)
    event_dict.setdefault("environment", settings.environment.value)
    return event_dict


def _console_renderer() -> list[structlog.typing.Processor]:
    """Финальные процессоры для КОНСОЛИ: pretty (rich) или JSON по флагу LOG_JSON."""
    if settings.log_json:
        return [
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(serializer=_orjson_dumps),
        ]
    # Человекочитаемая консоль: ру-формат времени + rich-traceback
    try:
        import rich  # noqa: F401

        exc_formatter: Any = structlog.dev.RichTracebackFormatter(width=120, show_locals=False)
    except ImportError:
        exc_formatter = structlog.dev.plain_traceback
    return [
        structlog.processors.TimeStamper(fmt="%d.%m.%Y %H:%M:%S UTC", utc=True),
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        structlog.dev.ConsoleRenderer(
            colors=True, pad_event=0, pad_level=False, exception_formatter=exc_formatter
        ),
    ]


def _file_renderer() -> list[structlog.typing.Processor]:
    """Финальные процессоры для ФАЙЛА: всегда JSON (ISO-время, структурный traceback)."""
    return [
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        structlog.processors.dict_tracebacks,
        structlog.processors.JSONRenderer(serializer=_orjson_dumps),
    ]


def _build_file_handler() -> logging.Handler:
    """Ротация по размеру + gzip архивов + лимит их числа; безопасно для multi-worker."""
    from concurrent_log_handler import ConcurrentRotatingFileHandler

    path = Path(settings.log_file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return ConcurrentRotatingFileHandler(
        str(path),
        maxBytes=settings.log_file_max_bytes,
        backupCount=settings.log_file_backup_count,
        use_gzip=True,  # архивы .gz
    )


def setup_logging() -> None:
    """Единая точка настройки логирования. Вызывается на старте приложения."""

    callsite = structlog.processors.CallsiteParameterAdder(
        parameters={CallsiteParameter.PATHNAME, CallsiteParameter.LINENO},
    )

    # Общая «пре-цепочка»: применяется и к нашим логам, и к сторонним (foreign_pre_chain).
    # Без таймстампа/рендера/трейсбека — это делает per-handler форматтер (разный формат).
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        callsite,
        _format_caller,  # type: ignore[list-item]  # сигнатура процессора шире структлоговой
        _inject_context,  # type: ignore[list-item]
        structlog.processors.StackInfoRenderer(),
    ]

    level = logging.getLevelNamesMapping().get(settings.log_level.upper(), logging.INFO)

    # structlog отдаёт записи в stdlib, а форматирует stdlib ProcessorFormatter
    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    # Консоль (stdout)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors, processors=_console_renderer()
        )
    )
    root.addHandler(console)

    # Файл (опционально) — JSON с ротацией
    if settings.log_file_enabled:
        file_handler = _build_file_handler()
        file_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                foreign_pre_chain=shared_processors, processors=_file_renderer()
            )
        )
        root.addHandler(file_handler)

    # Сторонние логгеры: свой request-лог уже есть, поэтому uvicorn.access глушим;
    # остальные пускаем в root (получат наш формат). sqlalchemy.engine — тоже тихо.
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").propagate = False
    for name in ("uvicorn", "uvicorn.error", "gunicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    logging.getLogger("sqlalchemy.engine").propagate = False


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Получить именованный логгер. Имя кладётся как поле `logger`."""
    return structlog.get_logger(name)  # type: ignore[return-value]
