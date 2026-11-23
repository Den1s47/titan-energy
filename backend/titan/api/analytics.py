# -*- coding: utf-8 -*-
"""
API аналитики и экспорта (специф §9.2 «Аналитика», «Отчёты»; ТЗ раздел 4).

Эндпоинты (все — viewer+, т.к. аналитика доступна на просмотр):
- GET /api/analytics/series    — временной ряд с усреднением + Min/Max/среднее
- GET /api/analytics/histogram — гистограмма (бины, мода, медиана, σ/3σ)
- GET /api/analytics/shewhart  — контрольная карта Шухарта (CL ± 3σ)
- GET /api/analytics/cusum     — карта кумулятивных сумм от target
- GET /api/export/csv          — экспорт истории точки в CSV (ТЗ: Excel/CSV)
- GET /api/export/xlsx         — экспорт в Excel (openpyxl)
"""
import csv
import io
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from titan.analytics.stats import basic_stats, cusum as cusum_fn, histogram_stats, shewhart_zones
from titan.api.auth import require_role
from titan.models.db import Measurement, utcnow
from titan.models.session import sessionmaker
from titan.timeutil import fmt_iso_ms

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# Усреднения из ТЗ (специф §10 «выбор интервала усреднения»): 30 с, 1, 3, 5, 10, 15 мин
ALLOWED_AVG_S = [30, 60, 180, 300, 600, 900]


async def _load_values(point_code: str, minutes: float) -> list[float]:
    """Загрузить значения точки за N минут (упорядоченные по времени)."""
    since = utcnow() - timedelta(minutes=minutes)
    async with sessionmaker() as session:
        result = await session.execute(
            select(Measurement.value)
            .where(Measurement.point_code == point_code, Measurement.time >= since)
            .order_by(Measurement.time)
        )
        return [v for (v,) in result.all()]


async def _load_rows(point_code: str, minutes: float):
    """Загрузить полные записи (время+значение+quality) для экспорта."""
    since = utcnow() - timedelta(minutes=minutes)
    async with sessionmaker() as session:
        result = await session.execute(
            select(Measurement)
            .where(Measurement.point_code == point_code, Measurement.time >= since)
            .order_by(Measurement.time)
        )
        return result.scalars().all()


@router.get("/analytics/series")
async def analytics_series(point_code: str, minutes: float = 15,
                           avg_s: int = 60,
                           user: dict = Depends(require_role("viewer"))) -> dict:
    """Временной ряд с усреднением (скользящее среднее) + статистика.

    avg_s — интервал усреднения (30/60/180/300/600/900 с — значения из ТЗ).
    """
    values = await _load_values(point_code, minutes)
    s = basic_stats(values)
    # Скользящее среднее по окну avg_s точек (опрос 1 Гц -> 1 точка/с)
    window = max(1, min(avg_s, len(values)))
    ma = None if not values else None  # считаем ниже с временными метками
    from titan.analytics.stats import moving_average
    ma = moving_average(values, window=window)

    async with sessionmaker() as session:
        result = await session.execute(
            select(Measurement.time)
            .where(Measurement.point_code == point_code, Measurement.time >= utcnow() - timedelta(minutes=minutes))
            .order_by(Measurement.time)
        )
        times = [fmt_iso_ms(t) for (t,) in result.all()]

    return {
        "point_code": point_code,
        "times": times,
        "values": values,
        "moving_avg": ma,
        "avg_window_s": window,
        "stats": s,
    }


@router.get("/analytics/histogram")
async def analytics_histogram(point_code: str, minutes: float = 15,
                              bins: int = Query(20, ge=5, le=100),
                              user: dict = Depends(require_role("viewer"))) -> dict:
    """Гистограмма показателя: бины, мода, медиана, σ/3σ (ТЗ «Анализ»)."""
    values = await _load_values(point_code, minutes)
    return histogram_stats(values, bins=bins)


@router.get("/analytics/shewhart")
async def analytics_shewhart(point_code: str, minutes: float = 15,
                             user: dict = Depends(require_role("viewer"))) -> dict:
    """Контрольная карта Шухарта: CL ± 3σ, точки выхода из-под контроля."""
    values = await _load_values(point_code, minutes)
    return shewhart_zones(values)


@router.get("/analytics/cusum")
async def analytics_cusum(point_code: str, minutes: float = 15,
                          target: float = 0.0,
                          user: dict = Depends(require_role("viewer"))) -> dict:
    """Карта кумулятивных сумм от целевого значения (baseline, ТЗ «КПЭ»)."""
    values = await _load_values(point_code, minutes)
    return {"point_code": point_code, "target": target, "values": cusum_fn(values, target)}


@router.get("/export/csv")
async def export_csv(point_code: str, minutes: float = 15,
                     user: dict = Depends(require_role("viewer"))) -> StreamingResponse:
    """Экспорт истории точки в CSV (ТЗ: «экспорт отчетов в стандартные форматы»)."""
    rows = await _load_rows(point_code, minutes)

    # Формируем CSV в память (строковый буфер) и отдаём потоком с attachment
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["time", "value", "quality"])
    for r in rows:
        writer.writerow([fmt_iso_ms(r.time), r.value, r.quality])
    buf.seek(0)
    filename = f"titan_{point_code}_{minutes}min.csv"
    return StreamingResponse(
        buf,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/export/xlsx")
async def export_xlsx(point_code: str, minutes: float = 15,
                      user: dict = Depends(require_role("viewer"))) -> StreamingResponse:
    """Экспорт истории точки в Excel (.xlsx, openpyxl — специф §3)."""
    from openpyxl import Workbook

    rows = await _load_rows(point_code, minutes)
    wb = Workbook()
    ws = wb.active
    ws.title = "Данные"
    ws.append(["time", "value", "quality"])          # шапка
    for r in rows:
        ws.append([fmt_iso_ms(r.time), r.value, r.quality])
    # Ширина колонок для читаемости в Excel
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 12
    ws.column_dimensions["C"].width = 10

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    filename = f"titan_{point_code}_{minutes}min.xlsx"
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
