# Architecture Decision Records (ADR)

Короткие записи о значимых архитектурных решениях: **что** решили, **почему** и
**какими последствиями**. Цель — чтобы через полгода (или новому человеку в команде)
был понятен контекст, а не только итоговый код.

## Когда писать ADR

Когда решение: влияет на структуру/контракты, трудно обратимо, или неочевидно и
кто-то наверняка спросит «почему так, а не иначе». Мелкие правки ADR не требуют.

## Формат

Один файл = одно решение, имя `NNNN-краткое-название.md`. Разделы:
**Status** (Proposed / Accepted / Superseded), **Context**, **Decision**, **Consequences**.
Решения не удаляем и не переписываем задним числом — если передумали, заводим новый ADR
со статусом, который заменяет старый (`Supersedes 000X`).

## Список

- [0001](0001-record-architecture-decisions.md) — вести ADR
- [0002](0002-transactional-outbox.md) — transactional outbox для надёжной доставки событий
- [0003](0003-argon2-offloading.md) — argon2 в пуле потоков и вне транзакции БД
- [0004](0004-optional-components.md) — брокер и S3-хранилище за флагами (опциональные компоненты)
- [0005](0005-concurrency-control.md) — защита от гонок: `for_update` + `VersionedMixin`
- [0006](0006-auth-token-transport.md) — транспорт токена: header XOR cookie
- [0007](0007-eager-loading.md) — eager-load relationship через `options` (async-требование)
- [0008](0008-unit-of-work-lifecycle.md) — UnitOfWork: ленивый, один на транзакцию
- [0009](0009-query-and-search.md) — универсальные фильтры/сортировка + умный (fuzzy) поиск
- [0010](0010-idempotency-keys.md) — идемпотентность POST через `Idempotency-Key` (хелпер + required по риску)
- [0011](0011-acquiring-abstraction.md) — абстракция эквайринга (SDK vs свой HTTP-клиент, дженерик `RawT`)
