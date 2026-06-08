# High-Load FastAPI Backend Template

Production-grade, async-first FastAPI шаблон, спроектированный под горизонтальное
масштабирование и нагрузку **10k–100k RPS**. Не «стартер на коленке», а каркас с
реальными паттернами высоконагруженных систем: Repository / Unit of Work / Service
Layer, абстракции кэша и брокера, единый контракт ответов, структурное логирование,
Redis rate limiting, идемпотентность и полностью контейнеризованная инфраструктура
(включая Nginx и Certbot).

---

## 🚀 Стек

| Слой | Технология |
|------|-----------|
| Runtime | Python 3.13, uvloop, httptools |
| Web | FastAPI (async), Uvicorn (dev), Gunicorn + UvicornWorker (prod) |
| ORM | SQLAlchemy 2.x (async) + asyncpg, Alembic |
| Хранилища | PostgreSQL (тюнингованный пул), Redis (кэш / rate limit / pub-sub) |
| Брокер | Абстракция + memory / Kafka (aiokafka) / RabbitMQ (aio-pika) |
| События | Типизированный EventBus + **transactional outbox** (надёжная доставка) |
| Объектное хранилище | S3-совместимое (aioboto3): AWS S3 / MinIO / Yandex Object Storage |
| Инфра | Docker Compose, Nginx (reverse proxy + TLS), Certbot |
| Наблюдаемость | structlog (JSON в prod, human в dev) + Sentry (error tracking, опц.) |
| Tooling | uv, Ruff (lint+format), mypy, pytest, pre-commit, CI (GitHub Actions) |

---

## 🧱 Архитектура

```
                 ┌───────────── Nginx (TLS, rate-limit, gzip, LB) ──────────────┐
HTTPS ──────────►│  reverse proxy → upstream keepalive → backend:8000 (×N реплик) │
                 └───────────────────────────────────────────────────────────────┘
                                          │
        ┌─────────────────────────────────┴─────────────────────────────────┐
        │                         FastAPI app (ASGI)                          │
        │  middleware: Security → RequestContext(log+request_id) → RateLimit  │
        │             → CORS → router                                         │
        │                                                                      │
        │   API route  ──►  Service (бизнес-логика)  ──►  UnitOfWork           │
        │   (без логики)        │            │                 │              │
        │                       ▼            ▼                 ▼              │
        │                Cache(Redis)   Broker(abstr.)   Repository (DB only) │
        └──────────────────────────────────────────────────────────────────────┘
                  │                    │                     │
              Redis ◄──────────┐   Kafka/RabbitMQ        PostgreSQL (async pool)
              (cache/RL/idem)  └── (фоновая обработка)
```

Слои и их единственная ответственность:

- **API route** (`app/api`) — парсинг входа, вызов сервиса, оборачивание в единый ответ. **Никакой бизнес-логики.**
- **Service** (`app/services`) — бизнес-логика. Наружу бросает **только** `ServerException`.
- **Repository** (`app/repositories`) — **только** доступ к данным (ORM + raw SQL в hot paths). Геттеры умеют `for_update` (блокировки) и `options=[selectinload(...)]` (eager-load связей — обязателен в async, см. ниже).
- **Unit of Work** (`app/db/uow.py`) — одна транзакция/сессия на бизнес-операцию. Не синглтон: **новый UoW на каждую транзакцию** (per-request), репозитории создаются лениво и кэшируются на сессию (`uow.users` или `uow.repo(AnyRepo)`). См. [ADR-0008](docs/adr/0008-unit-of-work-lifecycle.md).
- **Cache** (`app/cache`) / **Broker** (`app/broker`) / **Storage** (`app/storage`) — абстракции, сервис не знает о Redis/Kafka/S3.
- **Outbox** (`app/outbox`, `app/models/outbox.py`) — событие пишется в БД в одной транзакции с данными; релей в воркере публикует его в брокер (at-least-once). См. [ADR-0002](docs/adr/0002-transactional-outbox.md).
- **HTTP-клиенты** (`app/clients`) — базовый класс для интеграций с внешними API (см. ниже).
- **Decorators** (`app/decorators`) — `retry`, `cached`, `logged`, `transactional`.

### Интеграции с внешними API

`BaseHTTPClient` ([app/clients/base.py](app/clients/base.py)) — общий async-клиент на httpx:
пул соединений (keep-alive), ретраи на временные ошибки (таймауты/5xx/429 с backoff и
учётом `Retry-After`), структурное логирование, маппинг ошибок внешнего API в
`ExternalAPIError` (наследник `ServerException` → непойманная ошибка станет чистым `502`).

**Готовые обёртки под авторизацию** ([app/clients/auth.py](app/clients/auth.py)) — наследуйте
нужную вместо ручной сборки (или комбинируйте миксины):

| Обёртка / миксин | Авторизация | Создание |
|---|---|---|
| `BearerHTTPClient` | Bearer-токен | `Cls(token=...)` |
| `ApiKeyHeaderHTTPClient` | ключ в заголовке | `Cls(api_key=..., api_key_header="X-Api-Key")` |
| `ApiKeyQueryHTTPClient` | ключ в query | `Cls(api_key=..., api_key_param="appid")` |
| `BasicAuthHTTPClient` | HTTP Basic | `Cls(username=..., password=...)` |
| `LoginTokenHTTPClient` | login/pass → token (+ перелогин на 401) | `Cls(username=..., password=...)` |

**Единый контракт ответа.** Метод `call()` возвращает обёртку `ApiResponse[T]`
(`status` + `data` + `error`) — любой клиент отдаёт твой конверт, результат внешнего API
лежит в `data`. Ошибки не бросаются, а кладутся в `error` со `status=false`:

```python
from pydantic import BaseModel
from app.clients.auth import BearerHTTPClient
from app.clients.response import ApiResponse

class Order(BaseModel):
    id: str
    status: str

class PartnerAPI(BearerHTTPClient):           # готовая обёртка под Bearer
    base_url = "https://partner.example/api"
    service_name = "partner"

    async def create_order(self, payload: dict) -> ApiResponse[Order]:
        # call() -> ApiResponse[Order]: валидация в data, ошибки в error (не бросает)
        return await self.call("POST", "/orders", json=payload, model=Order)

async with PartnerAPI(token="...") as api:    # один клиент на процесс; закрывать на shutdown
    res = await api.create_order({...})
    if res.status:
        order = res.data                      # тип: Order
    else:
        log.warning("partner failed", code=res.error.upstream_status)
```

- `model=` валидирует ответ через `TypeAdapter` (любой тип: `Model`, `list[Model]`,
  `dict[...]`); тип `data` выводится через `TypeVar`/overload.
- Низкоуровневые `get/post/...` доступны, если нужна семантика «вернуть модель или
  бросить исключение» (без конверта).

**Внешний API со СВОИМ конвертом** (`{status, data}` / `{status:false, error_code, ...}`) —
подмешайте `EnvelopeMixin` ([app/clients/envelope.py](app/clients/envelope.py)) и зовите
`call_envelope()`: он развернёт чужой конверт в наш `ApiResponse[T]` (сохранив `error.code`).
Логика общая — в наследнике переопределяются только имена полей конверта (или хук), писать
её в каждом клиенте не нужно:

```python
class MessagesClient(EnvelopeMixin, ApiKeyHeaderHTTPClient):   # auth + распаковка конверта
    service_name = "messages"
    api_key_header = "X-Api-Key"
    # поля конверта совпадают с дефолтом (status/data/error_code/message) -> ничего не пишем

    async def get_status(self, task_id: str) -> ApiResponse[TaskStatus]:
        return await self.call_envelope("GET", f"/api/messages/{task_id}", data_model=TaskStatus)
```

Готовый клиент этого API — [app/clients/messages.py](app/clients/messages.py); примеры
auth-схем — [app/clients/example.py](app/clients/example.py).

### Брокер: типизированные события и консьюмеры

Тяжёлые сайд-эффекты (вызов внешнего API, рассылки) выносят из request lifecycle через
брокер: роут **публикует** событие и сразу отвечает, а **консьюмер** обрабатывает его в
фоне. Абстракция `AbstractBroker` ([app/broker/](app/broker/)) — `memory` (по умолчанию,
in-process), `kafka`, `rabbitmq` (выбор через `BROKER_TYPE`).

**Запуск брокера в Docker (опционально).** Контейнеры `kafka`/`rabbitmq` спрятаны за
профилями compose и стартуют только если активен профиль — задаётся через `COMPOSE_PROFILES`
в `.env` (его читает сам docker compose). Так `make up` / `make prod-up` поднимают нужный
брокер автоматически:

| Сценарий | `.env` |
|---|---|
| memory (дефолт) | `BROKER_TYPE=memory`, `COMPOSE_PROFILES=` |
| локальный Kafka | `BROKER_TYPE=kafka`, `BROKER_URL=kafka:9092`, `COMPOSE_PROFILES=kafka` |
| локальный RabbitMQ | `BROKER_TYPE=rabbitmq`, `BROKER_URL=amqp://guest:guest@rabbitmq:5672/`, `COMPOSE_PROFILES=rabbitmq` |
| **внешний** брокер (другой сервер) | `BROKER_TYPE=kafka`, `BROKER_URL=host:9092`, `COMPOSE_PROFILES=` (пусто — контейнер НЕ поднимаем) |

backend/worker ждут локальный брокер через `depends_on: required:false` — для внешнего
брокера зависимость игнорируется и контейнер не стартует. Kafka — KRaft (без Zookeeper),
RabbitMQ — с web-консолью на `:15672`. Кастомные Dockerfile не нужны (официальные образы).

Поверх брокера — **типобезопасный `EventBus`** (генерики + Pydantic, как у HTTP-клиента):
событие = модель с топиком, продюсер ТОЧНО знает поля payload, консьюмер получает уже
распарсенный объект, а не сырой dict.

```python
# Событие = контракт payload (app/broker/events.py -> Event):
class NotificationRequested(Event):
    topic: ClassVar[str] = "notifications.send"
    recipient_phone: str
    text: str
    markdown: bool = False

# Продюсер (роут) — типобезопасно, topic+payload берутся из события:
await bus.publish(NotificationRequested(recipient_phone="+7...", text="hi"))

# Консьюмер — получает ТИПИЗИРОВАННОЕ событие (а не Message):
async def handle_notification(event: NotificationRequested) -> None:
    await service.send(event.recipient_phone, event.text, markdown=event.markdown)

# Подписка — один раз на старте (app/consumers/__init__.py -> lifespan):
async def register_consumers(bus: EventBus) -> None:
    await bus.subscribe(NotificationRequested, handle_notification)
```

Рабочий пример из коробки: `POST /api/v1/notifications` (тело = событие
`NotificationRequested`) ставит уведомление в очередь (→ `202`), консьюмер отправляет его
через `NotificationService`. Сбой внешнего API при этом **не валит** запрос пользователя —
он изолирован в фоне (место для ретраев/DLQ). Битый payload не роняет консьюмера
(`ValidationError` логируется, handler не вызывается).

**Fan-out и группы потребителей.** `subscribe(..., group=...)` управляет доставкой (семантика
как у Kafka consumer group), одинаково для всех брокеров (memory/kafka/rabbitmq):

- **разные `group`** → каждый обработчик получает **каждое** событие (fan-out);
- **одинаковый `group`** → обработчики **делят** нагрузку (competing consumers, балансировка);
- без `group` → группа по умолчанию выводится из самой функции-обработчика → fan-out.

```python
async def register_consumers(bus: EventBus) -> None:
    # fan-out: одно событие UserCreated -> два независимых обработчика
    await bus.subscribe(UserCreated, handle_user_created_audit,   group="users-audit")    # метрика
    await bus.subscribe(UserCreated, handle_user_created_welcome, group="users-welcome")  # welcome
    # competing: при нескольких репликах одна группа -> отправка не дублируется
    await bus.subscribe(NotificationRequested, handle_notification, group="notifications")
```

Под капотом: in-memory — group→handlers + round-robin внутри группы; Kafka — `group` как
`group_id`; RabbitMQ — fanout-exchange на топик + отдельная очередь на группу. То есть
fan-out корректен и на распределённых брокерах, а не только in-memory.

---

## ⚡ Решения под высокую нагрузку

- **async-first везде** — никаких синхронных блокирующих вызовов в request lifecycle.
- **Пул PostgreSQL** тюнингуется (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `pool_pre_ping`, `pool_recycle`). Правило планирования: `pool_size * workers ≤ postgres.max_connections`.
- **Redis-кэш** на горячих путях (cache-aside) + `hiredis` C-парсер.
- **orjson** для (де)сериализации — все ответы через `ORJSONResponse`.
- **Минимальный ASGI-middleware** (без `BaseHTTPMiddleware`) — меньше оверхед.
- **structlog** с кэшированием логгеров и контекстом через `contextvars` (request_id/trace_id) — без блокировок.
- **Stateless-приложение** → горизонтальное масштабирование репликами за Nginx.
- **Raw SQL** разрешён в репозитории для самых горячих запросов (`UserRepository.search_raw`).
- **Streaming через генераторы** (`/users/stream/all`, NDJSON) — константная память на больших выборках.
- **Идемпотентность** (`Idempotency-Key`) и **распределённый rate limit** — многоярусный (минутная квота + burst/сек), atomic Lua в Redis.

---

## 📖 Документация API

- **Полный референс:** [docs/API.md](docs/API.md) — все ручки, контракт ответов, коды ошибок, авторизация, пагинация, стриминг, примеры curl.
- **Интерактивно:** Swagger UI на `/docs`, ReDoc на `/redoc`, схема — `/openapi.json`.
- **Авторизация в Swagger:** кнопка **Authorize** принимает Bearer-токен (JWT). Если включён `GLOBAL_API_KEY_ENABLED`, там же появляется поле `X-API-Key` — Swagger начнёт слать ключ на все запросы.

**Доступ к docs (важно).** Сами `/docs` и `/openapi.json` исключены из gate по `X-API-Key`
(иначе Swagger UI не загрузился бы), поэтому документация защищается **отдельно** — HTTP
Basic Auth:

| `DOCS_ENABLED` | `DOCS_BASIC_AUTH_USER/PASSWORD` | Среда | Доступ к /docs |
|---|---|---|---|
| `true` | пусто | dev | открыто |
| `true` | пусто | **prod** | **скрыто (404)** — безопасный дефолт |
| `true` | заданы | dev/prod | **Basic Auth** (логин/пароль из `.env`) |
| `false` | — | любая | отключено |

То есть в проде документация публикуется только если ты явно задал логин+пароль —
и тогда она за Basic Auth (видна только тебе, а не всему интернету). Полная схема API
не утекает наружу по умолчанию.

---

## 📡 Единый контракт ответов

Все ответы строго одинаковой формы (`app/schemas/response.py`):

```jsonc
// успех
{ "status": true,  "data": { /* T */ }, "meta": { "request_id": "…", "total": 42 } }
// ошибка (любая, включая валидацию и непредвиденные 500)
{ "status": false, "data": { "code": "not_found", "message": "User not found" },
  "meta": { "request_id": "…" } }
```

- `ServerResponse[T]` — generic-конверт; `SuccessResponse[T]` / `ErrorResponse`.
- `ErrorCode` — расширяемый enum машинных кодов.
- `ResponseMeta` — `request_id`, пагинация, произвольные `extra`.
- Глобальные хендлеры (`app/exceptions/handlers.py`) конвертируют **всё** — `ServerException`, `RequestValidationError`, `HTTPException`, любые `Exception` — в `ErrorResponse`. Сырые ошибки клиенту не утекают.

---

## 🔐 Аутентификация и авторизация

Шаблон даёт **два независимых контура защиты**, которые можно комбинировать.

### 1. JWT (access + refresh) с ролями и сессиями

- **access** — короткоживущий (`ACCESS_TOKEN_EXPIRE_MINUTES`, по умолчанию 15 мин), stateless, несёт `sub`, `role`, `sid` (id сессии). Проверяется без обращения к БД/Redis — дёшево под нагрузкой.
- **refresh** — долгоживущий (`REFRESH_TOKEN_EXPIRE_DAYS`), несёт `sid`+`jti`. На обновление — **ротация** `jti` внутри той же сессии + **детекция reuse** (повторный старый refresh → отзыв всей сессии).
- **Сессии** (`SessionStore`, Redis): каждая login создаёт сессию (`sid`). `sid` лежит и в access, и в refresh — поэтому выйти из текущей сессии можно по одному access-токену (refresh в теле не нужен).
- Пароли — argon2 (`pwdlib`).

Ручки (`/api/v1/auth`):

| Метод | Путь | Описание |
|------|------|----------|
| POST | `/auth/register` | регистрация (email, password, full_name, role?) |
| POST | `/auth/login` | новая сессия → `{access_token, refresh_token, expires_in}` |
| POST | `/auth/refresh` | ротация пары по refresh-токену (защита — самим refresh) |
| POST | `/auth/logout` | выйти из **текущей** сессии (требует access) → `{revoked}` |
| POST | `/auth/logout/all` | выйти из **всех** сессий (все устройства) → `{revoked}` |
| POST | `/auth/logout/others` | выйти из всех **кроме текущей** → `{revoked}` |
| GET  | `/auth/me` | принципал из токена: `id`, `role`, `sid` |

**Отзыв и удаление.** При `DELETE /users/{id}` все сессии пользователя отзываются (`revoke_all`).
После любого logout/удаления refresh мгновенно мёртв (новый access не получить); уже выданный
access живёт до истечения (минуты). Нужна **мгновенная** инвалидация access? Включите
`AUTH_VALIDATE_SESSION=true` — тогда каждый авторизованный запрос сверяет `sid` с Redis
(один `GET`), и отзыв действует сразу (ценой обращения к Redis на запрос).

Защита ручек — через зависимости (`app/api/deps.py`):

```python
from app.api.deps import CurrentUserDep, require_roles, require_at_least
from app.security.roles import Role

@router.get("/secret")
async def secret(user: CurrentUserDep): ...                       # любой авторизованный

@router.delete("/{id}", dependencies=[Depends(require_roles(Role.ADMIN))])
async def remove(...): ...                                        # точная роль

@router.post("/ops", dependencies=[Depends(require_at_least(Role.MANAGER))])
async def ops(...): ...                                           # роль не ниже (иерархия)
```

### Транспорт токена: header или cookie

Флаг `AUTH_TOKEN_TRANSPORT=header|cookie` — **что-то одно**, не оба сразу:

| Режим | Где токен | Для кого |
|---|---|---|
| `header` (по умолч.) | `Authorization: Bearer` | SPA/mobile; иммунен к CSRF (токен в JS) |
| `cookie` | HttpOnly-куки на login/refresh | браузерная сессия; токен недоступен JS (защита от XSS-кражи) |

В cookie-режиме `login`/`refresh` ставят `access_token` (Path=`/`) и `refresh_token`
(Path=`/api/v1/auth`) как HttpOnly + Secure + SameSite, `logout` их чистит, а авторизация
читает токен **только** из куки (заголовок игнорируется — и наоборот в header-режиме).

Ограничения (проверяются на старте — иначе приложение падает с понятной ошибкой):
- cookie работает только при `AUTH_JWT_ENABLED=true`;
- cookie **несовместим** с `GLOBAL_API_KEY_ENABLED=true` (браузеру негде хранить секретный
  ключ) — для браузерного режима глобальный gate должен быть выключен.

`SameSite=lax` (по умолч.) даёт базовую защиту от CSRF; для кросс-доменного фронта —
`none` + Secure + CSRF-токены. Подробнее — [ADR-0006](docs/adr/0006-auth-token-transport.md).

### Роли

Определены в **одном месте** — [app/security/roles.py](app/security/roles.py): `USER`, `MANAGER`, `ADMIN`, `SERVICE`.
Чтобы добавить/переименовать роль — правьте только этот файл (значение в `Role` + при
необходимости уровень в `_LEVELS` для иерархических проверок). Роль хранится строкой
в колонке `users.role`, дефолт — `user`.

### 2. Глобальный gate по одному API-ключу

Флаг в `.env`: `GLOBAL_API_KEY_ENABLED=true` + `GLOBAL_API_KEY=...`. Когда включён —
**весь API** (кроме `/health`, `/docs`, `/redoc`, `/openapi.json`) требует заголовок
`X-API-Key`. Это режим «сервис закрыт одним ключом» — доступ только доверенным продуктам
(service-to-service). Сравнение ключа — в постоянном времени.

### Режимы авторизации

Двумя флагами задаются **три** валидных режима (комбинация «оба выключены» запрещена —
сервис не должен остаться без защиты; такой конфиг падает на старте):

| Режим | `AUTH_JWT_ENABLED` | `GLOBAL_API_KEY_ENABLED` | Что нужно клиенту |
|---|---|---|---|
| **JWT + глобал** | `true` | `true` | `X-API-Key` **и** `Bearer` (на защищённых ручках) |
| **только JWT** | `true` | `false` | `Bearer` на защищённых ручках |
| **только глобал** | `false` | `true` | `X-API-Key` на всём (кроме health) |
| ~~ничего~~ | `false` | `false` | ❌ ошибка старта |

- В режиме **только глобал** JWT-зависимости не требуют токен и не проверяют роли:
  запрос уже прошёл gate по ключу, принципал — анонимный `service`. Ручки `/auth/*`
  (login/refresh/logout/me/sessions) **не регистрируются вовсе** — пользовательской
  авторизации в этом режиме нет (чистый service-to-service), их нет ни в API, ни в Swagger.
- Swagger отражает режим автоматически: в «Authorize» показываются ровно те схемы,
  что реально нужны (`X-API-Key` и/или `Bearer`), и требует их **вместе** там, где надо.

---

## 🔒 Защита от гонок (concurrency)

UoW даёт **атомарность** (всё-или-ничего), но не защищает от **lost update** в
read-modify-write (двое прочитали → оба записали → одно изменение потеряно). Шаблон даёт
оба классических механизма — выбираешь под контекст.

**Пессимистичная блокировка** — флаг на геттерах репозитория:

```python
async with uow:
    acc = await uow.accounts.get(acc_id, for_update=True)   # SELECT ... FOR UPDATE
    acc.balance -= 10                                        # конкуренты ждут commit
    await uow.commit()                                       # лок снят
```

`for_update` есть на `get` / `get_by` / `list`; плюс `skip_locked` (забрать пачку из
очереди, минуя занятые) и `nowait` (сразу падать, если занято). Только **внутри транзакции**;
не держать лок во время медленного I/O.

**Оптимистичная блокировка** — `VersionedMixin` (колонка `version_id`): наследуешь миксин,
и ORM сам добавляет `WHERE version_id = <прочитанная>` к каждому UPDATE и инкрементит её.
Проигравший гонку получает `StaleDataError` → сервис отдаёт **409 Conflict**. Применён к
`User` (см. `UserService.update`).

| | `for_update` (пессимистичная) | `VersionedMixin` (оптимистичная) |
|---|---|---|
| Механизм | замок строки в БД | счётчик версий, проверка при UPDATE |
| Держит лок | да (другие ждут) | нет |
| Когда лучше | конфликты **частые** (горячая строка) | конфликты **редкие** |
| Цена конфликта | ожидание | повтор операции (409) |

Подробнее и про выбор — [ADR-0005](docs/adr/0005-concurrency-control.md).

---

## 🔗 Связи и eager-load

В async **ленивая загрузка relationship запрещена**: обращение к связи вне сессии падает
с `MissingGreenlet`/`DetachedInstanceError`. Поэтому всё, что сериализуешь, грузи заранее —
геттеры репозитория принимают `options=`:

```python
acc = await uow.accounts.get(acc_id, options=[selectinload(Account.transactions)])
# вложенно («транзакции юзера»): профиль + счета + их транзакции одним набором запросов
user = await uow.users.get_overview(user_id)   # selectinload(User.accounts).selectinload(...)
```

Демо-домен **`accounts`** показывает все типы связей и eager-load в ответе API:

| Связь | Где |
|---|---|
| one-to-one | `User ↔ Profile` |
| one-to-many | `User → Accounts`, `Account → Transactions` |
| many-to-one | `Account → User`, `Transaction → Account` |
| many-to-many | `Transaction ↔ Category` (assoc-таблица) |

Ручки: `GET /accounts/{id}` (счёт + транзакции + категории), `GET /accounts/overview/{user_id}`
(профиль + счета + вложенные транзакции), `POST /accounts/{id}/deposit|withdraw` (под `for_update`).
Подробнее — [ADR-0007](docs/adr/0007-eager-loading.md).

---

## ⏰ Фоновые задачи (worker)

Периодические задачи (опрос внешнего сервиса, обслуживание, рассылки по расписанию)
выносятся в **отдельный процесс** — не в gunicorn/веб-воркеры. Это **тот же Docker-образ**,
но команда `python -m app.worker`, и запускается в **одном** экземпляре (без `replicas` —
задачи не должны дублироваться).

```
backend (gunicorn, N реплик)   — обслуживает HTTP
worker  (python -m app.worker, 1 шт) — крутит планировщик периодических задач
```

Планировщик ([app/scheduler/](app/scheduler/)) — лёгкий: интервальные **и cron**-задачи
(croniter), graceful shutdown по SIGTERM, ошибка задачи не роняет планировщик, опционально
Redis-лок (`single_instance`) — защита от пересечения/двойного запуска.

```python
# app/scheduler/jobs.py — добавить задачу = функция + строка в register_jobs:
async def poll_external_service() -> None:
    ...  # зовёшь свой клиент/сервис; внешний вызов в отдельном процессе

def register_jobs(scheduler: Scheduler) -> None:
    scheduler.add("heartbeat", heartbeat, interval=30, run_on_start=True)            # раз в 30с
    scheduler.add("poll_external", poll_external_service, interval=3600, single_instance=True)  # раз в час
    scheduler.add_cron("daily_report", daily_report, "0 3 * * *", single_instance=True)         # каждый день 03:00 UTC
```

Два вида расписания, добавляются одинаково просто:
- **интервал** — `add(name, func, interval=сек)` (отсчёт от старта процесса);
- **cron** — `add_cron(name, func, "мин час день месяц день_недели")` в UTC; выражение
  валидируется при регистрации (опечатка → ошибка на старте).

Запуск: локально — `make worker` (`uv run python -m app.worker`); в Docker — сервис
`worker` поднимается вместе со стеком (`make up` / `make prod-up`), логи — `make worker-logs`.
Сам процесс поднимает Redis/брокер/пул БД и аккуратно гасит их по сигналу.

---

## 🔧 Конфигурация

Все настройки — `app/core/config.py` (Pydantic Settings), читаются из `.env`.

```bash
cp .env.example .env   # отредактируйте секреты
```

DEV vs PROD:

| | DEV | PROD |
|---|---|---|
| Сервер | uvicorn `--reload` | gunicorn + UvicornWorker |
| Консоль | human-readable (rich-tracebacks) | human-readable (флаг `LOG_JSON`) |
| Файл-логи | выкл | JSON с ротацией (`LOG_FILE_ENABLED=true`) |
| Docs | `/docs`, `/redoc` | отключены |
| Воркеры | 1 | `WEB_CONCURRENCY` на контейнер × `replicas` (см. `gunicorn.conf.py`) |

### Флаги компонентов (не грузить лишнее)

Опциональную инфраструктуру можно полностью отключить — тогда она не подключается и
соответствующие ручки не регистрируются:

| Флаг | По умолч. | Эффект при выключении |
|---|---|---|
| `BROKER_ENABLED` | `true` | брокер/консьюмеры не поднимаются, `/notifications` и outbox-relay выключены |
| `STORAGE_ENABLED` | `false` | S3 не подключается, ручки `/files` не регистрируются |
| `OUTBOX_ENABLED` | `true` | релей не публикует события из outbox (нужен и `BROKER_ENABLED`) |
| `AUTH_JWT_ENABLED` / `GLOBAL_API_KEY_ENABLED` | см. выше | режимы авторизации |
| `AUTH_TOKEN_TRANSPORT` | `header` | `header` (Bearer) или `cookie` (HttpOnly) — что-то одно |
| `AUTH_VALIDATE_SESSION` | `false` | `true` => мгновенная инвалидация access (сверка `sid` в Redis) |
| `SENTRY_DSN` | пусто | Sentry включается только при заданном DSN |

PostgreSQL и Redis считаются базовыми (на них завязаны users/auth/cache/rate-limit) —
флага отключения у них намеренно нет.

### Логи: консоль + файл

Один лог уходит в два места с **разным форматом**:
- **Консоль (stdout)** — человекочитаемо с rich-tracebacks (или JSON при `LOG_JSON=true`).
  Доступна через `docker logs`; в проде поток ротируется драйвером `json-file` (`max-size`/`max-file`).
- **Файл** (если `LOG_FILE_ENABLED=true`) — всегда **JSON**, ротация по размеру
  (`LOG_FILE_MAX_BYTES`) + **gzip-архивы** + лимит их числа (`LOG_FILE_BACKUP_COUNT`,
  старые удаляются). Безопасно для нескольких воркеров gunicorn (файловый лок).

**Достать логи с хоста без `docker exec`:** в прод-compose каталог логов проброшен
bind-mount'ом `./logs:/app/logs`, поэтому файлы видны прямо на хосте:
```bash
tail -f logs/app.log                 # живой JSON-лог
ls logs/                             # app.log + app.log.1.gz, app.log.2.gz, ...
zcat logs/app.log.1.gz | jq .        # читать архив
```

> Время в файле — ISO 8601 UTC (по нему ищется любая дата через `grep`/`jq`); ротация
> по размеру + `backupCount` держит общий объём ограниченным (по умолчанию ~50MB × 15).

---

## 🏁 Быстрый старт

### Вариант A — всё в Docker (рекомендуется)

```bash
cp .env.example .env
make up            # backend + postgres + redis + nginx (+ авто-миграции)
make logs          # смотреть логи backend
# API:    http://localhost:8000/api/v1/health/live
# Через nginx: https://localhost/  (self-signed cert в dev)
make down
```

### Вариант B — локально через uv (приложение на хосте, БД/Redis в Docker)

```bash
make install       # uv venv + зависимости

# ВАЖНО: приложение на хосте обращается к контейнерам по localhost, а не по
# именам docker-сервисов. В .env поставьте:
#   POSTGRES_HOST=localhost
#   REDIS_HOST=localhost
# (порты 5432/6379 уже проброшены на хост в docker-compose.yml)
#
# Если 5432/6379 уже заняты локальными PostgreSQL/Redis — переопределите
# host-порты в .env и синхронизируйте порт подключения приложения:
#   POSTGRES_HOST_PORT=5433   и   POSTGRES_PORT=5433
#   REDIS_HOST_PORT=6380      и   REDIS_PORT=6380

make up-deps       # поднять ТОЛЬКО postgres + redis
make migrate       # накатить миграции (alembic upgrade head)
make dev           # uvicorn --reload на http://localhost:8000

# Проверка:
#   curl http://localhost:8000/api/v1/health/ready   # postgres/redis = ok
#   Swagger UI:  http://localhost:8000/docs           # тут удобно тестить ручки
```

> Брокер по умолчанию `BROKER_TYPE=memory` — внешний Kafka/RabbitMQ для локалки не нужен.

---

## 🗄️ Миграции (Alembic, async)

```bash
make migration m="add orders table"   # автогенерация
make migrate                          # применить до head
make downgrade                        # откат на шаг
# в Docker:
make docker-migrate
```

Начальная миграция таблицы `users` уже включена (`migrations/versions/0001_initial_users.py`).
URL и metadata берутся из приложения — единый источник правды.

---

## 🐳 Прод-развёртывание

```bash
# поднять прод-стек (gunicorn, 3 реплики backend, certbot renew loop, nginx reload loop)
make prod-up

# выпустить реальный SSL-сертификат Let's Encrypt
make ssl-init SERVER_NAME=example.com CERTBOT_EMAIL=you@example.com

# горизонтальное масштабирование
make prod-scale n=6
```

Nginx (`docker/nginx/`) — production-grade: HTTP→HTTPS редирект, TLS 1.2/1.3 hardening,
security-заголовки (HSTS/CSP/...), gzip, rate/conn limit, keepalive-пул к upstream,
ACME webroot для Certbot, отключение буферизации для стриминга. До выпуска реального
сертификата стартует на self-signed заглушке (контейнер не падает).

### Масштабирование: два уровня

Параллелизм складывается из двух независимых слоёв (не путать):

| Уровень | Чем управляется | Что даёт |
|---|---|---|
| **Gunicorn-воркеры** (внутри контейнера) | `WEB_CONCURRENCY` | процессы на event loop, recycling воркеров, graceful shutdown |
| **Replicas** (контейнеры) | `deploy.replicas` + nginx LB | изоляция отказов (упал контейнер — остальные живы), переезд в k8s |

Итог = `replicas × WEB_CONCURRENCY`. Дефолт прода: **3 × 3 = 9 воркеров**.

> Для `UvicornWorker` (async) воркеров на ядро нужно немного: формула `CPU+1`
> (не `2*CPU+1` — та для синхронных). В проде задавайте `WEB_CONCURRENCY` **явно** —
> `cpu_count()` видит ядра хоста, а не cgroup-лимит контейнера.

**Связь с пулом БД** (у каждого воркера свой пул):
`replicas × WEB_CONCURRENCY × (DB_POOL_SIZE + DB_MAX_OVERFLOW) ≤ postgres.max_connections`.
Без PgBouncer: `3 × 3 × (20+10) = 270 ≤ 300`.

### PgBouncer — пул соединений перед PostgreSQL (включён в проде)

PostgreSQL-соединение = отдельный процесс на сервере БД (дорого), отсюда жёсткий лимит
`max_connections`. **PgBouncer** стоит между приложением и PG и мультиплексирует сотни
клиентских соединений на маленький пул реальных (transaction pooling):

```
9 воркеров (270 «соединений»)  →  PgBouncer (MAX_CLIENT_CONN 1000)  →  PostgreSQL (25 реальных)
```

PG видит только `DEFAULT_POOL_SIZE` (25) соединений, **сколько бы воркеров/реплик ни было** —
можно масштабировать свободно. Прод-стек ([docker-compose.prod.yml](docker-compose.prod.yml))
уже поднимает сервис `pgbouncer`, а backend ходит в `pgbouncer:6432` (`DB_PGBOUNCER=true`).

> **Важно (transaction mode + asyncpg):** в этом режиме prepared statements asyncpg
> несовместимы (соединение к PG меняется между запросами), поэтому при `DB_PGBOUNCER=true`
> они отключаются автоматически ([app/db/session.py](app/db/session.py)). Это сознательный
> размен: чуть меньше микрооптимизации запросов ради кратно большей масштабируемости.

Включить в dev: подними сервис `pgbouncer` и поставь `DB_PGBOUNCER=true`,
`POSTGRES_HOST=pgbouncer`, `POSTGRES_PORT=6432`.

---

## 🧪 Тесты

```bash
make test          # все тесты (async SQLite + fakeredis + in-memory broker)
make test-perf     # только перформанс-санити (маркер perf)
make cov           # покрытие
```

Покрыты: репозитории, сервисы, auth (JWT/сессии/**куки**), **блокировки** (`for_update`
SQL + оптимистичная `version_id`), **связи + eager-load** (все 4 типа, accounts-домен),
**ленивый UoW** (кэш/реентерабельность), декораторы, HTTP-клиент, брокер и консьюмеры,
**outbox** (релей + at-least-once), **storage** (S3 + роутер /files), docs Basic Auth,
сериализация. БД — async SQLite, Redis — fakeredis, брокер — in-memory.

---

## 🧹 Качество кода

```bash
make lint          # ruff check
make format        # ruff format + автофиксы
make typecheck     # mypy
make hooks         # установить pre-commit хуки (ruff + mypy при коммите)
```

Строгая типизация, SOLID, OOP, async-first. Код — на английском, комментарии — на русском.

**CI** ([.github/workflows/ci.yml](.github/workflows/ci.yml)): на каждый PR — ruff (lint+format),
mypy, pytest и сборка backend-образа.

---

## 📁 Структура

```
app/
  api/            роуты (v1) + DI (deps.py) — без бизнес-логики
  core/           config, logging (structlog), lifespan, context
  schemas/        единый response-контракт + DTO
  exceptions/     ServerException + глобальные хендлеры
  models/         ORM-модели (User/Outbox + демо связей: Account/Transaction/Category/Profile)
  db/             async engine/session (пул), ленивый Unit of Work
  repositories/   Repository Pattern (ORM + raw SQL, for_update, eager-load options)
  services/       бизнес-логика (в т.ч. account: deposit/withdraw под FOR UPDATE)
  cache/          абстракция + Redis-реализация
  broker/         абстракция + memory/kafka/rabbitmq
  outbox/         transactional outbox: релей БД -> брокер (at-least-once)
  storage/        абстракция объектного хранилища + S3-реализация (aioboto3)
  consumers/      подписчики брокера (register_consumers в lifespan)
  scheduler/      планировщик периодических задач (для worker-процесса)
  worker.py       отдельный процесс периодических задач (python -m app.worker)
  clients/        базовый async HTTP-клиент для внешних API + примеры
  security/       JWT (access/refresh+sid), пароли (argon2), роли, SessionStore
  decorators/     retry, cached, logged, transactional
  ratelimit/      распределённый лимитер (Redis Lua)
  idempotency/    Idempotency-Key store (Redis)
  middleware/     request context/log, rate limit, api-key gate, security headers
docker/           backend/nginx/certbot Dockerfiles и конфиги
migrations/       Alembic (async env)
loadtest/         k6-сценарии нагрузочного тестирования (make loadtest)
docs/adr/         Architecture Decision Records (значимые решения)
.github/          CI (GitHub Actions)
tests/            pytest (services, repositories, decorators, storage, outbox, perf)
```

---

## 📈 Нагрузочное тестирование

Юнит-`perf`-тесты — санити в CI. Для реальной нагрузки есть готовый k6-харнес
([loadtest/](loadtest/), запускается в Docker — ставить ничего не нужно):

```bash
make loadtest                          # smoke (все ручки) + traffic + ratelimit
make loadtest mode=traffic vus=200 dur=60      # объёмная нагрузка через nginx
make loadtest url=http://backend:8000 mode=read vus=300 dur=60   # ёмкость мимо nginx
make sim-limit cpuset=0-3 mem=2g       # симулировать «сервер на 4 ядрах» (docker update)
```

Подробности и режимы — [loadtest/README.md](loadtest/README.md).

Узкое место под нагрузкой почти всегда — downstream-ресурсы (пул PG/Redis), а не число
воркеров. Сначала масштабируйте пулы и реплики, затем воркеры. Важно: argon2 (login/
register) — CPU-bound by design, считается в пуле потоков и вне транзакции БД
(см. [ADR-0003](docs/adr/0003-argon2-offloading.md)).
