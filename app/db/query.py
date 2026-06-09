"""
Универсальные фильтрация / сортировка / поиск для списочных запросов.

Работает с ЛЮБОЙ моделью через интроспекцию колонок — не нужно перечислять поля руками.
Безопасность: имена полей валидируются по реальным колонкам (белый список => нет инъекций
через имена), значения параметризуются SQLAlchemy (нет SQL-инъекций), операторы — из
фиксированного списка.

Синтаксис фильтра в query-параметрах: `field__op=value` (op по умолчанию `eq`):
    ?is_active__eq=true&created_at__ge=2024-01-01&full_name__ilike=%ва%&role__in=admin,manager

Сортировка: `?sort=created_at` (asc) или `?sort=-created_at` (desc). Дефолт — created_at asc.

Поиск `q`: «умный» (с опечатками) через pg_trgm на Postgres, ILIKE-фолбэк на SQLite.
"""

from __future__ import annotations

import datetime
import re
import uuid
from typing import Any

from sqlalchemy import Select, func, or_
from sqlalchemy.orm import InstrumentedAttribute

from app.exceptions.base import BadRequestError

# Операторы фильтра (`field__op=value`) -> построитель SQL-условия по колонке `c` и значению `v`.
# Только из этого белого списка (любой другой `op` => 400). `c` — колонка модели, `v` —
# значение, уже приведённое к типу колонки (см. _coerce). Использование: query-параметр
# `field__op=value`, напр. `?created_at__ge=2024-01-01`.
#
#   op       | смысл (SQL)                  | пример query-параметра
#   ---------|------------------------------|---------------------------------
#   eq       | c = v   (равно)              | ?is_active__eq=true   (или просто ?is_active=true)
#   ne       | c <> v  (не равно)           | ?role__ne=admin
#   gt       | c > v   (больше)             | ?age__gt=18
#   ge       | c >= v  (больше или равно)   | ?created_at__ge=2024-01-01
#   lt       | c < v   (меньше)             | ?price__lt=100
#   le       | c <= v  (меньше или равно)   | ?price__le=100
#   like     | c LIKE v   (рег.-зависимо)   | ?email__like=%@gmail.com
#   ilike    | c ILIKE v  (рег.-независимо) | ?full_name__ilike=ив%
#   contains | c ILIKE %v% (подстрока)      | ?full_name__contains=ив
#   in       | c IN (v...) (через запятую)  | ?role__in=admin,manager
#
# Дефолтный op — `eq` (если `__op` не указан: `?is_active=true` == `?is_active__eq=true`).
_OPS = {
    "eq": lambda c, v: c == v,
    "ne": lambda c, v: c != v,
    "gt": lambda c, v: c > v,
    "ge": lambda c, v: c >= v,
    "lt": lambda c, v: c < v,
    "le": lambda c, v: c <= v,
    "like": lambda c, v: c.like(v),
    "ilike": lambda c, v: c.ilike(v),
    "contains": lambda c, v: c.ilike(f"%{v}%"),
    "in": lambda c, v: c.in_(v),
}

_BOOL_TRUE = {"true", "1", "yes", "on"}


def _coerce(column: Any, raw: str) -> Any:
    """Привести строковое значение query-параметра к питон-типу колонки."""
    try:
        pytype = column.type.python_type
    except NotImplementedError:
        return raw
    if pytype is bool:
        return raw.strip().lower() in _BOOL_TRUE
    if pytype is int:
        return int(raw)
    if pytype is float:
        return float(raw)
    if pytype is uuid.UUID:
        return uuid.UUID(raw)
    if pytype is datetime.datetime:
        return datetime.datetime.fromisoformat(raw)
    if pytype is datetime.date:
        return datetime.date.fromisoformat(raw)
    return raw


def apply_filters(stmt: Select, model: type, filters: dict[str, str]) -> Select:
    """Навесить WHERE по фильтрам `field__op=value`. Неизвестные поля/операторы => 400."""
    columns = model.__table__.columns  # type: ignore[attr-defined]
    for key, raw in filters.items():
        field, _, op = key.partition("__")
        op = op or "eq"
        if field not in columns:
            raise BadRequestError(f"Unknown filter field: {field}")
        if op not in _OPS:
            raise BadRequestError(f"Unknown filter operator: {op}")
        col: InstrumentedAttribute = getattr(model, field)
        try:
            if op == "in":
                value: Any = [_coerce(columns[field], x) for x in raw.split(",")]
            else:
                value = _coerce(columns[field], raw)
        except (ValueError, TypeError) as exc:
            raise BadRequestError(f"Bad value for filter {field}: {raw!r}") from exc
        stmt = stmt.where(_OPS[op](col, value))
    return stmt


def apply_sort(
    stmt: Select, model: type, sort: str | None, *, default: str = "created_at"
) -> Select:
    """ORDER BY по `sort` (`-` = desc). Дефолт — created_at asc (или id, если поля нет)."""
    columns = model.__table__.columns  # type: ignore[attr-defined]
    desc = False
    field = default
    if sort:
        desc = sort.startswith("-")
        field = sort.lstrip("-+")
    if field not in columns:
        if field == default:
            field = "id" if "id" in columns else next(iter(columns.keys()))
        else:
            raise BadRequestError(f"Unknown sort field: {field}")
    col: InstrumentedAttribute = getattr(model, field)
    return stmt.order_by(col.desc() if desc else col.asc())


def sanitize_q(q: str, *, max_len: int = 100) -> str:
    """Очистить поисковую строку: срезать HTML-теги и control-символы, ограничить длину."""
    q = re.sub(r"<[^>]*>", " ", q)  # вырезаем HTML-теги
    q = re.sub(r"[\x00-\x1f\x7f]", " ", q)  # control-символы
    return q.strip()[:max_len]


def apply_search(
    stmt: Select,
    model: type,
    q: str,
    fields: list[str],
    *,
    dialect: str,
    threshold: float = 0.2,
) -> tuple[Select, Any | None]:
    """
    Поиск по q среди полей fields. Возвращает (stmt, order) — order по релевантности.

    Postgres: триграммная `word_similarity` (pg_trgm) — ищет похожее СЛОВО внутри значения
    поля (а не похожесть на всю строку целиком), поэтому терпит опечатки даже в многословных
    значениях: «всая» находит «Вася» в «Вася Пупкин». Сортировка по релевантности DESC.
    Иначе (SQLite): ILIKE-подстрока, без сортировки по релевантности.
    """
    cols = [getattr(model, f) for f in fields]
    if dialect == "postgresql":
        # word_similarity(query, text): насколько query похож на лучшее слово в text
        conds = [func.word_similarity(q, c) > threshold for c in cols]
        order = func.greatest(*[func.word_similarity(q, c) for c in cols]).desc()
        return stmt.where(or_(*conds)), order
    conds = [c.ilike(f"%{q}%") for c in cols]
    return stmt.where(or_(*conds)), None
