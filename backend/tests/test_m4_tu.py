# -*- coding: utf-8 -*-
"""
Тесты M4: телеуправление (двухшаговый алгоритм) и осциллограммы.

Приёмочный сценарий ТЗ: «блокировка -> выбор -> подтверждение ->
ожидание смены состояния -> успех», плюс таймауты и неудачи.
"""
import asyncio

import pytest

from titan.collector.service import Reading


async def login(client, username="admin", password="admin") -> str:
    """Хелпер: вход -> токен."""
    r = await client.post("/api/auth/login", json={"username": username, "password": password})
    return r.json()["access_token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_tu_select_requires_dispatcher(client):
    """Шаг 1 ТУ: viewer -> 403 (ТУ недоступно без роли dispatcher)."""
    token = await login(client)
    h = auth(token)
    await client.post("/api/admin/users", headers=h,
                      json={"username": "v1", "password": "pw", "role": "viewer"})
    vtok = (await client.post("/api/auth/login",
            json={"username": "v1", "password": "pw"})).json()["access_token"]
    r = await client.post("/api/commands/select", headers=auth(vtok),
                          json={"object_name": "Ввод 10 кВ №1", "action": "open"})
    assert r.status_code == 403


async def test_tu_full_success_cycle(client):
    """Полный цикл ТУ: select -> operate -> успех, состояние изменилось."""
    token = await login(client)
    h = auth(token)

    # Текущее состояние выключателя (ключ ввода ТП-1 в multi-object кэше)
    live = (await client.get("/api/live", headers=h)).json()
    current = int(live["arm1_incomer:BREAKER"]["value"])

    # Шаг 1: выбор противоположной команды
    action = "open" if current == 1 else "close"
    r = await client.post("/api/commands/select", headers=h,
                          json={"object_name": "Ввод 10 кВ №1", "action": action})
    assert r.status_code == 200
    token_op = r.json()["token"]
    assert r.json()["expires_in_s"] == 10     # таймаут выбора из ТЗ

    # Шаг 2: исполнение -> ждём смены состояния (актуатор 2 с + опрос)
    r = await client.post("/api/commands/operate", headers=h, json={"token": token_op})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["state"] == (0 if action == "open" else 1)

    # live-кэш подтверждает новое состояние
    await asyncio.sleep(0.3)
    live = (await client.get("/api/live", headers=h)).json()
    assert int(live["arm1_incomer:BREAKER"]["value"]) == body["state"]


async def test_tu_select_timeout(client):
    """Таймаут выбора 10 с: протухший токен -> 410 (операция сброшена)."""
    from titan.api.commands import _pending, SELECT_TIMEOUT_S
    token = await login(client)
    h = auth(token)

    r = await client.post("/api/commands/select", headers=h,
                          json={"object_name": "Ввод 10 кВ №1", "action": "open"})
    tok = r.json()["token"]

    # Симулируем истечение: сдвигаем дедлайн в прошлое
    _pending[tok]["expires_at"] = 0.0
    r = await client.post("/api/commands/operate", headers=h, json={"token": tok})
    assert r.status_code == 410


async def test_tu_block_on_fault(client):
    """Блокировка ТУ: при активной аварии select -> 409 «Телеуправление запрещено»."""
    token = await login(client)
    h = auth(token)

    # Вживую ставим код аварии в live-кэш коллектора (ключ ввода ТП-1)
    from titan.api.commands import _collector
    _collector.pollers[1].live["arm1_incomer:FAULT"] = Reading("FAULT", 1.0, "good")

    r = await client.post("/api/commands/select", headers=h,
                          json={"object_name": "Ввод 10 кВ №1", "action": "open"})
    assert r.status_code == 409
    assert "запрещено" in r.json()["detail"]

    # Убираем аварию
    _collector.pollers[1].live["arm1_incomer:FAULT"] = Reading("FAULT", 0.0, "good")


async def test_waveform_generated_on_short_circuit(client):
    """Приёмка M4: КЗ -> осциллограмма в архиве -> просмотр по id."""
    token = await login(client)
    h = auth(token)

    # Запускаем КЗ через пульт: ТОЧЕЧНО на ввод ТП-1 (multi-object API)
    r = await client.post("/api/sim/fault", headers=h,
                          params={"object_id": "arm1_incomer",
                                  "scenario": "short_circuit", "duration_s": 3})
    assert r.status_code == 200

    # Ждём: коллектор увидит код аварии и создаст осциллограмму (фоновая задача)
    await asyncio.sleep(2.5)

    # В журнале появилась авария
    evs = (await client.get("/api/events?cls=alarm", headers=h)).json()
    assert evs["count"] >= 1

    # Осциллограмма доступна (последняя записанная — id 1 в чистой БД теста)
    r = await client.get("/api/waveforms/1", headers=h)
    assert r.status_code == 200
    wf = r.json()
    assert wf["fault_code"] == 1
    # 6 каналов, каждый длиной ~400 сэмплов (20 периодов × 1 кГц)
    assert len(wf["channels"]) == 6
    assert len(wf["channels"]["ia"]) > 300
    # При КЗ амплитуда тока фазы A в разы больше номинальной
    peak_ia = max(abs(v) for v in wf["channels"]["ia"])
    assert peak_ia > 150   # 25 А номинал * 10 * sqrt(2) / 2 с запасом
