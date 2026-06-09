# syntax=docker/dockerfile:1.7
# ============================================================
# Backend image — multi-stage, менеджер зависимостей uv
# ============================================================

# ---- builder: ставим зависимости в изолированный venv ----
FROM python:3.13-slim AS builder

# uv — быстрый установщик зависимостей (берём официальный бинарь)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Сначала только манифест — кэшируем слой зависимостей
COPY pyproject.toml ./
RUN uv venv /opt/venv && uv pip install --python /opt/venv/bin/python -r pyproject.toml

# ---- runtime: тонкий финальный образ ----
FROM python:3.13-slim AS runtime

# Непривилегированный пользователь
RUN groupadd -r app && useradd -r -g app app

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Системные зависимости: curl (healthcheck) + gosu (понижение привилегий в entrypoint
# после фикса прав на смонтированные тома, напр. ./logs)
RUN apt-get update && apt-get install -y --no-install-recommends curl gosu \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini gunicorn.conf.py pyproject.toml ./
COPY docker/backend/entrypoint.sh /entrypoint.sh
# /app/logs создаём заранее (для файловых логов); права на смонтированный том
# чинит entrypoint при старте. USER не фиксируем — контейнер стартует под root,
# entrypoint роняет привилегии до app через gosu.
RUN chmod +x /entrypoint.sh && mkdir -p /app/logs && chown -R app:app /app

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
# По умолчанию prod-запуск; в dev переопределяется в compose
CMD ["gunicorn", "app.main:app", "-c", "gunicorn.conf.py"]
