# -*- coding: utf-8 -*-
"""Общая конфигурация тестов.

TITAN_TEST_DB=1 включается ДО импорта titan.* — тогда Settings подставит
временную БД вместо рабочей titan.db (см. titan/config.py).
"""
import os

# Должно выполниться раньше любых импортов пакета titan в тестах
os.environ.setdefault("TITAN_TEST_DB", "1")

import pytest
from httpx import ASGITransport, AsyncClient

from titan.api.app import app


@pytest.fixture
async def client():
    """HTTP-клиент поверх приложения с запущенным lifespan (общий для всех тестов)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            yield ac

    # После теста: сброс live-кэшей коллектора, ТУ-токенов и состояния движков
    # симулятора (singleton — инъекция КЗ из одного теста не должна «перетекать»
    # в следующий: breaker, pending_cmd, injection — на КАЖДОЙ ТП)
    from titan.api import commands
    if commands._collector:
        for poller in commands._collector.pollers.values():
            poller.live = {}
    commands._pending.clear()
    if commands._simulator:
        for engine in commands._simulator.engines.values():
            engine.injection = None
            engine.breaker = 1
            engine.pending_cmd = 0


# ---------- Общие хелперы для всех тестовых файлов ----------

def auth(token: str) -> dict:
    """Заголовок Authorization: Bearer <token>."""
    return {"Authorization": f"Bearer {token}"}


async def login(client, username: str = "admin", password: str = "admin") -> str:
    """Войти дефолтным админом, вернуть JWT-токен."""
    r = await client.post("/api/auth/login",
                          json={"username": username, "password": password})
    return r.json()["access_token"]
