# -*- coding: utf-8 -*-
"""
Тесты M5: аналитика MES-уровня (специф §9.2).

Проверяем математические функции (усреднения, статистика, Шухарт, CUSUM)
на детерминированных данных + API-эндпоинты с реальной БД.
"""
# Хелперы auth/login дублируются локально (conftest проекта недоступен как пакет
# из-за конфликта с conftest самого Hermes при запуске из другой cwd)
import pytest


async def login(client, username="admin", password="admin") -> str:
    """Вход дефолтным админом -> JWT."""
    r = await client.post("/api/auth/login", json={"username": username, "password": password})
    return r.json()["access_token"]


def auth(token: str) -> dict:
    """Заголовок Authorization."""
    return {"Authorization": f"Bearer {token}"}

from titan.analytics.stats import (
    cusum, histogram_stats, moving_average, shewhart_zones, basic_stats,
)
from titan.models.db import Measurement, utcnow
from titan.models.session import sessionmaker


# ============================ Математика (чистые функции) ============================

def test_basic_stats():
    """Max/Min/среднее/σ — требования ТЗ для каждой переменной."""
    s = basic_stats([1.0, 2.0, 3.0, 4.0, 5.0])
    assert s["min"] == 1.0
    assert s["max"] == 5.0
    assert s["mean"] == 3.0
    # σ выборки для 1..5 = sqrt(2.5) ≈ 1.58
    assert abs(s["std"] - 1.5811) < 0.001


def test_basic_stats_empty():
    """Пустая выборка -> нули, без деления на ноль."""
    s = basic_stats([])
    assert s["count"] == 0 and s["mean"] == 0.0


def test_moving_average():
    """Скользящее среднее с окном 3."""
    ma = moving_average([1, 2, 3, 4, 5], window=3)
    # Первые (окно-1) точек — None, дальше средние: [2, 3, 4]
    assert ma[:2] == [None, None]
    assert ma[2:] == [2.0, 3.0, 4.0]


def test_moving_average_window_larger_than_data():
    """Окно больше данных -> все None."""
    assert moving_average([1, 2], window=5) == [None, None]


def test_histogram_stats():
    """Гистограмма: мода (центр максимального бина), медиана, границы."""
    data = [1.0] * 5 + [2.0] * 10 + [3.0] * 3     # мода около 2.0
    stats = histogram_stats(data, bins=10, lo=0.0, hi=4.0)
    assert stats["max_bin_count"] == 10
    assert abs(stats["mode"] - 2.2) < 0.3          # центр бина, куда попала мода
    assert stats["median"] == 2.0
    # σ выборки (n-1) для этих данных ≈ 0.6764
    assert abs(stats["std"] - 0.6764) < 0.01
    assert len(stats["bin_counts"]) == 10
    assert len(stats["bin_edges"]) == 11           # edges на 1 больше бинов


def test_shewhart_limits():
    """Контрольная карта Шухарта: CL = среднее, UCL/LCL = ±3σ (ТЗ)."""
    data = [10.0] * 50                              # σ ≈ 0
    z = shewhart_zones(data)
    assert z["cl"] == 10.0
    assert z["ucl"] == 10.0 and z["lcl"] == 10.0
    assert z["points"] == [10.0] * 50


def test_shewhart_out_of_control_points():
    """Точка за пределами ±3σ помечается как выход процесса из-под контроля."""
    data = [10.0] * 30 + [20.0]                     # 20 при σ≈0 — явный выброс
    z = shewhart_zones(data)
    assert z["out_of_control"] == [30]              # индекс выброса


def test_cusum():
    """CUSUM: накопленное отклонение от целевого значения."""
    # Данные вокруг target=10: [9, 11, 10] -> отклонения [-1, 1, 0] -> CUSUM [-1, 0, 0]
    cs = cusum([9.0, 11.0, 10.0], target=10.0)
    assert cs == [-1.0, 0.0, 0.0]

    # Дрейф вверх: все значения 12 при target 10 -> CUSUM растёт линейно
    cs2 = cusum([12.0] * 4, target=10.0)
    assert cs2 == [2.0, 4.0, 6.0, 8.0]


def test_cusum_detects_drift():
    """Порог CUSUM: дрейф 2 ед./шаг превышает порог 10 на 6-м шаге (|12|>10)."""
    data = [12.0] * 6
    cs = cusum(data, target=10.0)
    threshold = 10.0
    first_breach = next((i for i, v in enumerate(cs) if abs(v) > threshold), None)
    assert first_breach == 5                        # значения 2,4,6,8,10,12 -> breach на 6-м


# ============================ API аналитики (с БД) ============================

async def _seed_measurements(minutes: int = 30, base: float = 100.0):
    """Засеять measurements синусоидой + шум: P за последние N минут."""
    import math
    import random
    from datetime import timedelta
    random.seed(42)  # детерминированность для проверок
    now = utcnow()
    rows = []
    for i in range(minutes * 60):   # 1 Гц
        t = now - timedelta(seconds=minutes * 60 - i)
        value = base + 10 * math.sin(i / 60) + random.uniform(-2, 2)
        rows.append(Measurement(time=t, point_code="P", value=value, quality="good"))
    async with sessionmaker() as session:
        session.add_all(rows)
        await session.commit()


async def test_analytics_series_endpoint(client):
    """GET /api/analytics/series: ряд P с усреднением и Min/Max/средним."""
    await _seed_measurements(minutes=5)
    token = await login(client)
    r = await client.get("/api/analytics/series",
                         params={"point_code": "P", "minutes": 5, "avg_s": 30},
                         headers=auth(token))
    assert r.status_code == 200
    d = r.json()
    assert d["point_code"] == "P"
    assert len(d["times"]) == len(d["values"]) > 0
    # статистика по ряду
    assert d["stats"]["count"] > 0
    assert d["stats"]["max"] >= d["stats"]["mean"] >= d["stats"]["min"]
    # значения в разумных пределах синусоиды 100±12
    assert 80 < d["stats"]["mean"] < 120


async def test_analytics_histogram_endpoint(client):
    """GET /api/analytics/histogram: бины, мода, медиана."""
    await _seed_measurements(minutes=3)
    token = await login(client)
    r = await client.get("/api/analytics/histogram",
                         params={"point_code": "P", "minutes": 3, "bins": 15},
                         headers=auth(token))
    assert r.status_code == 200
    d = r.json()
    assert len(d["bin_counts"]) == 15
    assert len(d["bin_edges"]) == 16
    assert d["max_bin_count"] > 0
    assert 80 < d["mode"] < 120


async def test_analytics_shewhart_endpoint(client):
    """GET /api/analytics/shewhart: CL/UCL/LCL + точки."""
    await _seed_measurements(minutes=3)
    token = await login(client)
    r = await client.get("/api/analytics/shewhart",
                         params={"point_code": "P", "minutes": 3},
                         headers=auth(token))
    assert r.status_code == 200
    d = r.json()
    assert d["ucl"] >= d["cl"] >= d["lcl"]
    assert len(d["points"]) > 0


async def test_analytics_cusum_endpoint(client):
    """GET /api/analytics/cusum: накопленная сумма от target."""
    await _seed_measurements(minutes=3, base=105.0)   # дрейф +5 от target 100
    token = await login(client)
    r = await client.get("/api/analytics/cusum",
                         params={"point_code": "P", "minutes": 3, "target": 100.0},
                         headers=auth(token))
    assert r.status_code == 200
    d = r.json()
    assert len(d["values"]) > 0
    # при base=105 отклонение +5/точку в среднем -> CUSUM в конце заметно > 0
    assert d["values"][-1] > 100


async def test_export_csv_endpoint(client):
    """GET /api/export/csv: выгрузка истории точки в CSV (ТЗ: экспорт Excel/CSV)."""
    await _seed_measurements(minutes=2)
    token = await login(client)
    r = await client.get("/api/export/csv",
                         params={"point_code": "P", "minutes": 2},
                         headers=auth(token))
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    body = r.text
    assert body.startswith("time,value,quality")     # заголовок CSV
    lines = body.strip().splitlines()
    assert len(lines) > 100                          # 2 минуты по 1 Гц


async def test_export_xlsx_endpoint(client):
    """GET /api/export/xlsx: настоящая xlsx-книга с данными (ТЗ: Excel)."""
    await _seed_measurements(minutes=2)
    token = await login(client)
    r = await client.get("/api/export/xlsx",
                         params={"point_code": "P", "minutes": 2},
                         headers=auth(token))
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]   # application/vnd.openxmlformats-...
    # валидность проверяем openpyxl-ом (он же пишет файл)
    import io
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(r.content))
    ws = wb.active
    assert ws.cell(row=1, column=1).value == "time"
    assert ws.max_row > 100
