# -*- coding: utf-8 -*-
"""
Осциллограммы при сработках (специф §1 п.6, ТЗ: «передача, визуализация и
архивирование осциллограмм тока и напряжения при аварийных отключениях»).

MVP: синтезированная 20-периодная запись фазных токов/напряжений при КЗ
(регистратор РЗА в реальной системе). Хранится в таблице waveforms,
просмотр — из строки журнала аварий.
"""
import logging
import math
import random
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from titan.api.auth import require_role
from titan.models.db import Waveform, utcnow
from titan.models.session import sessionmaker
from titan.timeutil import fmt_iso_ms

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/waveforms")

# Параметры записи (как у реального регистратора)
SAMPLE_RATE_HZ = 1000        # 1 кГц
PERIODS = 20                 # 20 периодов промышленной частоты
F_FREQ = 50.0                # Гц
DURATION_S = PERIODS / F_FREQ  # 0.4 с записи
N_SAMPLES = int(SAMPLE_RATE_HZ * DURATION_S)


async def generate_fault_waveform(fault_code: int, object_name: str = "ТП-1") -> int:
    """Синтезировать осциллограмму при аварии и записать в БД. -> id записи.

    Физика записи (упрощённо, но правдоподобно):
    - до момента КЗ (первые 25 % записи) — номинальный режим;
    - с момента КЗ: ток фазы A растёт до 10-кратного с апериодической
      составляющей (постоянное смещение, затухающее за ~40 мс),
      напряжение фазы A проседает;
    - фазы B/C почти не изменяются (однофазное КЗ).
    """
    if fault_code != 1:   # осциллограммы пишем только при КЗ (как в ТЗ)
        return 0

    ia: list[float] = []
    ua: list[float] = []
    ib: list[float] = []
    ub: list[float] = []
    ic: list[float] = []
    uc: list[float] = []

    i_nom, u_nom = 25.0, 230.0
    omega = 2 * math.pi * F_FREQ
    fault_sample = N_SAMPLES // 4          # момент возникновения КЗ
    tau = 0.02                             # постоянная времени апериодической сост.

    for n in range(N_SAMPLES):
        t = n / SAMPLE_RATE_HZ
        phase = omega * t
        # межфазные сдвиги 120°
        if n < fault_sample:
            # Номинальный режим: синусоиды
            ia.append(i_nom * math.sqrt(2) * math.sin(phase))
            ua.append(u_nom * math.sqrt(2) * math.sin(phase))
            ib.append(i_nom * math.sqrt(2) * math.sin(phase - 2.094))
            ub.append(u_nom * math.sqrt(2) * math.sin(phase - 2.094))
            ic.append(i_nom * math.sqrt(2) * math.sin(phase + 2.094))
            uc.append(u_nom * math.sqrt(2) * math.sin(phase + 2.094))
        else:
            # КЗ: ток фазы A — 10× с затухающей апериодической составляющей
            tf = t - fault_sample / SAMPLE_RATE_HZ
            aperiodic = i_nom * math.sqrt(2) * math.exp(-tf / tau)  # апериодическая
            ia.append(i_nom * 10 * math.sqrt(2) * math.sin(phase) + aperiodic
                      + random.uniform(-3, 3))
            ua.append(u_nom * 0.35 * math.sqrt(2) * math.sin(phase))   # просадка до 35 %
            ib.append(i_nom * math.sqrt(2) * math.sin(phase - 2.094))
            ub.append(u_nom * math.sqrt(2) * math.sin(phase - 2.094))
            ic.append(i_nom * math.sqrt(2) * math.sin(phase + 2.094))
            uc.append(u_nom * math.sqrt(2) * math.sin(phase + 2.094))

    async with sessionmaker() as session:
        import json
        wf = Waveform(
            time=utcnow(),
            object_name=object_name,
            fault_code=fault_code,
            sample_rate_hz=SAMPLE_RATE_HZ,
            # Каналы сериализуем в JSON-строки (колонки Text в БД)
            ia=json.dumps(ia), ua=json.dumps(ua), ib=json.dumps(ib),
            ub=json.dumps(ub), ic=json.dumps(ic), uc=json.dumps(uc),
        )
        session.add(wf)
        await session.commit()
        await session.refresh(wf)
        log.info("Осциллограмма КЗ записана: %d сэмплов, id=%d", N_SAMPLES, wf.id)
        return wf.id


@router.get("/{waveform_id}")
async def get_waveform(waveform_id: int,
                       user: dict = Depends(require_role("viewer"))) -> dict:
    """Получить осциллограмму по id (для графика из строки журнала аварий)."""
    async with sessionmaker() as session:
        result = await session.execute(select(Waveform).where(Waveform.id == waveform_id))
        wf = result.scalar_one_or_none()
    if wf is None:
        raise HTTPException(status_code=404, detail="Осциллограмма не найдена")
    import json
    # Время сэмпла в мс от начала записи (для оси X графика); каналы — JSON -> list
    return {
        "id": wf.id,
        "time": fmt_iso_ms(wf.time),
        "object": wf.object_name,
        "fault_code": wf.fault_code,
        "sample_rate_hz": wf.sample_rate_hz,
        "duration_ms": N_SAMPLES / SAMPLE_RATE_HZ * 1000,
        "channels": {
            "ia": json.loads(wf.ia), "ua": json.loads(wf.ua),
            "ib": json.loads(wf.ib), "ub": json.loads(wf.ub),
            "ic": json.loads(wf.ic), "uc": json.loads(wf.uc),
        },
    }
