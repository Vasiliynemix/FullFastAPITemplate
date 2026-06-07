"""Тесты слоя аутентификации: пароли, JWT, сессии, AuthService (login/refresh/logout)."""

from __future__ import annotations

import pytest

from app.db.uow import UnitOfWork
from app.exceptions.base import UnauthorizedError
from app.schemas.auth import LoginRequest, RegisterRequest
from app.security.jwt import (
    TokenError,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.security.password import hash_password, verify_password
from app.security.roles import Role, role_at_least
from app.security.session_store import SessionStore
from app.services.auth import AuthService
from app.services.user import UserService

pytestmark = pytest.mark.asyncio


# --- Пароли ---
async def test_password_hash_and_verify():
    hashed = hash_password("super-secret-123")
    assert hashed != "super-secret-123"
    assert verify_password("super-secret-123", hashed) is True
    assert verify_password("wrong", hashed) is False
    assert verify_password("x", None) is False


# --- JWT ---
async def test_access_token_roundtrip():
    token = create_access_token("user-1", Role.ADMIN, sid="s1")
    payload = decode_token(token, expected_type=TokenType.ACCESS)
    assert payload.sub == "user-1"
    assert payload.role is Role.ADMIN
    assert payload.sid == "s1"


async def test_refresh_token_carries_sid_and_jti():
    token = create_refresh_token("user-1", sid="s1", jti="j1")
    payload = decode_token(token, expected_type=TokenType.REFRESH)
    assert payload.sid == "s1"
    assert payload.jti == "j1"


async def test_wrong_token_type_rejected():
    access = create_access_token("u", Role.USER, sid="s1")
    with pytest.raises(TokenError):
        decode_token(access, expected_type=TokenType.REFRESH)


# --- Режимы авторизации (config) ---
async def test_auth_mode_validator_forbids_disabling_all():
    from pydantic import ValidationError

    from app.core.config import Settings

    # JWT off + global off — запрещено (сервис без защиты)
    with pytest.raises(ValidationError):
        Settings(auth_jwt_enabled=False, global_api_key_enabled=False)

    # Допустимые режимы — не падают
    Settings(auth_jwt_enabled=True, global_api_key_enabled=False)
    Settings(auth_jwt_enabled=False, global_api_key_enabled=True)


# --- Роли ---
async def test_role_hierarchy():
    assert role_at_least(Role.ADMIN, Role.USER) is True
    assert role_at_least(Role.USER, Role.ADMIN) is False
    assert role_at_least(Role.MANAGER, Role.MANAGER) is True


# --- SessionStore ---
async def test_session_store_create_validate_rotate(fake_redis):
    store = SessionStore(fake_redis)
    sid, jti = await store.create("user-1")
    assert await store.validate(sid, jti, "user-1") is True

    new_jti = await store.rotate(sid, "user-1")
    assert new_jti != jti
    # старый jti больше не валиден (reuse)
    assert await store.validate(sid, jti, "user-1") is False
    assert await store.validate(sid, new_jti, "user-1") is True


async def test_session_store_revoke_all_and_others(fake_redis):
    store = SessionStore(fake_redis)
    s1, _ = await store.create("user-1")
    s2, _ = await store.create("user-1")
    await store.create("user-1")  # третья сессия

    # выйти из всех кроме s1 -> отозвано 2
    assert await store.revoke_others("user-1", s1) == 2
    assert await store.exists(s1) is True
    assert await store.exists(s2) is False

    # выйти из всех -> отозвана оставшаяся 1
    assert await store.revoke_all("user-1") == 1
    assert await store.exists(s1) is False


# --- AuthService ---
def _auth(sessionmaker, fake_redis) -> AuthService:
    return AuthService(uow=UnitOfWork(sessionmaker), sessions=SessionStore(fake_redis))


async def test_register_and_login(sessionmaker, fake_redis):
    svc = _auth(sessionmaker, fake_redis)
    user = await svc.register(
        RegisterRequest(email="a@example.com", password="password123", full_name="A")
    )
    assert user.role is Role.USER

    pair = await svc.login(LoginRequest(email="a@example.com", password="password123"))
    payload = decode_token(pair.access_token, expected_type=TokenType.ACCESS)
    assert payload.sub == str(user.id)
    assert payload.sid  # сессия выдана


async def test_login_wrong_password(sessionmaker, fake_redis):
    svc = _auth(sessionmaker, fake_redis)
    await svc.register(
        RegisterRequest(email="b@example.com", password="password123", full_name="B")
    )
    with pytest.raises(UnauthorizedError):
        await svc.login(LoginRequest(email="b@example.com", password="nope"))


async def test_refresh_rotates_and_detects_reuse(sessionmaker, fake_redis):
    svc = _auth(sessionmaker, fake_redis)
    await svc.register(
        RegisterRequest(email="c@example.com", password="password123", full_name="C")
    )
    pair = await svc.login(LoginRequest(email="c@example.com", password="password123"))

    new_pair = await svc.refresh(pair.refresh_token)
    assert new_pair.refresh_token != pair.refresh_token
    # тот же sid сохраняется в новой паре
    old = decode_token(pair.access_token, expected_type=TokenType.ACCESS)
    new = decode_token(new_pair.access_token, expected_type=TokenType.ACCESS)
    assert old.sid == new.sid

    # повторное использование старого refresh -> отказ И отзыв сессии (reuse)
    with pytest.raises(UnauthorizedError):
        await svc.refresh(pair.refresh_token)
    # сессия скомпрометирована -> новый refresh тоже больше не работает
    with pytest.raises(UnauthorizedError):
        await svc.refresh(new_pair.refresh_token)


async def test_logout_current(sessionmaker, fake_redis):
    svc = _auth(sessionmaker, fake_redis)
    await svc.register(
        RegisterRequest(email="d@example.com", password="password123", full_name="D")
    )
    pair = await svc.login(LoginRequest(email="d@example.com", password="password123"))
    sid = decode_token(pair.access_token, expected_type=TokenType.ACCESS).sid

    assert await svc.logout_current(sid) == 1
    # refresh этой сессии больше не пройдёт
    with pytest.raises(UnauthorizedError):
        await svc.refresh(pair.refresh_token)


async def test_list_sessions_marks_current(sessionmaker, fake_redis):
    svc = _auth(sessionmaker, fake_redis)
    user = await svc.register(
        RegisterRequest(email="g@example.com", password="password123", full_name="G")
    )
    p1 = await svc.login(
        LoginRequest(email="g@example.com", password="password123"),
        ip="10.0.0.1",
        user_agent="Mozilla/5.0",
    )
    await svc.login(LoginRequest(email="g@example.com", password="password123"), ip="10.0.0.2")
    sid1 = decode_token(p1.access_token, expected_type=TokenType.ACCESS).sid

    sessions = await svc.list_sessions(str(user.id), sid1)
    assert len(sessions) == 2
    current = [s for s in sessions if s.current]
    assert len(current) == 1
    assert current[0].sid == sid1
    assert current[0].ip == "10.0.0.1"
    assert current[0].user_agent == "Mozilla/5.0"


async def test_logout_all_and_others(sessionmaker, fake_redis):
    svc = _auth(sessionmaker, fake_redis)
    user = await svc.register(
        RegisterRequest(email="e@example.com", password="password123", full_name="E")
    )
    # три «устройства»
    p1 = await svc.login(LoginRequest(email="e@example.com", password="password123"))
    await svc.login(LoginRequest(email="e@example.com", password="password123"))
    await svc.login(LoginRequest(email="e@example.com", password="password123"))
    sid1 = decode_token(p1.access_token, expected_type=TokenType.ACCESS).sid

    assert await svc.logout_others(str(user.id), sid1) == 2
    assert await svc.logout_all(str(user.id)) == 1  # осталась только текущая


async def test_delete_user_revokes_sessions(sessionmaker, fake_cache, fake_redis):
    sessions = SessionStore(fake_redis)
    auth = AuthService(uow=UnitOfWork(sessionmaker), sessions=sessions)
    users = UserService(
        uow=UnitOfWork(sessionmaker),
        cache=fake_cache,
        sessions=sessions,
    )

    user = await auth.register(
        RegisterRequest(email="f@example.com", password="password123", full_name="F")
    )
    pair = await auth.login(LoginRequest(email="f@example.com", password="password123"))

    await users.delete(user.id)

    # все сессии пользователя должны быть отозваны
    assert await sessions.list_sids(str(user.id)) == []
    with pytest.raises(UnauthorizedError):
        await auth.refresh(pair.refresh_token)
