"""
Роли и их иерархия.

Чтобы добавить/переименовать роль — правьте ТОЛЬКО этот файл:
* добавьте значение в Role;
* при необходимости укажите уровень в _LEVELS (для проверок «роль не ниже X»).

Проверки доступа в ручках делаются через require_roles(...) (см. app/api/deps.py)
либо role_at_least(...) для иерархических случаев.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    # Порядок не важен — иерархию задаёт _LEVELS ниже.
    SERVICE = "service"  # машинный доступ другого продукта (service-to-service)
    ADMIN = "admin"  # полный доступ
    MANAGER = "manager"  # расширенный доступ
    USER = "user"  # обычный пользователь (дефолт)


# Числовой уровень для сравнения «не ниже». Чем больше — тем больше прав.
# SERVICE намеренно высокий: интеграции продуктов часто нужны привилегии.
_LEVELS: dict[Role, int] = {
    Role.USER: 10,
    Role.MANAGER: 20,
    Role.ADMIN: 30,
    Role.SERVICE: 30,
}

DEFAULT_ROLE = Role.USER


def role_at_least(role: Role, minimum: Role) -> bool:
    """True, если `role` имеет уровень не ниже `minimum`."""
    return _LEVELS.get(role, 0) >= _LEVELS.get(minimum, 0)
