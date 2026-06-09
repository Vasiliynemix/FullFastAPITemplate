# API Reference

Справочник по REST API сервиса. Интерактивная версия — **Swagger UI** на `/docs`
(в dev), ReDoc на `/redoc`. Машинная схема — `/openapi.json`.

- **Base URL:** `http(s)://<host>` + префикс версии `//api/v1`
- Полный путь любой ручки: `/api/v1/...`
- Формат тел запросов/ответов: `application/json` (стриминг — `application/x-ndjson`).

---

## Единый контракт ответов

**Любой** ответ обёрнут в единый конверт.

Успех:
```json
{
  "status": true,
  "data": { /* полезная нагрузка (объект, список, ...) */ },
  "meta": { "request_id": "9de0...", "page": 1, "per_page": 50, "total": 2, "pages": 1 }
}
```

Ошибка (включая валидацию и непредвиденные 500 — сырые ошибки наружу не утекают):
```json
{
  "status": false,
  "data": { "code": "not_found", "message": "User not found", "details": null },
  "meta": { "request_id": "9de0..." }
}
```

- `meta.request_id` присутствует в **каждом** ответе и дублируется в заголовке `X-Request-ID` — используйте для сквозной трассировки (тот же id виден в логах сервиса).
- `meta` для коллекций содержит пагинацию: `page`, `per_page`, `total`, `pages`.

### Коды ошибок (`data.code`)

| HTTP | code | Когда |
|------|------|-------|
| 400 | `bad_request` | некорректный запрос |
| 401 | `unauthorized` | нет/невалидный токен или API-ключ |
| 403 | `forbidden` | недостаточно прав (роль) |
| 404 | `not_found` | ресурс не найден |
| 409 | `conflict` | конфликт (например, email уже занят) |
| 422 | `validation_error` | ошибка валидации тела; детали в `data.details[]` |
| 429 | `rate_limited` | превышен лимит запросов |
| 503 | `service_unavailable` | зависимость недоступна |
| 500 | `internal_error` | непредвиденная ошибка |

---

## Авторизация

Два контура — **JWT** и **глобальный API-ключ** — управляются двумя флагами и дают
**три** режима (комбинация «оба выключены» запрещена и роняет старт сервиса):

| Режим | `AUTH_JWT_ENABLED` | `GLOBAL_API_KEY_ENABLED` | Заголовки клиента |
|---|---|---|---|
| JWT + глобал | `true` | `true` | `X-API-Key` **И** `Authorization: Bearer` |
| только JWT | `true` | `false` | `Authorization: Bearer` |
| только глобал | `false` | `true` | `X-API-Key` |

В режиме «только глобал» защита целиком на ключе. Пользовательские ручки `/auth/*`
(`register/login/refresh/logout/me/sessions`) в этом режиме **не публикуются** — это
чистый service-to-service, их нет ни в API, ни в Swagger. Оставшиеся JWT-зависимости
(например на `/users/{id}`) не требуют токен и не проверяют роли (принципал — анонимный
`service`). Swagger показывает в **Authorize** ровно нужные схемы.

### 1. Глобальный API-ключ (`X-API-Key`)

Активируется флагом `GLOBAL_API_KEY_ENABLED=true` (+ `GLOBAL_API_KEY=<секрет>`).
Когда включён — **весь API, кроме `/health/*` и `/docs`/`/redoc`/`/openapi.json`**,
требует заголовок:

```
X-API-Key: <GLOBAL_API_KEY>
```

Без него / с неверным ключом — `401 unauthorized`. Это режим «сервис закрыт одним
ключом для доверенных продуктов» (service-to-service). В Swagger при включённом флаге
в диалоге **Authorize** появляется поле `ApiKeyAuth`.

### 2. JWT (Bearer) + роли + сессии

Защищённые ручки требуют заголовок:
```
Authorization: Bearer <access_token>
```

- **access_token** — короткоживущий (`ACCESS_TOKEN_EXPIRE_MINUTES`, по умолчанию 15 мин).
- **refresh_token** — долгоживущий (`REFRESH_TOKEN_EXPIRE_DAYS`); на `/auth/refresh`
  **ротируется** (старый становится недействительным; повторное использование → отзыв сессии).
- **Сессии**: каждый `login` создаёт сессию (`sid`); `sid` лежит в обоих токенах.
- **Роли**: `user` (дефолт), `manager`, `admin`, `service`. Проверяются на ручках.

Немедленный отзыв access после logout/удаления — опционально через `AUTH_VALIDATE_SESSION=true`
(каждый запрос сверяет сессию с Redis). По умолчанию access stateless (живёт до истечения).

**Транспорт токена** (`AUTH_TOKEN_TRANSPORT`, по умолчанию `header`) — что-то ОДНО:
- `header` — токен в `Authorization: Bearer` (SPA/mobile);
- `cookie` — `login`/`refresh` ставят HttpOnly-куки `access_token`/`refresh_token`,
  авторизация читает токен из куки, `logout` их чистит (браузерная сессия на одном домене).

Cookie работает только при `AUTH_JWT_ENABLED=true` и **несовместим** с глобальным ключом
(валидатор роняет старт). Тело ответа `login`/`refresh` в обоих режимах содержит токены.

---

## Эндпоинты

Условные обозначения столбца «Auth»: `—` нет JWT · `JWT` нужен access-токен ·
`role` нужна роль. Если включён глобальный gate, ко всем (кроме health) дополнительно
нужен `X-API-Key`.

### Health

| Метод | Путь | Auth | Описание |
|------|------|------|----------|
| GET | `/api/v1/health/live` | — | процесс жив (liveness) |
| GET | `/api/v1/health/ready` | — | проверка включённых компонентов (config — только не в проде) |

`/health/ready` проверяет ВКЛЮЧЁННЫЕ компоненты: `postgres`/`redis` всегда, `broker`/`storage` —
если включены флагами. При недоступности любого — **HTTP 503** (`status: "degraded"`), иначе `200`.

Блок `config` (активные режимы, без секретов и без типа брокера/хранилища) отдаётся
**только не в проде** — эндпоинт открыт, а конфиг раскрывает защитную конфигурацию.
В проде включается флагом `HEALTH_EXPOSE_CONFIG=true`. В проде по умолчанию — только `checks`.

```json
// GET /health/ready  (prod, по умолчанию)
{ "status": true, "data": {
    "status": "ok",
    "checks": { "postgres": "ok", "redis": "ok", "broker": "ok" }
  }, "meta": {...} }

// GET /health/ready  (dev — добавляется config)
{ "status": true, "data": {
    "status": "ok",
    "checks": { "postgres": "ok", "redis": "ok", "broker": "ok" },
    "config": {
      "environment": "dev",
      "auth": { "jwt": true, "global_api_key": false, "token_transport": "header", "validate_session": true },
      "broker": { "enabled": true },
      "storage": { "enabled": false },
      "outbox": { "enabled": true }, "rate_limit": true, "sentry": false, "docs": true
    }
  }, "meta": {...} }
```

### Auth

> Доступны только при `AUTH_JWT_ENABLED=true`. В режиме «только глобал» этих ручек нет.

| Метод | Путь | Auth | Описание |
|------|------|------|----------|
| POST | `/api/v1/auth/register` | — | регистрация |
| POST | `/api/v1/auth/login` | — | вход → пара токенов (создаёт сессию) |
| POST | `/api/v1/auth/refresh` | refresh-токен в теле | ротация пары |
| POST | `/api/v1/auth/logout` | JWT | выйти из текущей сессии |
| POST | `/api/v1/auth/logout/all` | JWT | выйти из всех сессий |
| POST | `/api/v1/auth/logout/others` | JWT | выйти из всех, кроме текущей |
| GET | `/api/v1/auth/sessions` | JWT | список активных сессий (мои устройства) |
| GET | `/api/v1/auth/me` | JWT | данные текущего принципала |

**register** — body: `{ "email", "password" (≥8), "full_name", "role"? }` → `201`, `data: UserRead`.

**login** — body: `{ "email", "password" }` →
```json
{ "status": true, "data": {
    "access_token": "eyJ...", "refresh_token": "eyJ...",
    "token_type": "bearer", "expires_in": 900 }, "meta": {...} }
```

**refresh** — body: `{ "refresh_token": "eyJ..." }` → новая пара токенов.

**logout / logout/all / logout/others** → `{ "data": { "revoked": <int> } }` (сколько сессий отозвано).

**sessions** →
```json
{ "status": true, "data": [
    { "sid": "445c...", "created_at": "...", "last_used_at": "...",
      "ip": "127.0.0.1", "user_agent": "iPhone Safari", "current": true } ], "meta": {...} }
```

### Users

| Метод | Путь | Auth | Описание |
|------|------|------|----------|
| POST | `/api/v1/users` | — | создать пользователя. Поддерживает `Idempotency-Key` (опционально) |
| GET | `/api/v1/users` | — | список: пагинация + фильтры + сортировка + умный поиск (см. ниже) |
| GET | `/api/v1/users/{id}` | JWT | получить по id |
| PATCH | `/api/v1/users/{id}` | — | частичное обновление |
| PUT | `/api/v1/users/{id}/profile` | — | создать/обновить профиль: `{ "bio"?, "avatar_url"? }` (one-to-one upsert) |
| DELETE | `/api/v1/users/{id}` | `admin` | удалить (отзывает все сессии юзера) |
| GET | `/api/v1/users/stream/all` | — | потоковая выгрузка (NDJSON) |

> Уровень защиты каждой ручки указан в колонке **Auth**.

**list** — query: `page` (≥1, дефолт 1), `per_page` (1–200, дефолт 50):
```json
{ "status": true, "data": [ /* UserRead[] */ ],
  "meta": { "page": 1, "per_page": 50, "total": 2, "pages": 1, "request_id": "..." } }
```

`UserRead`: `{ "id", "email", "full_name", "is_active", "role" }`.

### Accounts (демо связей + блокировки)

Демонстрационный домен: ВСЕ типы relationship (one-to-one / one-to-many / many-to-one /
many-to-many) с eager-load в ответе + операции с балансом под пессимистичной блокировкой
`SELECT ... FOR UPDATE`. Баланс и суммы — целые **минорные единицы** (копейки).

| Метод | Путь | Auth | Описание |
|------|------|------|----------|
| POST | `/api/v1/accounts` | — | создать счёт: `{ "user_id", "name" }` → `201`, `AccountRead`. **Требует** `Idempotency-Key` |
| GET | `/api/v1/accounts/{id}` | — | счёт + транзакции + категории каждой (eager-load) |
| GET | `/api/v1/accounts/overview/{user_id}` | — | юзер: профиль (1-1) + счета (1-many) + вложенные транзакции |
| POST | `/api/v1/accounts/{id}/deposit` | — | пополнить: `{ "amount" (>0), "acquirer", "category_ids"? }`. **Требует** `Idempotency-Key` |
| POST | `/api/v1/accounts/{id}/withdraw` | — | списать: `{ "amount" (>0), "acquirer" }`. **Требует** `Idempotency-Key` |
| POST | `/api/v1/categories` | — | создать категорию: `{ "name" }` → `201` (имя уникально) |
| GET | `/api/v1/categories` | — | список категорий |

- `deposit`/`withdraw` берут строку счёта `FOR UPDATE` — параллельные операции не теряют
  изменения (нет lost update).
- `acquirer` — платёжная система (enum): `memory` | `yookassa` | … Обязателен. Должен быть
  **включён** в конфиге деплоя (`*_ENABLED`); неизвестное значение или выключенный провайдер
  → `422 validation_error`. Какие включены — зависит от окружения.
- `withdraw` при нехватке средств → `409 conflict`.
- `deposit.category_ids` привязывает категории к транзакции (many-to-many). **Категории
  должны существовать** — неизвестный `category_id` → `404` (не игнорируется). Создать
  категорию: `POST /categories`. Дубль имени категории → `409`.

### Notifications

> Доступна только при `BROKER_ENABLED=true`.

| Метод | Путь | Auth | Описание |
|------|------|------|----------|
| POST | `/api/v1/notifications` | — | поставить уведомление в очередь брокера → `202` |

body: `{ "recipient_phone": "+1...", "text": "...", "markdown"? }`. Публикуется как
типизированное событие; доставку выполняет консьюмер (фоном, вне request lifecycle).

### Files (объектное хранилище)

> Доступны только при `STORAGE_ENABLED=true` (S3-совместимое хранилище: AWS S3/MinIO/Yandex).

| Метод | Путь | Auth | Описание |
|------|------|------|----------|
| POST | `/api/v1/files` | — | загрузка (multipart-поле `file`) → `{ key, url, size, content_type }` |
| GET | `/api/v1/files/url/{key}` | — | presigned-ссылка на ПРЯМОЕ скачивание из S3 |
| GET | `/api/v1/files/download/{key}` | — | потоковое скачивание через бэкенд |
| DELETE | `/api/v1/files/{key}` | — | удалить объект |

---

## Пагинация

Списки используют **постраничную** навигацию: `?page=N&per_page=M`.
- `page` — номер страницы с 1; `offset` считается сервером как `(page-1)*per_page`.
- В `meta`: `total` (всего записей), `pages` (всего страниц = `ceil(total/per_page)`).
- Пустой список — когда `page > pages`.

## Фильтрация, сортировка, поиск

**Эти параметры работают на ЛЮБОЙ ручке, отдающей список** (сейчас — `GET /users`).
Фильтровать/сортировать можно по полям ресурса; неизвестное поле → `400`.

**Фильтры** — `?field__op=value`. Оператор по умолчанию `eq` (`?is_active=true` ==
`?is_active__eq=true`). Полный список операторов:

| op | смысл (SQL) | пример |
|---|---|---|
| `eq` | `=` (равно) | `?is_active__eq=true` |
| `ne` | `<>` (не равно) | `?role__ne=admin` |
| `gt` | `>` (больше) | `?age__gt=18` |
| `ge` | `>=` (больше или равно) | `?created_at__ge=2024-01-01` |
| `lt` | `<` (меньше) | `?price__lt=100` |
| `le` | `<=` (меньше или равно) | `?price__le=100` |
| `like` | `LIKE` (регистрозависимо, с `%`) | `?email__like=%@gmail.com` |
| `ilike` | `ILIKE` (регистронезависимо, с `%`) | `?full_name__ilike=ив%` |
| `contains` | подстрока — `ILIKE %v%` | `?full_name__contains=ив` |
| `in` | `IN (...)` — значения через запятую | `?role__in=admin,manager` |

Несколько фильтров комбинируются по AND. Значения приводятся к типу колонки
(bool/int/float/date/datetime/uuid); неверное значение или неизвестный оператор → `400`.

**Сортировка** — `?sort=field` (asc) или `?sort=-field` (desc). По умолчанию — `created_at`
по возрастанию. Неизвестное поле → `400`.

**Умный поиск** — `?q=<строка>`: терпит **опечатки**. Для пользователей ищет по имени и
email. Пример: `?q=всая` находит «Вася Пупкин» (перестановка букв). Результаты сортируются
по релевантности. Спецсимволы из `q` безопасно игнорируются.

```bash
# активные, отсортированные по имени, вторая страница
curl "$BASE/users?is_active__eq=true&sort=full_name&page=2&per_page=20"
# умный поиск с опечаткой
curl -G "$BASE/users" --data-urlencode "q=всая"
```

## Стриминг (NDJSON)

`GET /api/v1/users/stream/all` отдаёт **NDJSON** (`application/x-ndjson`): по одному
JSON-объекту на строку, без обёртки-конверта. Подходит для больших выгрузок —
память на сервере константная. Читать построчно:

```bash
curl -s .../api/v1/users/stream/all | jq -c .
```

## Rate limiting

Лимит по клиенту (Redis, fixed window), **многоярусный** — запрос блокируется при превышении
любого яруса. Идентификатор клиента: по умолчанию **IP** (за nginx — первый `X-Forwarded-For`);
при включённом глобальном ключе — **по ключу** (квота на продукт, а не на IP):
- **длинное окно** — `RATE_LIMIT_REQUESTS` запросов за `RATE_LIMIT_WINDOW` секунд (квота);
- **burst-ярус** — `RATE_LIMIT_BURST` запросов за `RATE_LIMIT_BURST_WINDOW` секунд против
  всплесков (`0` = выключен). Пример: `1000/60с` + `20/1с` — минутная квота, но не более
  ~20 запросов в секунду.

При превышении — `429 rate_limited` + заголовки `Retry-After`, `X-RateLimit-Limit`,
`X-RateLimit-Remaining` (отдаются на ответе `429`). Health-ручки не лимитируются.

## Идемпотентность

Небезопасные операции (создание ресурсов, движения денег) можно безопасно ретраить при
сетевых сбоях — заголовок `Idempotency-Key: <uuid>`. Ключ генерирует **клиент**, один на
одну логическую операцию (новый uuid на каждую новую операцию).

Длина ключа — 8..255 символов (если прислан невалидный → `422`).

- **Обязателен** (без заголовка → `422`, чтобы случайный ретрай не задвоил деньги/ресурс):
  `POST /accounts`, `POST /accounts/{id}/deposit`, `POST /accounts/{id}/withdraw`.
- **Опционален** (желателен, но не требуется): `POST /users`.

Поведение по тому же ключу:

| Ситуация | Ответ |
|---|---|
| Первый запрос | операция выполняется, результат сохраняется под ключом |
| Повтор после завершения | возвращается **тот же** сохранённый результат, дубль не создаётся (replay) |
| Повтор, пока первый ещё выполняется (in-flight) | `409 conflict` — повторите позже |

```bash
KEY=$(uuidgen)
# первый запрос — выполнит депозит
curl -s -X POST $BASE/accounts/$ACC/deposit -H "Idempotency-Key: $KEY" \
  -H 'Content-Type: application/json' -d '{"amount":10000,"acquirer":"yookassa"}'
# повтор с тем же ключом (например после таймаута сети) — вернёт тот же результат, не задвоит
curl -s -X POST $BASE/accounts/$ACC/deposit -H "Idempotency-Key: $KEY" \
  -H 'Content-Type: application/json' -d '{"amount":10000,"acquirer":"yookassa"}'
```

Сохранённый ответ живёт ~сутки (TTL). Без заголовка эндпоинт работает как обычно, без
защиты от дублей.

---

## Примеры (curl)

```bash
BASE=http://localhost:8080/api/v1
KEY=...   # если включён GLOBAL_API_KEY_ENABLED, добавляйте: -H "X-API-Key: $KEY"

# 1) регистрация и вход
curl -s -X POST $BASE/auth/register -H 'Content-Type: application/json' \
  -d '{"email":"a@example.com","password":"password123","full_name":"A"}'

ACCESS=$(curl -s -X POST $BASE/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"a@example.com","password":"password123"}' | jq -r .data.access_token)

# 2) защищённая ручка
curl -s $BASE/auth/me -H "Authorization: Bearer $ACCESS"

# 3) список с пагинацией
curl -s "$BASE/users?page=1&per_page=20"

# 4) мои сессии и выход из остальных
curl -s $BASE/auth/sessions -H "Authorization: Bearer $ACCESS"
curl -s -X POST $BASE/auth/logout/others -H "Authorization: Bearer $ACCESS"
```
