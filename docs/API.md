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

---

## Эндпоинты

Условные обозначения столбца «Auth»: `—` нет JWT · `JWT` нужен access-токен ·
`role` нужна роль. Если включён глобальный gate, ко всем (кроме health) дополнительно
нужен `X-API-Key`.

### Health

| Метод | Путь | Auth | Описание |
|------|------|------|----------|
| GET | `/api/v1/health/live` | — | процесс жив (liveness) |
| GET | `/api/v1/health/ready` | — | зависимости доступны (Postgres/Redis) |

```json
// GET /health/ready
{ "status": true, "data": { "checks": { "postgres": "ok", "redis": "ok" } }, "meta": {...} }
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
| POST | `/api/v1/users` | — | создать пользователя (поддерживает `Idempotency-Key`) |
| GET | `/api/v1/users` | — | список (пагинация) |
| GET | `/api/v1/users/{id}` | JWT | получить по id |
| PATCH | `/api/v1/users/{id}` | — | частичное обновление |
| DELETE | `/api/v1/users/{id}` | `admin` | удалить (отзывает все сессии юзера) |
| GET | `/api/v1/users/stream/all` | — | потоковая выгрузка (NDJSON) |

> Защита конкретных ручек — это пример. Меняйте под свои правила в `app/api/v1/users.py`.

**list** — query: `page` (≥1, дефолт 1), `per_page` (1–200, дефолт 50):
```json
{ "status": true, "data": [ /* UserRead[] */ ],
  "meta": { "page": 1, "per_page": 50, "total": 2, "pages": 1, "request_id": "..." } }
```

`UserRead`: `{ "id", "email", "full_name", "is_active", "role" }`.

---

## Пагинация

Списки используют **постраничную** навигацию: `?page=N&per_page=M`.
- `page` — номер страницы с 1; `offset` считается сервером как `(page-1)*per_page`.
- В `meta`: `total` (всего записей), `pages` (всего страниц = `ceil(total/per_page)`).
- Пустой список — когда `page > pages`.

## Стриминг (NDJSON)

`GET /api/v1/users/stream/all` отдаёт **NDJSON** (`application/x-ndjson`): по одному
JSON-объекту на строку, без обёртки-конверта. Подходит для больших выгрузок —
память на сервере константная. Читать построчно:

```bash
curl -s .../api/v1/users/stream/all | jq -c .
```

## Rate limiting

Лимит по IP (Redis, fixed window), **многоярусный** — запрос блокируется при превышении
любого яруса:
- **длинное окно** — `RATE_LIMIT_REQUESTS` запросов за `RATE_LIMIT_WINDOW` секунд (квота);
- **burst-ярус** — `RATE_LIMIT_BURST` запросов за `RATE_LIMIT_BURST_WINDOW` секунд против
  всплесков (`0` = выключен). Пример: `1000/60с` + `20/1с` — минутная квота, но не более
  ~20 запросов в секунду.

При превышении — `429 rate_limited` + заголовки `Retry-After`, `X-RateLimit-Limit`,
`X-RateLimit-Remaining` (отдаются на ответе `429`). Health-ручки не лимитируются.

## Идемпотентность

Для безопасных ретраев `POST /users` пришлите заголовок `Idempotency-Key: <uuid>`.
Повторный запрос с тем же ключом вернёт сохранённый результат, не создавая дубль.

---

## Примеры (curl)

```bash
BASE=http://localhost:8000/api/v1
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
