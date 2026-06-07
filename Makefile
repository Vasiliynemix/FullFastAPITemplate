# ============================================================
# Makefile — основные команды разработки и эксплуатации
# ============================================================
.DEFAULT_GOAL := help
SHELL := /bin/bash

COMPOSE      := docker compose
COMPOSE_PROD := docker compose -f docker-compose.yml -f docker-compose.prod.yml

.PHONY: help
help: ## Показать список команд
	@grep -E '^[a-zA-Z0-9_.-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ---------------- Локальная разработка (без Docker) ----------------
.PHONY: install
install: ## Установить зависимости через uv (dev-группа)
	uv venv && uv pip install -e ".[dev]"

.PHONY: dev
dev: ## Запустить uvicorn с автоперезагрузкой
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

.PHONY: prod-local
prod-local: ## Запустить gunicorn локально (как в проде)
	uv run gunicorn app.main:app -c gunicorn.conf.py

.PHONY: worker
worker: ## Запустить воркер периодических задач локально (отдельный процесс)
	uv run python -m app.worker

# ---------------- Качество кода ----------------
.PHONY: lint
lint: ## Линт (ruff check)
	uv run ruff check app tests

.PHONY: format
format: ## Форматирование (ruff format + автофиксы)
	uv run ruff format app tests
	uv run ruff check --fix app tests

.PHONY: typecheck
typecheck: ## Статическая проверка типов (mypy)
	uv run mypy

.PHONY: hooks
hooks: ## Установить git-хуки pre-commit
	uv run pre-commit install

.PHONY: pre-commit
pre-commit: ## Прогнать все pre-commit хуки по всем файлам
	uv run pre-commit run --all-files

.PHONY: test
test: ## Прогнать тесты
	uv run pytest

.PHONY: test-perf
test-perf: ## Только перформанс-тесты
	uv run pytest -m perf

.PHONY: cov
cov: ## Тесты с покрытием
	uv run pytest --cov=app --cov-report=term-missing

# ---------------- Миграции ----------------
.PHONY: migration
migration: ## Создать автогенерируемую миграцию: make migration m="msg"
	uv run alembic revision --autogenerate -m "$(m)"

.PHONY: migrate
migrate: ## Применить миграции до head
	uv run alembic upgrade head

.PHONY: downgrade
downgrade: ## Откатить на одну миграцию
	uv run alembic downgrade -1

# ---------------- Docker (dev) ----------------
.PHONY: up
up: ## Поднять dev-инфраструктуру в Docker (всё)
	$(COMPOSE) up -d --build

.PHONY: up-deps
up-deps: ## Поднять ТОЛЬКО postgres+redis (для локального запуска через uv, вариант B)
	$(COMPOSE) up -d postgres redis

.PHONY: down
down: ## Остановить dev-инфраструктуру
	$(COMPOSE) down

.PHONY: build
build: ## Собрать образы
	$(COMPOSE) build

.PHONY: logs
logs: ## Логи бэкенда из stdout (follow)
	$(COMPOSE) logs -f backend

.PHONY: logs-file
logs-file: ## Хвост файлового JSON-лога с хоста (prod, bind-mount ./logs)
	tail -f logs/app.log

.PHONY: worker-logs
worker-logs: ## Логи воркера периодических задач (follow)
	$(COMPOSE) logs -f worker

.PHONY: ps
ps: ## Статус контейнеров
	$(COMPOSE) ps

.PHONY: docker-migrate
docker-migrate: ## Применить миграции внутри контейнера
	$(COMPOSE) exec backend alembic upgrade head

# ---------------- Docker (prod) ----------------
# Боевые тома (pgdata, certbot_conf) объявлены external — compose их не создаёт.
# Создаём заранее (идемпотентно), чтобы prod поднимался и на чистом сервере.
.PHONY: prod-init-volumes
prod-init-volumes: ## Создать external-тома prod (БД, сертификаты). Идемпотентно
	@docker volume create fastapi_pgdata >/dev/null && echo "  ✔ fastapi_pgdata"
	@docker volume create fastapi_certbot_conf >/dev/null && echo "  ✔ fastapi_certbot_conf"

.PHONY: prod-up
prod-up: prod-init-volumes ## Поднять/обновить prod-стек (пересобирает изменённое, применяет изменения)
	$(COMPOSE_PROD) up -d --build

.PHONY: prod-rebuild
prod-rebuild: prod-init-volumes ## Применить изменения КОДА: форс-пересборка образов + пересоздание контейнеров
	$(COMPOSE_PROD) up -d --build --force-recreate

.PHONY: prod-restart
prod-restart: prod-init-volumes ## Перезапуск БЕЗ пересборки (например после правки .env/compose)
	$(COMPOSE_PROD) up -d --force-recreate

.PHONY: prod-down
prod-down: ## Остановить prod-стек (тома и данные СОХРАНЯЮТСЯ — без -v)
	$(COMPOSE_PROD) down

.PHONY: prod-ps
prod-ps: ## Статус контейнеров prod-стека
	$(COMPOSE_PROD) ps

.PHONY: prod-logs
prod-logs: ## Логи prod-стека (follow). Сервис: make prod-logs s=worker (по умолч. backend)
	$(COMPOSE_PROD) logs -f $(or $(s),backend)

.PHONY: prod-scale
prod-scale: ## Масштабировать backend: make prod-scale n=5
	$(COMPOSE_PROD) up -d --scale backend=$(n) backend

# Намеренное удаление БД/сертификатов. external-тома compose сам не сносит, поэтому
# удаляем их явно — но ТОЛЬКО после ввода случайного кода + 'y'. Защита от опечатки.
.PHONY: prod-destroy
prod-destroy: ## ☠️  ОПАСНО: снести prod-стек ВМЕСТЕ С БД и сертификатами (с подтверждением кодом)
	@code=$$(LC_ALL=C tr -dc 'A-Z0-9' </dev/urandom | head -c 6); \
	echo ""; \
	echo "  ============================================================"; \
	echo "  ☠️  БЕЗВОЗВРАТНОЕ удаление ДАННЫХ ПРОДА"; \
	echo "  Будут удалены контейнеры И ТОМА:"; \
	echo "    • fastapi_pgdata        (вся база PostgreSQL)"; \
	echo "    • fastapi_certbot_conf  (Let's Encrypt сертификаты)"; \
	echo "    • прочие тома стека (брокер и т.п.)"; \
	echo "  ============================================================"; \
	echo ""; \
	echo "  Для подтверждения введите код: $$code"; \
	read -r -p "  Код> " ans; \
	if [ "$$ans" != "$$code" ]; then echo "  ✋ Код не совпал — отменено, ничего не удалено."; exit 1; fi; \
	read -r -p "  Точно удалить ВСЁ? введите 'y': " yn; \
	if [ "$$yn" != "y" ]; then echo "  ✋ Отменено."; exit 1; fi; \
	echo "  💥 Удаляю..."; \
	$(COMPOSE_PROD) down -v; \
	docker volume rm -f fastapi_pgdata fastapi_certbot_conf >/dev/null 2>&1 || true; \
	echo "  ✔ Готово. Тома удалены."

# ---------------- PgBouncer (prod) ----------------
# Админ-консоль pgbouncer = спец-БД "pgbouncer". Юзер/пароль берём из env контейнера.
.PHONY: pgbouncer-pools
pgbouncer-pools: ## PgBouncer: активные/ждущие соединения по пулам (SHOW POOLS)
	$(COMPOSE_PROD) exec -T pgbouncer sh -c 'PGPASSWORD=$$DB_PASSWORD psql -h 127.0.0.1 -p 6432 -U $$DB_USER pgbouncer -c "SHOW POOLS"'

.PHONY: pgbouncer-stats
pgbouncer-stats: ## PgBouncer: статистика запросов/трафика (SHOW STATS)
	$(COMPOSE_PROD) exec -T pgbouncer sh -c 'PGPASSWORD=$$DB_PASSWORD psql -h 127.0.0.1 -p 6432 -U $$DB_USER pgbouncer -c "SHOW STATS"'

.PHONY: pgbouncer-clients
pgbouncer-clients: ## PgBouncer: текущие клиентские соединения (SHOW CLIENTS)
	$(COMPOSE_PROD) exec -T pgbouncer sh -c 'PGPASSWORD=$$DB_PASSWORD psql -h 127.0.0.1 -p 6432 -U $$DB_USER pgbouncer -c "SHOW CLIENTS"'

.PHONY: db-conns
db-conns: ## Сколько соединений видит PostgreSQL (с PgBouncer ~DEFAULT_POOL_SIZE, а не сотни)
	$(COMPOSE_PROD) exec -T postgres sh -c 'psql -U $$POSTGRES_USER -d $$POSTGRES_DB -c "SELECT count(*) FROM pg_stat_activity;"'

# ---------------- Нагрузочный тест (k6) ----------------
# Гоняет loadtest/k6.js через образ grafana/k6 в сети prod-стека (достукивается до
# nginx по внутреннему DNS). Параметры: mode= vus= dur= url= spread= api_key=
#   make loadtest                      # smoke+traffic+ratelimit через nginx
#   make loadtest mode=smoke           # только пройтись по всем ручкам
#   make loadtest mode=traffic vus=200 dur=60
#   make loadtest mode=ratelimit       # доказать app-лимит (429)
#   make loadtest url=http://backend:8000   # МИМО nginx — настоящая ёмкость бэкенда
NET ?= fastapi_default
# Пин генератора k6 на отдельные ядра (gen_cpuset=) => не отбирает CPU у сервера
# («будто с другой машины»). По умолчанию не пинуем. Пример: gen_cpuset=10-11
.PHONY: loadtest
loadtest: ## Нагрузочный тест k6. Параметры: mode= vus= dur= url= spread= gen_cpuset= (см. README)
	docker run --rm --network $(NET) \
		$(if $(gen_cpuset),--cpuset-cpus $(gen_cpuset),) \
		-v $(PWD)/loadtest:/scripts:ro \
		-e BASE_URL=$(or $(url),https://nginx) \
		-e MODE=$(or $(mode),all) \
		-e VUS=$(or $(vus),50) \
		-e DURATION=$(or $(dur),30) \
		-e SPREAD_IPS=$(or $(spread),true) \
		-e API_KEY=$(or $(api_key),) \
		grafana/k6 run /scripts/k6.js

# ---------------- Симуляция «другой машины» (resource limits) ----------------
# Урезаем app-tier (backend+worker) по CPU/RAM ВЖИВУЮ через docker update, без
# пересборки. Postgres/Redis оставляем свободными — в реале БД обычно на отд. хосте.
# Параметры: cpuset=0-3 (какие ядра) mem=2g (лимит памяти). sim-reset — снять лимиты.
.PHONY: sim-limit
sim-limit: ## Симулировать сервер: урезать backend по ядрам/RAM. Пример: make sim-limit cpuset=0-3 mem=2g
	@ids=$$($(COMPOSE_PROD) ps -q backend worker); \
	for id in $$ids; do \
		docker update --cpuset-cpus "$(or $(cpuset),0-3)" --memory "$(or $(mem),2g)" --memory-swap "$(or $(mem),2g)" $$id >/dev/null \
			&& echo "  limited $$(docker inspect -f '{{.Name}}' $$id | sed 's,^/,,')  ->  cpus=$(or $(cpuset),0-3) mem=$(or $(mem),2g)"; \
	done

.PHONY: sim-reset
sim-reset: ## Снять лимиты с backend/worker (пересоздаёт из compose — лимитов там нет)
	$(COMPOSE_PROD) up -d --force-recreate --no-build backend worker
	@echo "  лимиты сняты (контейнеры пересозданы из compose)"

# ---------------- SSL ----------------
.PHONY: ssl-init
ssl-init: ## Выпустить SSL: make ssl-init SERVER_NAME=example.com CERTBOT_EMAIL=you@example.com
	SERVER_NAME=$(SERVER_NAME) CERTBOT_EMAIL=$(CERTBOT_EMAIL) bash docker/certbot/init-letsencrypt.sh
