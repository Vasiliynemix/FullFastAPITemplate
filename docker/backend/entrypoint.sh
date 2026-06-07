#!/usr/bin/env sh
# ============================================================
# Entrypoint бэкенда: фиксим права на тома, ждём БД, миграции, стартуем.
# RUN_MIGRATIONS=1 — применять alembic upgrade перед стартом (по умолчанию вкл).
# ============================================================
set -e

# Под root: чиним права на смонтированный каталог логов (bind-mount часто создаётся
# docker'ом как root) и роняем привилегии до app — дальше всё работает под app.
if [ "$(id -u)" = "0" ]; then
  mkdir -p /app/logs
  chown -R app:app /app/logs 2>/dev/null || true
  exec gosu app "$0" "$@"
fi

echo "[entrypoint] waiting for postgres ${POSTGRES_HOST}:${POSTGRES_PORT}..."
# Простой пинг порта без psql-клиента
until python -c "import socket,sys; s=socket.socket(); s.settimeout(1); \
  sys.exit(0) if not s.connect_ex(('${POSTGRES_HOST}', int('${POSTGRES_PORT}'))) else sys.exit(1)" 2>/dev/null; do
  echo "[entrypoint] postgres is unavailable - sleeping"
  sleep 1
done
echo "[entrypoint] postgres is up"

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "[entrypoint] running alembic migrations..."
  alembic upgrade head
fi

echo "[entrypoint] starting: $*"
exec "$@"
