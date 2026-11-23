# -*- coding: utf-8 -*-
"""
Тесты M2: аутентификация и RBAC (специф §10: «роли отсекают чужие эндпоинты»).

Проверяем:
- вход по логину/паролю, выдача JWT;
- просмотр требует токена (401 без него);
- роли отсекают действия: viewer не может ack (403), admin может всё;
- журнал действий оператора пишется, включая неудачные попытки.

Фичи симулятора/коллектора НЕ нужны — отключаем (медленные bcrypt + фоновый
опрос к одной SQLite-файлу давали взаимные блокировки и «зависания» прогона).
"""
import os

os.environ.setdefault("TITAN_TEST_DB", "1")   # temp SQLite (основное ставит conftest)

# Персист коллектора гасится фикстурой client (NO_PERSIST-механизм в conftest)
NO_PERSIST = True

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from titan.api.app import app
from titan.models.db import Base, OperatorAction


@pytest.fixture
async def client():
    """Клиент с запущенным lifespan; persist коллектора гасится на время теста.

    settings.collector_persist читается коллектором В КАЖДОМ цикле опроса,
    поэтому переключение действует динамически, без пересоздания движка.
    """
    from titan.config import settings as _s
    _s.collector_persist = False        # нет записи 1 Гц → нет блокировок БД
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            async with app.router.lifespan_context(app):
                yield ac
    finally:
        _s.collector_persist = True     # вернуть для следующих тестовых файлов


def auth(token: str) -> dict:
    """Заголовок Authorization для запросов с токеном."""
    return {"Authorization": f"Bearer {token}"}


async def test_login_success_and_me(client):
    """Вход дефолтного admin -> токен -> /me возвращает его роль."""
    r = await client.post("/api/auth/login",
                          json={"username": "admin", "password": "admin"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    assert r.json()["role"] == "admin"

    r = await client.get("/api/me", headers=auth(token))
    assert r.status_code == 200
    assert r.json() == {"username": "admin", "role": "admin"}


async def test_login_wrong_password(client):
    """Неверный пароль -> 401 (а не 500 или молчаливый успех)."""
    r = await client.post("/api/auth/login",
                          json={"username": "admin", "password": "WRONG"})
    assert r.status_code == 401


async def test_protected_requires_token(client):
    """Без токена защищённые эндпоинты отвечают 401."""
    r = await client.get("/api/live")
    assert r.status_code == 401
    r = await client.get("/api/events")
    assert r.status_code == 401


async def test_admin_endpoints_require_admin(client):
    """Admin-эндпоинты недоступны без роли admin (403 при другом токене)."""
    # создаём dispatcher'а
    r = await client.post("/api/auth/login",
                          json={"username": "admin", "password": "admin"})
    admin_token = r.json()["access_token"]

    r = await client.post("/api/admin/users", headers=auth(admin_token),
                          json={"username": "disp1", "password": "pw123", "role": "dispatcher"})
    assert r.status_code == 200

    # dispatcher пытается зайти в admin-зону -> 403
    r = await client.post("/api/auth/login",
                          json={"username": "disp1", "password": "pw123"})
    disp_token = r.json()["access_token"]
    r = await client.get("/api/admin/users", headers=auth(disp_token))
    assert r.status_code == 403
    # пульт симулятора — только admin (multi-object API: требуется object_id)
    r = await client.post("/api/sim/fault", headers=auth(disp_token),
                          params={"object_id": "arm1_incomer", "scenario": "overload"})
    assert r.status_code == 403


async def test_viewer_cannot_ack_but_dispatcher_can(client):
    """Ключевое правило RBAC: viewer -> 403 на ack, dispatcher -> успех."""
    # готовим учётки
    r = await client.post("/api/auth/login",
                          json={"username": "admin", "password": "admin"})
    admin_token = r.json()["access_token"]
    await client.post("/api/admin/users", headers=auth(admin_token),
                      json={"username": "disp2", "password": "pw", "role": "dispatcher"})
    await client.post("/api/admin/users", headers=auth(admin_token),
                      json={"username": "view1", "password": "pw", "role": "viewer"})

    tok_disp = (await client.post("/api/auth/login",
                json={"username": "disp2", "password": "pw"})).json()["access_token"]
    tok_view = (await client.post("/api/auth/login",
                json={"username": "view1", "password": "pw"})).json()["access_token"]

    # viewer не может квитировать
    r = await client.post("/api/events/1/ack", headers=auth(tok_view))
    assert r.status_code == 403

    # dispatcher может (если событие есть; при пустом журнале — 404, тоже валидно)
    r = await client.post("/api/events/1/ack", headers=auth(tok_disp))
    assert r.status_code in (200, 404)


async def test_user_crud_and_protections(client):
    """CRUD учёток: дубль -> 409, само-понижение админа -> 409, деактивация блокирует вход."""
    r = await client.post("/api/auth/login",
                          json={"username": "admin", "password": "admin"})
    admin_token = r.json()["access_token"]
    h = auth(admin_token)

    # дубль имени -> 409
    await client.post("/api/admin/users", headers=h,
                      json={"username": "u1", "password": "pw", "role": "viewer"})
    r = await client.post("/api/admin/users", headers=h,
                          json={"username": "u1", "password": "pw", "role": "viewer"})
    assert r.status_code == 409

    # недопустимая роль -> 422
    r = await client.post("/api/admin/users", headers=h,
                          json={"username": "u2", "password": "pw", "role": "boss"})
    assert r.status_code == 422

    # админ не может деактивировать себя -> 409
    admin_id = (await client.get("/api/admin/users", headers=h)).json()["users"]
    admin_row = next(u for u in admin_id if u["username"] == "admin")
    r = await client.patch(f"/api/admin/users/{admin_row['id']}", headers=h,
                           json={"is_active": False})
    assert r.status_code == 409

    # деактивация чужой учётки -> вход становится невозможен (401)
    u1_row = next(u for u in admin_id if u["username"] == "u1")
    await client.patch(f"/api/admin/users/{u1_row['id']}", headers=h,
                       json={"is_active": False})
    r = await client.post("/api/auth/login", json={"username": "u1", "password": "pw"})
    assert r.status_code == 401


async def test_operator_actions_logged(client):
    """Журнал действий оператора: и успешный вход, и НЕУДАЧНАЯ попытка."""
    # неудачный вход
    await client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    # успешный вход + действие
    r = await client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    token = r.json()["access_token"]
    await client.get("/api/actions", headers=auth(token))

    r = await client.get("/api/actions", headers=auth(token))
    assert r.status_code == 200
    actions = r.json()["actions"]
    results = {(a["username"], a["action"], a["result"]) for a in actions}
    # неудачный вход записан (ТЗ 3.3.2: включая неудачные попытки)
    assert ("admin", "login", "failed") in results
    assert ("admin", "login", "success") in results
