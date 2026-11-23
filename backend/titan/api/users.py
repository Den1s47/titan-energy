# -*- coding: utf-8 -*-
"""
Эндпоинты аутентификации и управления пользователями (специф §10: /auth, /admin/users).

- POST /api/auth/login        — вход, выдача JWT (без токена)
- GET  /api/me                — кто я (любая роль с валидным токеном)
- CRUD /api/admin/users       — управление учётками (только admin)
- POST /api/admin/reset-password — сброс пароля (только admin)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from titan.api.auth import (
    get_current_user, hash_password, log_action, require_role, verify_password,
)
from titan.models.db import User
from titan.models.session import sessionmaker

router = APIRouter(prefix="/api")


# ============================ Схемы запросов/ответов ============================

class LoginRequest(BaseModel):
    """Тело запроса входа."""
    username: str
    password: str


class UserCreate(BaseModel):
    """Тело запроса создания пользователя."""
    username: str
    password: str
    role: str = "viewer"     # admin | dispatcher | viewer


class UserUpdate(BaseModel):
    """Тело запроса изменения пользователя (все поля опциональны)."""
    role: str | None = None
    is_active: bool | None = None


class PasswordReset(BaseModel):
    """Тело запроса сброса пароля."""
    new_password: str


# ============================ Вход ============================

@router.post("/auth/login")
async def login(body: LoginRequest) -> dict:
    """Вход: проверяем пароль -> выдаём JWT-токен.

    При неверном логине/пароле — 401. Неудачные попытки логина тоже
    пишутся в журнал действий (аудит, специф §11).
    """
    async with sessionmaker() as session:
        result = await session.execute(select(User).where(User.username == body.username))
        user = result.scalar_one_or_none()

    ok = user is not None and user.is_active and verify_password(body.password, user.password_hash)
    if not ok:
        # Аудит неудачной попытки входа (если учётка существует — с её именем)
        await log_action(sessionmaker, body.username, "login", "failed", details="неверный логин или пароль")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")

    token = create_access_token(user.username, user.role)
    await log_action(sessionmaker, user.username, "login", "success")
    return {"access_token": token, "token_type": "bearer", "role": user.role}


# локальный импорт после определения router (цикл: auth.py не знает про router)
from titan.api.auth import create_access_token  # noqa: E402


@router.get("/me")
async def me(user: dict = Depends(get_current_user)) -> dict:
    """Текущий пользователь по токену (для проверки токена фронтендом)."""
    return user


# ============================ Управление пользователями (admin) ============================

VALID_ROLES = {"admin", "dispatcher", "viewer"}


@router.get("/admin/users")
async def list_users(admin: dict = Depends(require_role("admin"))) -> dict:
    """Список учётных записей (без хэшей паролей!)."""
    async with sessionmaker() as session:
        result = await session.execute(select(User).order_by(User.username))
        users = result.scalars().all()
    return {"users": [
        {"id": u.id, "username": u.username, "role": u.role, "is_active": u.is_active}
        for u in users
    ]}


@router.post("/admin/users")
async def create_user(body: UserCreate, admin: dict = Depends(require_role("admin"))) -> dict:
    """Создать учётную запись. Имя — уникальное, роль — из допустимого набора."""
    if body.role not in VALID_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"role must be one of {sorted(VALID_ROLES)}")
    # Валидация имени: не пустое, без пробелов, разумная длина (специф §11)
    username = (body.username or "").strip()
    if not username or len(username) > 64 or any(ch.isspace() for ch in username):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "username: непустое, без пробелов, до 64 символов")
    if not body.password or len(body.password) < 4:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "password: минимум 4 символа")
    async with sessionmaker() as session:
        exists = await session.execute(select(User).where(User.username == body.username))
        if exists.scalar_one_or_none():
            raise HTTPException(status.HTTP_409_CONFLICT, "Пользователь уже существует")
        user = User(
            username=body.username,
            password_hash=hash_password(body.password),   # пароль не хранится в открытом виде
            role=body.role,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    await log_action(sessionmaker, admin["username"], "user_create", "success",
                     details=f"создан {body.username} (роль {body.role})")
    return {"id": user.id, "username": user.username, "role": user.role}


@router.patch("/admin/users/{user_id}")
async def update_user(user_id: int, body: UserUpdate,
                      admin: dict = Depends(require_role("admin"))) -> dict:
    """Изменить роль или деактивировать учётку (специф §9.3: Admin UI).

    Защита от само-блокировки: админ не может разжать/деактивировать сам себя,
    чтобы не потерять единственный админский доступ.
    """
    if body.role is not None and body.role not in VALID_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"role must be one of {sorted(VALID_ROLES)}")
    async with sessionmaker() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
        if user.username == admin["username"] and (
            (body.role and body.role != "admin") or body.is_active is False
        ):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "Нельзя понизить или деактивировать собственную учётку")
        if body.role is not None:
            user.role = body.role
        if body.is_active is not None:
            user.is_active = body.is_active
        await session.commit()
    await log_action(sessionmaker, admin["username"], "user_update", "success",
                     details=f"{user.username}: роль={user.role}, активен={user.is_active}")
    return {"id": user.id, "username": user.username, "role": user.role,
            "is_active": user.is_active}


@router.post("/admin/users/{user_id}/reset-password")
async def reset_password(user_id: int, body: PasswordReset,
                         admin: dict = Depends(require_role("admin"))) -> dict:
    """Сброс пароля учётки администратором (специф §9.3)."""
    async with sessionmaker() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
        user.password_hash = hash_password(body.new_password)
        await session.commit()
    await log_action(sessionmaker, admin["username"], "password_reset", "success",
                     details=f"пароль сброшен для {user.username}")
    return {"ok": True, "username": user.username}
