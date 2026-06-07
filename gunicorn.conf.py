"""
Gunicorn — продакшн-конфиг под высокую нагрузку.

СТРАТЕГИЯ МАСШТАБИРОВАНИЯ ВОРКЕРОВ
----------------------------------
ДВА УРОВНЯ параллелизма (не путать!):
  1) Gunicorn-воркеры — процессы ВНУТРИ одного контейнера (этот файл).
  2) replicas — несколько контейнеров (docker-compose.prod.yml -> deploy.replicas).
  Итог = replicas × workers. Пример: 3 реплики × 3 воркера = 9 процессов.

* worker_class = UvicornWorker — каждый воркер это asyncio event loop (uvloop +
  httptools). Один async-воркер держит МНОГО одновременных IO-bound запросов,
  поэтому процессов на ядро нужно немного: формула CPU+1 (а НЕ (2*CPU)+1 — та для
  СИНХРОННЫХ воркеров). В проде число воркеров задаём ЯВНО через WEB_CONCURRENCY,
  т.к. cpu_count() видит ядра хоста, а не cgroup-лимит контейнера.
* Реальный потолок RPS определяется не воркерами, а downstream-ресурсами (пул PG,
  Redis). ВАЖНО (preload_app=False -> у каждого воркера свой пул):
      replicas × workers × (db_pool_size + db_max_overflow) <= postgres.max_connections
  Дефолт: 3 × 3 × (20 + 10) = 270 <= 300. При росте реплик/воркеров — поднимайте
  max_connections PostgreSQL или ставьте PgBouncer перед БД.
* Горизонтальное масштабирование репликами предпочтительнее бесконечного роста
  воркеров на одной машине: лучше изоляция отказов (упал контейнер — остальные живы),
  приложение stateless — это безопасно.

НАДЁЖНОСТЬ
----------
* max_requests + jitter — периодический рестарт воркера борется с утечками памяти.
* graceful_timeout — даём текущим запросам завершиться при деплое/рестарте.
* preload_app=False — каждый воркер сам создаёт пулы (БД/Redis), не делим сокеты
  между процессами после fork.
"""

from __future__ import annotations

from app.core.config import settings

# --- Сеть ---
bind = f"{settings.host}:{settings.port}"

# --- Воркеры ---
workers = settings.workers
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000

# --- Тайминги ---
timeout = settings.gunicorn_timeout
graceful_timeout = settings.gunicorn_graceful_timeout
keepalive = settings.gunicorn_keepalive

# --- Анти-утечки: периодический рестарт воркеров ---
max_requests = 10000
max_requests_jitter = 1000

# --- Прочее ---
preload_app = False
# Логи отдаём structlog'у; gunicorn — только access на stdout при необходимости
accesslog = "-" if settings.debug else None
errorlog = "-"
loglevel = settings.log_level.lower()


def on_starting(server) -> None:  # noqa: ANN001, ARG001
    pass
