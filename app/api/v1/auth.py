"""
Ручки аутентификации.

В роутах нет бизнес-логики — только вызов AuthService и единый конверт ответа.
Демонстрирует регистрацию, login (access+refresh), ротацию refresh, logout и
защищённую ручку текущего пользователя.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from app.api.deps import AuthServiceDep, CurrentUserDep
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    SessionInfo,
    TokenPair,
)
from app.schemas.response import SuccessResponse, success
from app.schemas.user import UserRead

router = APIRouter()


def _client_ip(request: Request) -> str | None:
    # За Nginx реальный IP в X-Forwarded-For (первый адрес)
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post(
    "/register",
    response_model=SuccessResponse[UserRead],
    status_code=status.HTTP_201_CREATED,
)
async def register(data: RegisterRequest, service: AuthServiceDep) -> SuccessResponse[UserRead]:
    return success(await service.register(data))


@router.post("/login", response_model=SuccessResponse[TokenPair])
async def login(
    data: LoginRequest,
    service: AuthServiceDep,
    request: Request,
) -> SuccessResponse[TokenPair]:
    # Захватываем IP и User-Agent для метаданных сессии
    pair = await service.login(
        data, ip=_client_ip(request), user_agent=request.headers.get("user-agent")
    )
    return success(pair)


@router.post("/refresh", response_model=SuccessResponse[TokenPair])
async def refresh(data: RefreshRequest, service: AuthServiceDep) -> SuccessResponse[TokenPair]:
    return success(await service.refresh(data.refresh_token))


@router.post("/logout", response_model=SuccessResponse[dict])
async def logout(service: AuthServiceDep, current: CurrentUserDep) -> SuccessResponse[dict]:
    """Выйти из ТЕКУЩЕЙ сессии. Нужен только access-токен (sid внутри него)."""
    revoked = await service.logout_current(current.sid)
    return success({"revoked": revoked})


@router.post("/logout/all", response_model=SuccessResponse[dict])
async def logout_all(service: AuthServiceDep, current: CurrentUserDep) -> SuccessResponse[dict]:
    """Выйти из ВСЕХ сессий пользователя (на всех устройствах)."""
    revoked = await service.logout_all(str(current.id))
    return success({"revoked": revoked})


@router.post("/logout/others", response_model=SuccessResponse[dict])
async def logout_others(service: AuthServiceDep, current: CurrentUserDep) -> SuccessResponse[dict]:
    """Выйти из всех сессий, КРОМЕ текущей."""
    revoked = await service.logout_others(str(current.id), current.sid)
    return success({"revoked": revoked})


@router.get("/sessions", response_model=SuccessResponse[list[SessionInfo]])
async def sessions(
    service: AuthServiceDep, current: CurrentUserDep
) -> SuccessResponse[list[SessionInfo]]:
    """Активные сессии пользователя (мои устройства). Текущая помечена current=true."""
    return success(await service.list_sessions(str(current.id), current.sid))


@router.get("/me", response_model=SuccessResponse[dict])
async def me(current: CurrentUserDep) -> SuccessResponse[dict]:
    # Принципал собран из клеймов токена (без обращения к БД)
    return success({"id": str(current.id), "role": current.role, "sid": current.sid})
