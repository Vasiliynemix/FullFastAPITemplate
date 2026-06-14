# Stack Playbook — перенос проекта на эту FastAPI-архитектуру

> **Как пользоваться:** положи этот файл в корень целевого проекта как `CLAUDE.md`
> (он подхватится в контекст автоматически) или ссылайся `@STACK_PLAYBOOK.md`.
> Это перенос **структуры и контрактов**, а не файлов 1:1 — домен у целевого проекта свой.
> Эталонный репозиторий (если подключён через `--add-dir`): бери из него точные исходники
> доменно-нейтральных файлов (раздел «Копировать как есть»).

---

## 0. Философия (неизменяемые правила)

1. **Слои строго однонаправлены:** `api → services → repositories → db`. Роут не лезет в БД,
   сервис не знает про HTTP, репозиторий не знает про бизнес-логику.
2. **Единый конверт ответов.** ЛЮБОЙ ответ — `{status, data, meta}`. Собирается ТОЛЬКО
   хелперами `success()/error()/empty()`. Прямое конструирование классов-конвертов запрещено.
3. **Ошибки наружу — только `ServerException`** (контролируемые). Всё остальное ловит
   глобальный хендлер и отдаёт чистый `500` в том же конверте (сырой traceback не утекает).
4. **Конфиг — fail-fast.** Невалидная конфигурация роняет старт с понятной ошибкой, а не
   падает на первом запросе. Валидаторы в `Settings`.
5. **Код — на английском, комментарии/докстринги — на русском.** Комментарии объясняют
   «почему», а не «что».
6. **Каждая фича = код + тесты + (если значимо) ADR + обновление README/API.md.**
7. **Зелёный набор всегда:** `ruff check`, `ruff format --check`, `mypy`, `pytest` — без ошибок.

---

## 1. Стек

- Python **3.13** (зафиксировать: `.python-version` = `3.13` + `requires-python = ">=3.13,<3.14"`;
  убедись, что `.python-version` НЕ в `.gitignore`).
- FastAPI + Starlette (ASGI), Pydantic v2, pydantic-settings.
- SQLAlchemy 2.x async + asyncpg, Alembic (async env).
- Redis (async) — кэш / rate-limit / idempotency / сессии.
- uv (пакеты/venv), ruff (lint+format), mypy, pytest + pytest-asyncio (`asyncio_mode = "auto"`).
- Опционально за флагами: брокер (kafka/rabbitmq/memory), S3-хранилище, эквайринг.

---

## 2. Карта директорий (что чем владеет)

```
app/
  main.py            app-factory create_app(): middleware, роутеры, хендлеры, lifespan
  core/
    config.py        Settings (pydantic-settings) + enums + валидаторы; singleton `settings`
    logging.py       структурный логгер
    context.py       contextvars: request_id / trace_id
    lifespan.py      старт/стоп: коннекты к зависимостям, healthcheck
    openapi.py       кастомизация схемы/Swagger
  api/
    deps.py          DI: Annotated-типы (XServiceDep, CurrentUserDep, ...) + параметры списков
    router.py        сборка v1-роутера
    v1/<домен>.py    РОУТЫ: распарсить вход → вызвать сервис → success(...). Без логики.
  schemas/
    response.py      конверт ServerResponse[T] + success/error/empty + ErrorCode
    <домен>.py       DTO запросов/ответов (Pydantic)
  services/<домен>.py  БИЗНЕС-логика. Только ServerException наружу.
  repositories/
    base.py          BaseRepository: get/get_by/list/paginate/filters/sort/search/locks
    <домен>.py       репозиторий модели
  db/
    session.py       async engine + sessionmaker (пул)
    uow.py           UnitOfWork: ленивый, один на транзакцию/запрос
    query.py         apply_filters/apply_sort/apply_search/sanitize_q (+ операторы)
  models/<домен>.py  ORM-модели (+ Base, миксины: timestamps, uuid pk, version_id)
  exceptions/
    base.py          ServerException + подклассы (BadRequest/NotFound/Conflict/...)
    handlers.py      глобальные хендлеры -> единый ErrorResponse; регистрация в create_app
  middleware/        request_context, api_key gate, rate_limit, security headers
  cache/, ratelimit/, idempotency/, broker/, storage/, acquiring/, clients/, security/, decorators/
migrations/          Alembic (async)
tests/               conftest (фикстуры) + test_*.py
docs/adr/            Architecture Decision Records
```

---

## 3. Ядро-контракты (с кодом — воспроизводи дословно)

### 3.1 Конверт ответов — `app/schemas/response.py`

```python
class ErrorCode(StrEnum):
    INTERNAL="internal_error"; VALIDATION="validation_error"; NOT_FOUND="not_found"
    CONFLICT="conflict"; UNAUTHORIZED="unauthorized"; FORBIDDEN="forbidden"
    RATE_LIMITED="rate_limited"; BAD_REQUEST="bad_request"; UNAVAILABLE="service_unavailable"

class ResponseMeta(BaseModel):
    request_id: str | None = None
    page: int | None = None; per_page: int | None = None
    total: int | None = None; pages: int | None = None
    extra: dict[str, object] | None = None

class ServerResponse(BaseModel, Generic[T]):
    status: bool; data: T; meta: ResponseMeta | None = None
class SuccessResponse(ServerResponse[T]): status: bool = True
class EmptyResponse(BaseModel): status: bool = True; meta: ResponseMeta | None = None
class ErrorData(BaseModel):
    code: ErrorCode = ErrorCode.INTERNAL; message: str
    details: list[dict[str, object]] | None = None
class ErrorResponse(ServerResponse[ErrorData]): status: bool = False

def success(data, *, meta=None) -> SuccessResponse:   # подмешивает request_id из контекста
def empty() -> EmptyResponse:
def error(message, *, code=ErrorCode.INTERNAL, details=None) -> ErrorResponse:
```

- В роутах: `return success(dto)`. Списки: `success(items, meta=ResponseMeta(page=..., total=...))`.
- `response_model=SuccessResponse[XRead]` на каждом роуте (для OpenAPI и сериализации).
- **Исключение из «только хелпером»:** рехидрация из кэша/idempotency через
  `SuccessResponse[X].model_validate(cached)` — это восстановление готового конверта, не сборка.
- (Опц.) AST-тест-гард, запрещающий прямое конструирование конвертов вне `response.py`.

### 3.2 Исключения — `app/exceptions/`

```python
# base.py
class ServerException(Exception):
    def __init__(self, status_code:int, message:str, *, code=ErrorCode.INTERNAL, details=None): ...
class NotFoundError(ServerException):     # 404 / not_found
class ConflictError(ServerException):     # 409 / conflict
class BadRequestError(ServerException):   # 400 / bad_request
class UnauthorizedError(ServerException): # 401
class ForbiddenError(ServerException):    # 403
class RateLimitedError(ServerException):  # 429
```

```python
# handlers.py — регистрируются в create_app():
add_exception_handler(ServerException, ...)        # -> envelope с его code/status
add_exception_handler(RequestValidationError, ...) # -> 422, details из ошибок Pydantic
add_exception_handler(StarletteHTTPException, ...)  # 404/405 и т.п. в единый конверт
add_exception_handler(Exception, _unhandled)        # -> 500 internal_error; traceback в ЛОГ
```

> **КРИТИЧНО:** создавай `FastAPI(debug=False)` ВСЕГДА (не `debug=settings.debug`). При
> `debug=True` Starlette сам отдаёт сырой traceback в тело, минуя твой хендлер. Traceback
> всё равно попадает в логи (`_unhandled` логирует `exc_info`), а клиент получает чистый конверт.

### 3.3 Конфиг — `app/core/config.py`

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")
    environment: Environment = Environment.DEV
    # ... поля с дефолтами (всё имеет дефолт, чтобы стартовать без .env) ...

    @model_validator(mode="after")
    def _validate_xxx(self) -> Settings:
        # fail-fast: несовместимые комбинации/отсутствие кред у включённого провайдера -> ValueError
        return self

    @property
    def is_prod(self) -> bool: return self.environment is Environment.PROD

settings = get_settings()   # lru_cache singleton
```

Паттерны: enum-поля (типобезопасно, мусор → ошибка на старте); фичи за `*_enabled` флагами;
секреты не светить в открытых ручках.

### 3.4 DB / UnitOfWork / репозиторий

- `db/session.py`: один async engine + `async_sessionmaker` на процесс (пул).
- `db/uow.py`: `UnitOfWork` — лёгкий, **ленивый** (соединение берётся в `async with`), один на
  транзакцию; репозитории доступны лениво (`uow.users`, `uow.repo(Model)`); `commit()` явный.
- `repositories/base.py`: `BaseRepository[Model]` с `get/get_by/list/paginate`,
  `paginate(page, per_page, filters, sort, q, options)`; `options=` (eager-load) — на items, не на COUNT;
  локи `for_update/skip_locked/nowait`; `search_fields` для умного поиска.
- **Async-правило:** ленивая загрузка relationship запрещена — всё, что сериализуешь,
  грузи заранее через `options=[selectinload(...)]`. Иначе `MissingGreenlet`.

### 3.5 DI — `app/api/deps.py`

```python
def get_uow() -> UnitOfWork: return UnitOfWork(get_sessionmaker())
def get_user_service(uow=Depends(get_uow), ...) -> UserService: ...
UserServiceDep = Annotated[UserService, Depends(get_user_service)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
# параметры списков (page/per_page/sort/q/динамические фильтры) -> ListParamsDep
```

### 3.6 App factory — `app/main.py`

```python
def create_app() -> FastAPI:
    app = FastAPI(debug=False, lifespan=lifespan, ...)   # debug=False! (см. 3.2)
    register_exception_handlers(app)
    # middleware ВНЕШНИЙ->ВНУТРЕННИЙ: security headers, request_context(request_id),
    #   api_key gate, rate_limit, CORS
    app.include_router(api_v1_router, prefix=settings.api_v1_prefix)
    return app
```

`lifespan`: на старте — коннекты к включённым зависимостям; `/health/ready` проверяет их.

---

## 4. Конвенции домена

- **Роут**: только парс/вызов/`success`. Бизнес-логики ноль.
- **Сервис**: транзакции через `async with self.uow`; наружу — только `ServerException`;
  cache-aside где нужно; доменные события — через outbox/брокер.
- **Idempotency-Key** на небезопасных POST (деньги/создание): хелпер-обёртка; обязателен для
  денежных операций (без него `422`), опционален для прочих.
- **Списки**: `ListParamsDep` + `search_fields` (+ trgm-индекс для fuzzy) — без ручного
  перечисления полей; синтаксис фильтров/сортировки документируется ОДИН раз в описании API.
- **API.md** — для потребителей API (контракт), без деталей реализации.

---

## 5. Что КОПИРОВАТЬ как есть vs ПЕРЕПИСАТЬ

**Копировать почти дословно (доменно-нейтральное):**
`schemas/response.py`, `exceptions/*`, `core/{config,logging,context,lifespan,openapi}.py`,
`db/{session,uow,base,query}.py`, `repositories/base.py`, `api/deps.py` (каркас),
`middleware/*`, `cache/*`, `ratelimit/*`, `idempotency/*`, `tests/conftest.py` (фикстуры),
конфиги: `pyproject.toml` (ruff/mypy/pytest секции), `.pre-commit-config.yaml`, `Makefile`.

**Переписать под свой домен (берём структуру, не содержимое):**
`models/*`, `schemas/<домен>.py`, `repositories/<домен>.py`, `services/<домен>.py`,
`api/v1/<домен>.py`, миграции, доменные тесты.

---

## 6. Чек-лист рефактора (инкрементально, спина → домены)

**Фаза 0 — каркас (проект «дышит» в новом стиле):**
- [ ] `pyproject.toml`: deps + секции ruff/mypy/pytest; `.python-version`=3.13.
- [ ] `core/config.py` (Settings + валидаторы), `.env.example`.
- [ ] `schemas/response.py` (конверт + хелперы).
- [ ] `exceptions/base.py` + `handlers.py`; `create_app(debug=False)` + регистрация хендлеров.
- [ ] `core/logging.py`, `core/context.py`, middleware `request_context` (request_id).
- [ ] `db/session.py`, `db/uow.py`, `repositories/base.py`.
- [ ] `core/lifespan.py` + `/health/live` и `/health/ready`.
- [ ] `tests/conftest.py` (sqlite in-memory, fake_cache/redis/storage).
- [ ] Зелёные `ruff/mypy/pytest`.

**Фаза 1..N — по одному домену:**
- [ ] модель → миграция → schema → repository → service → роут (`response_model=SuccessResponse[...]`).
- [ ] тесты: сервис+БД (happy/граница/ошибка) + при необходимости API-тест на коды.
- [ ] прогнать `ruff/mypy/pytest`; обновить API.md.

---

## 7. Тесты (как в эталоне)

- **Уровни:** юнит (чистая логика) · интеграция (сервис+sqlite через `sessionmaker`) ·
  API (`AsyncClient` + `ASGITransport` + `dependency_overrides`) · контрактные (AST-гард) ·
  perf (`-m perf`) · нагрузка (k6, отдельно).
- **AAA:** Arrange → Act → Assert; имя теста = поведение словами.
- **Фикстуры** в `conftest.py`: `sessionmaker`, `session`, `fake_cache`, `fake_redis`,
  `fake_storage`, `memory_broker`.
- **Рецепты:** `pytest.raises(X, match=...)`, `monkeypatch.setattr(settings, ...)`,
  `@pytest.mark.parametrize`, `async def` + `asyncio_mode=auto`.

---

## 8. Команды проверки (гонять после каждого шага)

```bash
uv run pytest -q
uv run ruff check app tests
uv run ruff format --check app tests
uv run mypy
```

---

## 9. Ключевые решения (резюме ADR — переноси вместе с паттернами)

- Единый конверт + хелперы; ошибки только через `ServerException`; `FastAPI(debug=False)`.
- UoW ленивый, один на транзакцию; eager-load через `options=` (async-требование).
- Конфиг fail-fast (валидаторы на старте); фичи за `*_enabled` флагами.
- Idempotency-Key для небезопасных POST; обязателен для денег.
- Оптимистичная (`version_id`→409) и пессимистичная (`for_update`) блокировки.
- Transactional Outbox для надёжной доставки доменных событий.
- Абстракции внешнего мира (broker/storage/acquirer/cache) за интерфейсом + фабрика;
  есть SDK → его клиент, нет SDK → клиент на BaseHTTPClient.

---

**Старт в целевом проекте:** «Перестраиваем проект по `STACK_PLAYBOOK.md`, начни с Фазы 0
(каркас). Домен оставь мой, переписывай только доменные слои. После каждого шага — прогон
ruff/mypy/pytest.»
