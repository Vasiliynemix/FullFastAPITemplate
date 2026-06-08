# 0007. Eager-load relationship через options на геттерах

## Status
Accepted

## Context
В async SQLAlchemy **ленивая загрузка relationship невозможна**: обращение к незагруженной
связи вне активной greenlet-сессии падает с `MissingGreenlet`, а на отвязанном объекте —
`DetachedInstanceError`. При этом ответы API сериализуются из ORM (`from_attributes`), и
если связь не загружена заранее — сборка ответа падает.

Изначально геттеры репозитория (`get`/`get_by`/`list`) не давали способа загрузить связи —
только сами строки. Любая сериализация relationship была миной.

## Decision
Геттеры `BaseRepository` принимают `options: Sequence[Any] | None` и применяют их через
`.options(...)`. Туда передаются loader-стратегии SQLAlchemy:

```python
acc = await uow.accounts.get(acc_id, options=[selectinload(Account.transactions)])
# вложенно:
user = await uow.users.get(uid, options=[
    selectinload(User.accounts).selectinload(Account.transactions)
])
```

Частые загрузки инкапсулируются именованными методами репозитория
(`AccountRepository.get_with_transactions`, `UserRepository.get_overview`) — сервис не тянет
`selectinload` сам. Связи валидируются в DTO **внутри** сессии (пока всё загружено).

Добавлен демо-домен `accounts`, показывающий ВСЕ типы связей (one-to-one / one-to-many /
many-to-one / many-to-many) и их eager-load в реальных ручках.

## Consequences
- (+) Связи в ответах грузятся предсказуемо; `MissingGreenlet` исключён по построению.
- (+) Стратегия загрузки — на стороне репозитория (где ей и место), сервис её не знает.
- (+) `selectinload` по умолчанию (отдельный IN-запрос на связь) — без дублирования строк
  и декартова взрыва, в отличие от `joinedload` на коллекциях.
- (−) Разработчик ОБЯЗАН осознанно перечислять, что грузить (нельзя «потом дотянется само») —
  это цена async. Тест `test_lazy_load_without_eager_fails` фиксирует грабли явно.
- Правило: всё, что сериализуешь/используешь вне сессии, грузи через `options=`.
