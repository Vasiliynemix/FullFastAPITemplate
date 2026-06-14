# React + TS Playbook — продакшн-шаблон фронта в пару к FastAPI-бэкенду

> **Как пользоваться:** скопируй этот файл в корень `MainReactTemplate` как `CLAUDE.md`
> (подхватится в контекст автоматически). Затем: «Скаффолдим React-шаблон по CLAUDE.md,
> начни с Фазы 0». Бэкенд-эталон рядом: `../MainFastAPITemplate` (его контракт — раздел 3).

---

## 0. Философия (неизменяемые правила)

1. **TypeScript strict, без `any`.** Типы — источник истины. API-ответы типизированы.
2. **Слои однонаправлены:** `pages → features → shared`. Компоненты «тупые» (рендер),
   логика — в хуках; сеть — только через `apiClient`/Query-хуки, не в компонентах.
3. **Один способ ходить в сеть.** Весь HTTP — через типизированный `apiClient`, который
   разворачивает конверт бэкенда `{status,data,meta}` и кидает типизированный `ApiError`.
   Прямой `fetch`/`axios` в компонентах запрещён.
4. **Серверный стейт ≠ клиентский.** Данные с бэка живут в TanStack Query (кэш/инвалидация),
   а НЕ в useState/глобальном сторе. Глобальный стор — только UI/сессия.
5. **Формы типизированы Zod-схемой**, которая зеркалит DTO бэкенда; ошибки `422` бэкенда
   раскладываются по полям формы.
6. **Код — на английском, комментарии — на русском** (как в бэкенде).
7. **Зелёный набор всегда:** `typecheck`, `lint`, `test` — без ошибок.

---

## 1. Стек (зафиксирован)

- **Vite + React 18 + TypeScript** (strict).
- **React Router** (v6) — маршрутизация SPA.
- **TanStack Query** (React Query) — серверный стейт.
- **React Hook Form + Zod** (+ `@hookform/resolvers`) — формы и валидация.
- **Tailwind CSS** — стилизация.
- **Zustand** — клиентский стор для НЕсерверного состояния: сессия (токены/юзер), UI-флаги
  (тема, сайдбар, модалки). Серверные данные тут НЕ держим (они в TanStack Query).
- **ESLint + Prettier** — линт/формат (аналог ruff). **tsc --noEmit** — типы (аналог mypy).
- **Vitest + React Testing Library + MSW** — юнит/компонентные тесты с моком бэка.
- **Playwright** — E2E (опционально, отдельный прогон).
- Пакетный менеджер: **pnpm** (или npm — команды ниже работают и так).

---

## 2. Структура (feature-based)

```
src/
  main.tsx                 точка входа: провайдеры (Query, Router, Auth)
  app/
    router.tsx             роуты + защищённые маршруты (ProtectedRoute)
    providers.tsx          QueryClientProvider, AuthProvider, ...
  shared/
    api/
      client.ts            apiClient: fetch-обёртка, конверт, 401-refresh, ApiError
      types.ts             ServerResponse<T>, ErrorCode, ResponseMeta, ApiError, Page<T>
      queryClient.ts       настроенный QueryClient (retry/staleTime)
    config/env.ts          VITE_API_URL и пр. (типизировано)
    ui/                    дизайн-система: Button, Input, Spinner, ... (Tailwind)
    lib/                   утилиты (cn, formatters, ...)
    hooks/                 общие хуки (useDebounce, ...)
  features/<домен>/        напр. auth/, users/, accounts/
    api.ts                 Query/Mutation-хуки этого домена (useUsers, useCreateUser)
    schema.ts              Zod-схемы запросов/ответов (зеркало DTO бэка)
    components/            компоненты домена
    types.ts               доменные типы (из schema через z.infer)
  pages/                   страницы-роуты (тонкие: собирают features)
  index.css                Tailwind directives
```

Правило зависимостей: `pages` → `features` → `shared`. `features` НЕ импортят друг друга
напрямую (через shared или явный публичный API фичи).

---

## 3. Завязка на бэкенд (КОНТРАКТ — главное)

Бэкенд (`../MainFastAPITemplate`) отдаёт ЕДИНЫЙ конверт. Фронт обязан его понимать.

**Успех:** `{ "status": true, "data": <T>, "meta": { "request_id", "page", "per_page", "total", "pages" } }`
**Ошибка:** `{ "status": false, "data": { "code": <ErrorCode>, "message", "details": [{loc,msg,type}]|null }, "meta": {"request_id"} }`

- База: `VITE_API_URL` + `/api/v1`. Каждый ответ несёт `X-Request-ID` (логируй при ошибках).
- **Auth:** `Authorization: Bearer <access>`. `POST /auth/login` → `{access_token, refresh_token, token_type, expires_in}`.
  `POST /auth/refresh` с `{refresh_token}` → новая пара. На `401` — один раз пробуем refresh, потом retry; не вышло → logout.
- **Idempotency-Key:** на небезопасных POST (деньги/создание) — заголовок `Idempotency-Key: <uuid>`
  (для денежных операций ОБЯЗАТЕЛЕН, иначе `422`). Генерь `crypto.randomUUID()` на отправку.
- **Коды ошибок (`data.code`):** `validation_error`(422) `not_found`(404) `conflict`(409)
  `unauthorized`(401) `forbidden`(403) `rate_limited`(429) `bad_request`(400)
  `service_unavailable`(503) `internal_error`(500).
- **Пагинация:** `meta.{page,per_page,total,pages}`. Списки: `?page=&per_page=&sort=&q=&<field>__<op>=`.

### 3.1 Типы — `shared/api/types.ts`

```ts
export type ErrorCode =
  | "internal_error" | "validation_error" | "not_found" | "conflict"
  | "unauthorized" | "forbidden" | "rate_limited" | "bad_request" | "service_unavailable";

export interface ResponseMeta {
  request_id?: string;
  page?: number; per_page?: number; total?: number; pages?: number;
  extra?: Record<string, unknown> | null;
}
export interface SuccessResponse<T> { status: true; data: T; meta?: ResponseMeta }
export interface ErrorBody {
  code: ErrorCode; message: string;
  details?: { loc: (string | number)[]; msg: string; type: string }[] | null;
}
export interface ErrorResponse { status: false; data: ErrorBody; meta?: ResponseMeta }

/** Типизированная ошибка API — её кидает apiClient, ловят хуки/формы. */
export class ApiError extends Error {
  constructor(
    public code: ErrorCode,
    message: string,
    public httpStatus: number,
    public details?: ErrorBody["details"],
    public requestId?: string,
  ) { super(message); this.name = "ApiError"; }
}

/** Страница списка: данные + мета пагинации. */
export interface Page<T> { items: T[]; meta: ResponseMeta }
```

### 3.2 apiClient — `shared/api/client.ts`

```ts
import { API_URL } from "@/shared/config/env";
import { ApiError, type SuccessResponse, type ErrorResponse } from "./types";
import { getAccessToken, refreshTokens, clearSession } from "@/features/auth/session";

interface Options extends Omit<RequestInit, "body"> { body?: unknown; idempotencyKey?: string; }

async function raw(path: string, opts: Options = {}): Promise<Response> {
  const headers = new Headers(opts.headers);
  headers.set("Content-Type", "application/json");
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (opts.idempotencyKey) headers.set("Idempotency-Key", opts.idempotencyKey);
  return fetch(`${API_URL}/api/v1${path}`, {
    ...opts, headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
}

/** Разворачивает конверт: возвращает data, кидает ApiError. 401 -> один refresh + retry. */
export async function api<T>(path: string, opts: Options = {}, _retried = false): Promise<T> {
  let res = await raw(path, opts);

  if (res.status === 401 && !_retried) {
    const ok = await refreshTokens();           // тихий refresh (один раз)
    if (ok) return api<T>(path, opts, true);
    clearSession();
  }

  const body = (await res.json().catch(() => null)) as
    | SuccessResponse<T> | ErrorResponse | null;
  const requestId = res.headers.get("X-Request-ID") ?? body?.meta?.request_id ?? undefined;

  if (body && body.status === true) return body.data;

  // единый разбор ошибки в типизированный ApiError
  const err = body && body.status === false ? body.data : null;
  throw new ApiError(
    err?.code ?? "internal_error",
    err?.message ?? `HTTP ${res.status}`,
    res.status, err?.details ?? null, requestId,
  );
}

export const apiGet = <T>(p: string, o?: Options) => api<T>(p, { ...o, method: "GET" });
export const apiPost = <T>(p: string, body?: unknown, o?: Options) => api<T>(p, { ...o, method: "POST", body });
export const apiPatch = <T>(p: string, body?: unknown, o?: Options) => api<T>(p, { ...o, method: "PATCH", body });
export const apiDelete = <T>(p: string, o?: Options) => api<T>(p, { ...o, method: "DELETE" });
```

### 3.3 Query-хуки фичи — `features/users/api.ts`

```ts
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPost } from "@/shared/api/client";
import type { Page } from "@/shared/api/types";
import type { User, UserCreate } from "./types";

const keys = {
  all: ["users"] as const,
  list: (p: number) => [...keys.all, "list", p] as const,
};

export function useUsers(page = 1) {
  return useQuery({
    queryKey: keys.list(page),
    // список приходит как data:User[] + meta пагинации -> собираем Page<User> в самом хуке
    queryFn: async () => apiGet<User[]>(`/users?page=${page}&per_page=50`),
  });
}

export function useCreateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (dto: UserCreate) =>
      apiPost<User>("/users", dto, { idempotencyKey: crypto.randomUUID() }),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}
```

### 3.4 Форма (RHF + Zod) + раскладка ошибок `422`

```ts
// features/users/schema.ts — Zod зеркалит DTO бэкенда
import { z } from "zod";
export const userCreateSchema = z.object({
  email: z.string().email(),
  password: z.string().min(8),
  full_name: z.string().min(1),
});
export type UserCreate = z.infer<typeof userCreateSchema>;
```

```tsx
// в компоненте формы: маппим details бэкенда (loc:["body","email"]) в ошибки полей
const form = useForm<UserCreate>({ resolver: zodResolver(userCreateSchema) });
const create = useCreateUser();
const onSubmit = form.handleSubmit(async (values) => {
  try { await create.mutateAsync(values); }
  catch (e) {
    if (e instanceof ApiError && e.code === "validation_error") {
      for (const d of e.details ?? []) {
        const field = d.loc.at(-1);                  // напр. "email"
        if (typeof field === "string") form.setError(field as keyof UserCreate, { message: d.msg });
      }
    } else throw e;                                   // прочее -> глобальный тост/ErrorBoundary
  }
});
```

### 3.5 Auth + сессия на Zustand — `features/auth/session.ts`

Сессия (токены/юзер) — это клиентское состояние → **Zustand-стор** (с `persist` для refresh).
`apiClient` дёргает его не-реактивно через `getState()`; компоненты — через хук-селектор.

```ts
import { create } from "zustand";
import { persist } from "zustand/middleware";
import { apiPost } from "@/shared/api/client";

interface Tokens { access: string; refresh: string }
interface SessionState {
  tokens: Tokens | null;
  setSession: (t: Tokens) => void;
  clearSession: () => void;
}

export const useSession = create<SessionState>()(
  persist(
    (set) => ({
      tokens: null,
      setSession: (tokens) => set({ tokens }),
      clearSession: () => set({ tokens: null }),
    }),
    { name: "session", partialize: (s) => ({ tokens: s.tokens }) }, // в localStorage только токены
  ),
);

// не-реактивные хелперы для apiClient (вне React-дерева):
export const getAccessToken = () => useSession.getState().tokens?.access ?? null;
export const setSession = (t: Tokens) => useSession.getState().setSession(t);
export const clearSession = () => useSession.getState().clearSession();

/** Тихий refresh (зовётся из apiClient на 401). true = обновили. */
export async function refreshTokens(): Promise<boolean> {
  const refresh = useSession.getState().tokens?.refresh;
  if (!refresh) return false;
  try {
    const pair = await apiPost<{ access_token: string; refresh_token: string }>(
      "/auth/refresh", { refresh_token: refresh },
    );
    setSession({ access: pair.access_token, refresh: pair.refresh_token });
    return true;
  } catch { return false; }
}
```

- `ProtectedRoute` смотрит `useSession((s) => s.tokens)` и редиректит на `/login`, если нет.
- `useLogin()` мутация → `setSession()` → редирект. `logout()` → `clearSession()` + чистка Query-кэша.
- UI-состояние (тема/сайдбар) — отдельный маленький `useUiStore` по тому же паттерну.

---

## 4. Конвенции

- **Алиас импорта** `@/` → `src/` (`vite.config.ts` + `tsconfig.paths`).
- Компонент — функция, пропсы типизированы; без бизнес-логики (она в хуках).
- Имена: компоненты `PascalCase`, хуки `useXxx`, файлы фич — по домену.
- Никаких «магических строк» URL по компонентам — только через `features/<x>/api.ts`.
- Состояния загрузки/ошибки/пустоты у списков — обязательны (Query даёт `isLoading/isError`).
- Доступность и `aria-*` для интерактивных элементов из `shared/ui`.

---

## 5. Чек-лист скаффолда (Фаза 0 → фичи)

**Фаза 0 — каркас (приложение «дышит»):**
- [ ] `npm create vite@latest . -- --template react-ts` (в пустой папке).
- [ ] Tailwind: установить + `index.css` с директивами + `tailwind.config`.
- [ ] Алиас `@/` (vite + tsconfig); strict в `tsconfig` (`strict`, `noUncheckedIndexedAccess`).
- [ ] ESLint + Prettier; скрипты `lint/format/typecheck/test` в `package.json`.
- [ ] `shared/api/{types,client,queryClient}.ts` (раздел 3) + `shared/config/env.ts` (`VITE_API_URL`).
- [ ] Провайдеры (`QueryClientProvider`, Router, Auth) в `app/providers.tsx` + `main.tsx`.
- [ ] `shared/ui`: Button, Input, Spinner, FormField (Tailwind).
- [ ] `features/auth`: Zustand session-стор (persist) + login + ProtectedRoute; страница `/login`.
- [ ] `.env.example` (`VITE_API_URL=http://localhost:8080`), `README`.
- [ ] Зелёные `typecheck/lint/test`.

**Фаза 1..N — по одной фиче** (зеркаля домены бэкенда: users, accounts, ...):
- [ ] `schema.ts` (Zod) → `types.ts` (z.infer) → `api.ts` (Query/Mutation-хуки).
- [ ] компоненты + страница; состояния loading/error/empty.
- [ ] тесты: хук с MSW-моком + компонент через RTL; форму — на раскладку `422`.
- [ ] прогон `typecheck/lint/test`.

---

## 6. Тесты (уровни — как в бэкенде)

- **Юнит** — утилиты/хелперы/Zod-схемы (Vitest).
- **Компонентные** — RTL: рендер + взаимодействие; сеть мокаем **MSW** (mock backend, отдаёт
  тот же конверт `{status,data,meta}`). Это аналог «сервис+БД» бэкенда.
- **API-клиент** — отдельно тестируем разбор конверта/ошибок/refresh на MSW.
- **E2E** — Playwright против `npm run dev` + поднятого бэкенда (`make up`): сценарий
  login → действие → проверка. Отдельный прогон, не в обычном CI.
- Паттерн: Arrange → Act → Assert; `screen.getByRole`, `userEvent`, `await findBy...`.

---

## 7. Команды

```bash
npm run dev          # Vite dev-сервер
npm run build        # прод-сборка (tsc + vite build)
npm run typecheck    # tsc --noEmit  (аналог mypy)
npm run lint         # eslint
npm run format       # prettier --write
npm run test         # vitest
npm run test:e2e     # playwright (нужен поднятый бэкенд)
```

---

## 8. Ключевые решения (резюме)

- Единый `apiClient`, разворачивающий конверт бэкенда → типизированный `ApiError` по `code`.
- Серверный стейт в TanStack Query; клиентский (сессия/UI) — в Zustand (persist для токенов).
- Формы RHF+Zod; `422 details` бэкенда раскладываются по полям.
- Auth: Bearer + тихий refresh на `401`; ProtectedRoute.
- Idempotency-Key на небезопасных POST (обязателен для денег).
- Feature-based слои `pages → features → shared`; компоненты тупые, логика в хуках.
- TS strict, тесты с MSW (mock того же контракта), что и у бэкенда.

---

**Старт в новой сессии (внутри MainReactTemplate):** «Скаффолдим по CLAUDE.md, Фаза 0:
Vite react-ts + Tailwind + алиас + apiClient/types + провайдеры + auth-каркас. После шага —
typecheck/lint/test. Бэкенд-контракт — раздел 3.»
