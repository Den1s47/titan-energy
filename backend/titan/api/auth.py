# -*- coding: utf-8 -*-
"""
Аутентификация и авторизация (специф §10, §11).

Механика:
- вход: POST /api/auth/login (username+password) -> JWT access_token (HS256);
- каждый защищённый эндпоинт через зависимость require_role(...) проверяет
  токен из заголовка Authorization: Bearer <token> и роль пользователя;
- пароли: PBKDF2-HMAC-SHA256, 120k итераций, индивидуальная соль (специф §11);
- роль иерархическая: admin > dispatcher > viewer — require_role("dispatcher")
  пропускает и admin тоже.
"""
import hashlib
import hmac
import logging
import secrets

import jwt  # PyJWT
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from titan.config import settings
from titan.models.db import OperatorAction, User, utcnow

log = logging.getLogger(__name__)

# Иерархия ролей: чем правее, тем больше прав.
ROLE_HIERARCHY = {"viewer": 0, "dispatcher": 1, "admin": 2}

# Схема Authorization: Bearer <token>; auto_error=False -> сами вернём 401
bearer_scheme = HTTPBearer(auto_error=False)


# ============================ Пароли (PBKDF2) ============================

def hash_password(password: str) -> str:
    """Хэшировать пароль: PBKDF2-HMAC-SHA256, 120k итераций, случайная соль.

    Возвращает строку 'salt$hash' (hex) — она хранится в БД вместо пароля.
    """
    salt = secrets.token_hex(16)                        # индивидуальная соль на пароль
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Проверить пароль против сохранённого хэша 'salt$hash'.

    hmac.compare_digest — защита от timing-атак (постоянное время сравнения).
    """
    try:
        salt, expected = stored.split("$", 1)
    except ValueError:
        return False  # некорректный формат хэша в БД
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return hmac.compare_digest(dk.hex(), expected)


# ============================ JWT-токены ============================

def create_access_token(username: str, role: str) -> str:
    """Создать JWT: payload = {sub: username, role: ..., exp: срок жизни}.

    Подписывается секретом из настроек (jwt_secret, .env — специф §11).
    """
    payload = {
        "sub": username,
        "role": role,
        "exp": utcnow() + timedelta_minutes(settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """Декодировать JWT или бросить исключение (просрочен/подделан)."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def timedelta_minutes(minutes: int):
    """datetime.timedelta без прямого импорта datetime в сигнатурах выше."""
    from datetime import timedelta
    return timedelta(minutes=minutes)


# ============================ Зависимости FastAPI ============================

async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    """Зависимость: извлечь пользователя из Bearer-токена.

    Возвращает {username, role}. 401 — нет токена, просрочен, пользователь
    не найден или деактивирован (проверяем по БД, а не только по токену).
    """
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Не авторизован")
    try:
        payload = decode_token(creds.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Токен истёк")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Недействительный токен")

    username: str = payload.get("sub", "")
    async with session_factory() as session:
        result = await session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Пользователь неактивен")
    return {"username": user.username, "role": user.role}


def require_role(min_role: str):
    """Фабрика зависимостей: доступ только для роли min_role и выше.

    Пример: Depends(require_role("dispatcher")) — viewer получит 403.
    Права проверяются НА БЭКЕНДЕ для каждого запроса (специф §9).
    """
    async def checker(user: dict = Depends(get_current_user)) -> dict:
        if ROLE_HIERARCHY[user["role"]] < ROLE_HIERARCHY[min_role]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
        return user
    return checker


# ============================ Журнал действий ============================

async def log_action(session_factory: async_sessionmaker, username: str, action: str,
                     result: str, object_name: str | None = None,
                     details: str | None = None) -> None:
    """Записать действие оператора в журнал (ТЗ 3.3.2: включая неудачные попытки)."""
    async with session_factory() as session:
        session.add(OperatorAction(
            time=utcnow(), username=username, action=action,
            object_name=object_name, result=result, details=details,
        ))
        await session.commit()


# Импорт внизу — избегаем циклической зависимости (session.py -> db.py -> ... )
from titan.models.session import sessionmaker as session_factory  # noqa: E402
