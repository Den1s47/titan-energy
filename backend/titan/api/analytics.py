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
import math
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from titan.analytics.stats import basic_stats, cusum as cusum_fn, histogram_stats, shewhart_zones
from titan.api.auth import require_role
from titan.models.db import Event, Measurement, utcnow
from titan.models.session import sessionmaker
from titan.timeutil import fmt_iso_ms

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# Усреднения из ТЗ (специф §10 «выбор интервала усреднения»): 30 с, 1, 3, 5, 10, 15 мин
ALLOWED_AVG_S = [30, 60, 180, 300, 600, 900]

# Авто-агрегация для больших периодов (аналитика «за сутки/неделю»):
# при опросе 1 Гц сутки = 86400 точек — в график не помещаются. Поэтому
# для периода > 3 часов отдаём НЕ сырые значения, а средние по окнам
# (bagging): 24ч @ 5 мин = 288 точек, 7 дней @ 30 мин = 336 точек.
AGG_BUCKETS = {60: 30, 240: 60, 480: 120, 1440: 300, 10080: 1800}  # период_мин: окно_с


async def _load_bucketed(point_code: str, minutes: float, bucket_s: int):
    """Загрузить точку, агрегированную по окнам bucket_s (среднее за окно).

    Возвращает (times[], values[]) — средние значения по непустым окнам.
    Используется для больших периодов, где сырые данные не читаемы.
    """
    if ":" not in point_code:
        point_code = f"arm1_incomer:{point_code}"
    since = utcnow() - timedelta(minutes=minutes)
    async with sessionmaker() as session:
        result = await session.execute(
            select(Measurement.time, Measurement.value)
            .where(Measurement.point_code == point_code, Measurement.time >= since)
            .order_by(Measurement.time)
        )
        rows = result.all()
    if not rows:
        return [], []
    # Группировка по номеру окна (epoch-секунды // bucket_s)
    buckets: dict[int, list[float]] = {}
    for t, v in rows:
        key = int(t.timestamp()) // bucket_s
        buckets.setdefault(key, []).append(v)
    times: list[str] = []
    values: list[float] = []
    for key in sorted(buckets):
        vs = buckets[key]
        values.append(sum(vs) / len(vs))
        # метка времени — середина окна
        times.append(fmt_iso_ms(datetime.fromtimestamp((key * bucket_s) + bucket_s / 2, tz=timezone.utc)))
    return times, values


async def _load_events(minutes: float) -> list[dict]:
    """События журнала за период (для меток аварий на графике и сводки)."""
    since = utcnow() - timedelta(minutes=minutes)
    async with sessionmaker() as session:
        result = await session.execute(
            select(Event)
            .where(Event.time >= since, Event.cls.in_(["alarm", "abnormal"]))
            .order_by(Event.time)
        )
        evs = result.scalars().all()
    return [
        {
            "time": fmt_iso_ms(e.time),
            "priority": e.priority,
            "cls": e.cls,
            "object_name": e.object_name,
            "message": e.message,
        }
        for e in evs
    ]


async def _load_values(point_code: str, minutes: float) -> list[float]:
    """Загрузить значения точки за N минут (упорядоченные по времени).

    Multi-object (специф §16): в БД коды вида 'tp1:P', 'tp2_f3:I'. Если
    передан старый формат ('P'), берём точку ВВОДА ТП-1 ('tp1_incomer:P') —
    обратная совместимость со старыми графиками/ссылками.
    """
    # Защита от аномальных периодов (пентест 23.11): отрицательный период
    # бессмыслен, гигантский выгружает всю таблицу (DoS-вектор). Максимум —
    # 30 дней, достаточно для любых отчётов.
    if not (0 < minutes <= 60 * 24 * 30):
        # 400 с человеческим текстом (а не 500 — ранее необработанный ValueError)
        raise HTTPException(status_code=400,
                            detail="Период minutes должен быть в диапазоне (0..43200] — максимум 30 дней")
    if ":" not in point_code:
        point_code = f"arm1_incomer:{point_code}"
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
    if ":" not in point_code:
        point_code = f"arm1_incomer:{point_code}"
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
    Для больших периодов (≥ 4 ч) ряд автоматически агрегируется по окнам
    (24 ч @ 5 мин, 7 дней @ 30 мин) — иначе 86k точек нечитаемы. Вместе с
    рядом отдаются события журнала — для меток аварий прямо на графике.
    """
    # Большой период -> бакетная агрегация вместо сырых точек
    bucket_s = AGG_BUCKETS.get(int(minutes))
    if bucket_s:
        times, values = await _load_bucketed(point_code, minutes, bucket_s)
        ma = None  # агрегация уже сглаживает ряд
        window = bucket_s
    else:
        values = await _load_values(point_code, minutes)
        s_raw = basic_stats(values)
        window = max(1, min(avg_s, len(values)))
        from titan.analytics.stats import moving_average
        ma = moving_average(values, window=window) if values else []
        if ":" not in point_code:
            point_code = f"arm1_incomer:{point_code}"
        async with sessionmaker() as session:
            result = await session.execute(
                select(Measurement.time)
                .where(Measurement.point_code == point_code,
                       Measurement.time >= utcnow() - timedelta(minutes=minutes))
                .order_by(Measurement.time)
            )
            times = [fmt_iso_ms(t) for (t,) in result.all()]

    s = basic_stats(values) if values else {"count": 0}
    events = await _load_events(minutes)

    return {
        "point_code": point_code,
        "times": times,
        "values": values,
        "moving_avg": ma,
        "avg_window_s": window,
        "aggregated": bool(bucket_s),
        "stats": s,
        "events": events,
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
    """Контрольная карта Шухарта: CL ± 3σ, точки выхода из-под контроля.

    Вместе с точками отдаётся times[] — метки времени каждой точки (пожелание
    заказчика: на аномалии должно быть видно, КОГДА они были).
    """
    values = await _load_values(point_code, minutes)
    result = shewhart_zones(values)
    # Метки времени: грузим тем же запросом, что и значения (тот же период)
    if ":" not in point_code:
        point_code = f"arm1_incomer:{point_code}"
    async with sessionmaker() as session:
        db_result = await session.execute(
            select(Measurement.time)
            .where(Measurement.point_code == point_code,
                   Measurement.time >= utcnow() - timedelta(minutes=minutes))
            .order_by(Measurement.time)
        )
        result["times"] = [fmt_iso_ms(t) for (t,) in db_result.all()]
    return result


@router.get("/analytics/cusum")
async def analytics_cusum(point_code: str, minutes: float = 15,
                          target: float = 0.0,
                          user: dict = Depends(require_role("viewer"))) -> dict:
    """Карта кумулятивных сумм от целевого значения (baseline, ТЗ «КПЭ»).

    Вместе со значениями отдаётся times[] — метки времени точек (как у
    Шухарта: на отклонение должно быть видно, КОГДА оно происходило).
    """
    if ":" not in point_code:
        point_code = f"arm1_incomer:{point_code}"
    # Пентест v2: target=Infinity прошёл валидацию float (FastAPI парсит
    # "Infinity"), cusum_fn выдавал [null...] и график умирал. Отсекаем
    # нечисловые/бесконечные цели явно.
    if not math.isfinite(target):
        raise HTTPException(status_code=400,
                            detail="target должен быть конечным числом (не Infinity/NaN)")
    values = await _load_values(point_code, minutes)
    async with sessionmaker() as session:
        db_result = await session.execute(
            select(Measurement.time)
            .where(Measurement.point_code == point_code,
                   Measurement.time >= utcnow() - timedelta(minutes=minutes))
            .order_by(Measurement.time)
        )
        times = [fmt_iso_ms(t) for (t,) in db_result.all()]
    return {"point_code": point_code, "target": target,
            "values": cusum_fn(values, target), "times": times}


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


@router.get("/analytics/summary")
async def analytics_summary(point_code: str, minutes: float = 1440,
                            user: dict = Depends(require_role("viewer"))) -> dict:
    """Сводка «здоровья» объекта за период (панель КПЭ аналитики).

    Считается по сырым данным точки (1 Гц):
    - доступность: доля времени, когда BREAKER=1 (для точки BREAKER — доля
      единиц; для прочих точек недоступность оценивается по quality bad);
    - min / max / среднее;
    - число аварий и отклонений (events) за тот же период.
    """
    if ":" not in point_code:
        point_code = f"arm1_incomer:{point_code}"
    values = await _load_values(point_code, minutes)
    events = await _load_events(minutes)

    s = basic_stats(values) if values else {"count": 0}
    is_breaker = point_code.upper().endswith(":BREAKER")

    # Доступность (пентест-правка 24.11: раньше всегда ~100%, т.к. при
    # авариях качество оставалось good, а BREAKER не учитывался):
    #   1) для точки ввода: доступность = доля времени, когда ввод ВКЛЮЧЕН
    #      и есть напряжение (BREAKER=1 И U>5% номинала);
    #   2) для прочих точек — доля «good»-отсчётов.
    # Плюс вычитаются интервалы аварий (events cls=alarm/abnormal): авария
    # сработала -> объект «недоступен» до восстановления.
    if values and point_code.endswith("_incomer:U"):
        # доля времени с напряжением > 5% номинала (обесточено = недоступен)
        uptime = sum(1 for v in values if v > 11.0)   # 10 кВ / 230 В: >11 В = жив
        availability = uptime / len(values) * 100.0
    elif is_breaker and values:
        availability = sum(1 for v in values if v >= 0.5) / len(values) * 100.0
    elif values:
        async with sessionmaker() as session:
            result = await session.execute(
                select(Measurement.quality)
                .where(Measurement.point_code == point_code,
                       Measurement.time >= utcnow() - timedelta(minutes=minutes))
            )
            quals = [q for (q,) in result.all()]
        availability = sum(1 for q in quals if q == "good") / len(quals) * 100.0 if quals else 0.0
    else:
        availability = 0.0

    # Учитываем аварии за период: каждая аларм-секция уменьшает доступность.
    # Оценка консервативна: авария «стоит» минимум 5 с недоступности (средняя
    # длительность срабатывания/переброса на реальной ПС того же порядка).
    alarm_events = [e for e in events if e["cls"] == "alarm"]
    if values and alarm_events:
        period_s = minutes * 60
        outage_s = min(len(alarm_events) * 5.0, period_s)  # не более периода
        availability = max(0.0, availability - outage_s / period_s * 100.0)

    return {
        "point_code": point_code,
        "period_minutes": minutes,
        "availability_pct": round(availability, 2),
        "stats": s,
        "alarms_total": len(events),
        "alarms_critical": sum(1 for e in events if e["priority"] == "critical"),
    }


@router.get("/analytics/compare")
async def analytics_compare(point_a: str, point_b: str, minutes: float = 480,
                            user: dict = Depends(require_role("viewer"))) -> dict:
    """Сравнение двух точек на одном графике (ввод №1 vs ввод №2).

    Оба ряда агрегируются в одинаковые окна — ряды совпадают по времени
    и корректно ложатся на одну ось.
    """
    # Общее окно агрегации по периоду (как в series — читаемость)
    bucket_s = AGG_BUCKETS.get(int(minutes), 60)
    ta, va = await _load_bucketed(point_a, minutes, bucket_s)
    tb, vb = await _load_bucketed(point_b, minutes, bucket_s)
    return {
        "bucket_s": bucket_s,
        "a": {"point_code": point_a, "times": ta, "values": va},
        "b": {"point_code": point_b, "times": tb, "values": vb},
    }
