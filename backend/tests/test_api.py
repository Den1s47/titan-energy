# -*- coding: utf-8 -*-
"""
Контрактные тесты API (обновлено под RBAC среза M2).

Используем ASGITransport — httpx ходит прямо в ASGI-приложение без реального
TCP-сервера. lifespan запускаем вручную, чтобы поднялись симулятор+коллектор.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from titan.api.app import app


@pytest.fixture
async def client():
    """HTTP-клиент поверх приложения с запущенным lifespan."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Ручной запуск lifespan: стартуют симулятор и коллектор
        async with app.router.lifespan_context(app):
            yield ac


def auth(token: str) -> dict:
    """Заголовок Authorization: Bearer <token>."""
    return {"Authorization": f"Bearer {token}"}


async def login_admin(client) -> str:
    """Хелпер: войти дефолтным админом, вернуть токен."""
    r = await client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    return r.json()["access_token"]


async def test_health(client):
    """Health-check отвечает и сообщает имя приложения (без авторизации)."""
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_live_requires_token(client):
    """Телеметрия защищена: без токена 401 (RBAC, специф §10)."""
    r = await client.get("/api/live")
    assert r.status_code == 401


async def test_live_and_fault_flow(client):
    """Приёмочный сценарий M1 под токеном admin: телеметрия + инъекция КЗ.

    Проверяем полный путь: REST -> коллектор -> Modbus -> симулятор.
    """
    import asyncio

    token = await login_admin(client)

    # Ждём первый успешный цикл опроса (до ~5 с, опрос идёт раз в 1 с).
    # Multi-object: ключи live — 'tp1_incomer:U' и т.п.
    live = {}
    for _ in range(20):
        r = await client.get("/api/live", headers=auth(token))
        live = r.json()
        if live and any(k.endswith(":U") for k in live):
            break
        await asyncio.sleep(0.25)
    assert any(k.endswith(":U") for k in live)          # телеметрия дошла до API
    assert any(k.endswith(":I") for k in live)

    # Инъекция КЗ на ввод ТП-1: API принимает (пульт — только admin)
    r = await client.post("/api/sim/fault", headers=auth(token),
                          params={"object_id": "tp1_incomer",
                                  "scenario": "short_circuit"})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Неизвестный сценарий отклоняется
    r = await client.post("/api/sim/fault", headers=auth(token),
                          params={"object_id": "tp1_incomer",
                                  "scenario": "unknown"})
    assert r.json()["ok"] is False

    # Неизвестный объект тоже отклоняется
    r = await client.post("/api/sim/fault", headers=auth(token),
                          params={"object_id": "no_such",
                                  "scenario": "short_circuit"})
    assert r.json()["ok"] is False
