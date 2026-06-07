"""
Сервис аутентификации — регистрация/логин/refresh/logout + управление сессиями.

Правила слоя соблюдены: БД через UoW, наружу только ServerException.

Сессии (см. SessionStore):
* login   — создаёт новую сессию (sid+jti), выдаёт access(sid) + refresh(sid,jti).
* refresh — валидирует refresh по sid+jti, РОТИРУЕТ jti внутри той же сессии,
            выдаёт новую пару. Старый refresh после ротации не пройдёт (reuse -> отзыв сессии).
* logout (current/all/others) — отзыв одной/всех/всех-кроме-текущей сессий.
"""

from __future__ import annotations

import uuid

from app.core.config import settings
from app.db.uow import UnitOfWork
from app.decorators.logging import logged
from app.exceptions.base import ConflictError, UnauthorizedError
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, SessionInfo, TokenPair
from app.schemas.user import UserRead
from app.security.jwt import (
    TokenError,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.security.password import hash_password_async, verify_password_async
from app.security.roles import Role
from app.security.session_store import SessionStore


class AuthService:
    def __init__(self, uow: UnitOfWork, sessions: SessionStore) -> None:
        self.uow = uow
        self.sessions = sessions

    @logged("auth.register")
    async def register(self, data: RegisterRequest) -> UserRead:
        # КРИТИЧНО для нагрузки: argon2 (~десятки мс) считаем ДО открытия транзакции,
        # иначе CPU-bound хеш держит соединение пула PgBouncer всё своё время и под
        # массовым register пул мгновенно исчерпывается -> таймауты.
        hashed = await hash_password_async(data.password)
        async with self.uow:
            if await self.uow.users.get_by_email(data.email) is not None:
                raise ConflictError("User with this email already exists")
            user = await self.uow.users.add(
                User(
                    email=data.email,
                    full_name=data.full_name,
                    hashed_password=hashed,
                    role=data.role,
                )
            )
            await self.uow.commit()
            return UserRead.model_validate(user)

    @logged("auth.login")
    async def login(
        self,
        data: LoginRequest,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> TokenPair:
        # Берём из БД только нужный снимок и СРАЗУ отпускаем соединение пула —
        # argon2-verify считаем уже вне транзакции (см. комментарий в register).
        async with self.uow:
            user = await self.uow.users.get_by_email(data.email)
            snapshot = (
                (str(user.id), user.hashed_password, user.is_active, Role(user.role))
                if user is not None
                else None
            )

        # verify_password на None вернёт False; сообщение одинаковое — не раскрываем email
        if snapshot is None or not await verify_password_async(data.password, snapshot[1]):
            raise UnauthorizedError("Invalid credentials")
        uid, _hashed, is_active, role = snapshot
        if not is_active:
            raise UnauthorizedError("User is inactive")

        sid, jti = await self.sessions.create(uid, ip=ip, user_agent=user_agent)
        return self._pair(uid, role, sid, jti)

    @logged("auth.sessions")
    async def list_sessions(self, user_id: str, current_sid: str | None) -> list[SessionInfo]:
        """Активные сессии пользователя; помечаем текущую (current_sid)."""
        raw = await self.sessions.list_sessions(user_id)
        return [SessionInfo(**s, current=(s["sid"] == current_sid)) for s in raw]  # type: ignore[arg-type]

    @logged("auth.refresh")
    async def refresh(self, refresh_token: str) -> TokenPair:
        try:
            payload = decode_token(refresh_token, expected_type=TokenType.REFRESH)
        except TokenError as exc:
            raise UnauthorizedError(str(exc)) from exc

        sid, jti = payload.sid, payload.jti
        if not sid or not jti or not await self.sessions.validate(sid, jti, payload.sub):
            # jti не совпал/сессия отозвана -> возможен reuse: гасим сессию целиком
            if sid:
                await self.sessions.revoke(sid)
            raise UnauthorizedError("Refresh token revoked or already used")

        new_jti = await self.sessions.rotate(sid, payload.sub)
        if new_jti is None:
            raise UnauthorizedError("Session no longer exists")

        # Подтягиваем актуальную роль/активность
        async with self.uow:
            user = await self.uow.users.get(uuid.UUID(payload.sub))
        if user is None or not user.is_active:
            await self.sessions.revoke(sid)
            raise UnauthorizedError("User not found or inactive")

        return self._pair(str(user.id), Role(user.role), sid, new_jti)

    @logged("auth.logout")
    async def logout_current(self, sid: str | None) -> int:
        """Выйти из текущей сессии (по sid из access-токена)."""
        return await self.sessions.revoke(sid) if sid else 0

    @logged("auth.logout_all")
    async def logout_all(self, user_id: str) -> int:
        """Выйти из всех сессий пользователя."""
        return await self.sessions.revoke_all(user_id)

    @logged("auth.logout_others")
    async def logout_others(self, user_id: str, keep_sid: str | None) -> int:
        """Выйти из всех сессий, кроме текущей."""
        if not keep_sid:
            return await self.sessions.revoke_all(user_id)
        return await self.sessions.revoke_others(user_id, keep_sid)

    # ------------------------------------------------------------------
    def _pair(self, user_id: str, role: Role, sid: str, jti: str) -> TokenPair:
        return TokenPair(
            access_token=create_access_token(user_id, role, sid),
            refresh_token=create_refresh_token(user_id, sid, jti),
            expires_in=settings.access_token_expire_minutes * 60,
        )
