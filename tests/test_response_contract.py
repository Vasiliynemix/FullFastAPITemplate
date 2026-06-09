"""
Контракт ответов: конверты собираются ТОЛЬКО через хелперы success()/error()/empty().

Прямое конструирование SuccessResponse(...)/ErrorResponse(...)/ErrorData(...)/EmptyResponse(...)/
ServerResponse(...) вне app/schemas/response.py запрещено — иначе теряется единый стиль
(request_id из контекста запроса, дефолтные коды). Использование как ТИПА
(response_model=SuccessResponse[T], аннотации возврата) и `SuccessResponse[T].model_validate(...)`
по-прежнему разрешено — это не конструирование конверта вручную.

Тест статический (AST), без импорта приложения: дёшев и ловит нарушение прямо в CI.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Конверты ответа из app/schemas/response.py (имена уникальны: в app/clients/response.py
# свои ApiResponse/ApiError, коллизии нет).
_BANNED = {"ServerResponse", "SuccessResponse", "EmptyResponse", "ErrorResponse", "ErrorData"}

_APP = Path(__file__).resolve().parent.parent / "app"
_ALLOWED_FILE = _APP / "schemas" / "response.py"  # единственное место, где их можно конструировать


def _constructed_name(func: ast.expr) -> str | None:
    """Имя конверта, если это его ПРЯМОЕ конструирование (вызов), иначе None."""
    # ErrorResponse(...)
    if isinstance(func, ast.Name) and func.id in _BANNED:
        return func.id
    # SuccessResponse[T](...) — вызов над подпиской
    if (
        isinstance(func, ast.Subscript)
        and isinstance(func.value, ast.Name)
        and func.value.id in _BANNED
    ):
        return func.value.id
    # SuccessResponse[T].model_validate(...) — это Attribute, НЕ конструирование → разрешено
    return None


def test_response_envelopes_built_only_via_helpers() -> None:
    violations: list[str] = []
    for path in _APP.rglob("*.py"):
        if path == _ALLOWED_FILE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _constructed_name(node.func)
                if name is not None:
                    rel = path.relative_to(_APP.parent)
                    violations.append(f"{rel}:{node.lineno} -> {name}(...)")

    assert not violations, (
        "Конверты ответов нужно собирать через success()/error()/empty(), "
        "а не конструировать напрямую:\n  " + "\n  ".join(violations)
    )
