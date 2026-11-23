# -*- coding: utf-8 -*-
"""
Тесты срезов M1: запись измерений в БД + генерация событий журнала.

Используем ОТДЕЛЬНУЮ in-memory SQLite — тесты не трогают рабочую базу titan.db.
"""
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from titan.collector.events import EventGenerator
from titan.collector.service import Collector, Reading
from titan.models.db import Base, Event, Measurement
from titan.models.session import init_db


@pytest.fixture
async def db():
    """Изолированная in-memory БД на время теста."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield maker
    await engine.dispose()


async def test_persist_measurements(db):
    """Снапшот телеметрии записывается в measurements одним батчем."""
    c = Collector(sessionmaker=db)
    snapshot = {
        "U": Reading("U", 230.0, "good"),
        "I": Reading("I", 25.0, "good"),
    }
    await c.persist(snapshot)
    async with db() as session:
        rows = (await session.execute(Measurement.__table__.select())).fetchall()
    assert len(rows) == 2
    codes = {r.point_code for r in rows}
    assert codes == {"U", "I"}
    # quality сохраняется вместе со значением
    u_row = next(r for r in rows if r.point_code == "U")
    assert u_row.value == 230.0 and u_row.quality == "good"


async def test_fault_event_on_short_circuit(db):
    """Сценарий приёмки M1: код аварии КЗ -> событие класса alarm в журнале."""
    gen = EventGenerator(db)
    snapshot = {
        "I": Reading("I", 150.0, "suspect"),      # ток КЗ
        "FAULT": Reading("FAULT", 1.0, "good"),   # код аварии = КЗ
    }
    events = await gen.process(snapshot)
    alarms = [e for e in events if e.cls == "alarm"]
    assert len(alarms) == 1
    assert alarms[0].priority == "critical"       # КЗ = критический уровень ISA-18.2
    assert "МТЗ" in alarms[0].message
    assert alarms[0].setpoint == 400.0            # уставка защиты в сообщении


async def test_abnormal_event_and_no_duplication(db):
    """Выход за границы -> abnormal; пока не вернулся в норму — НЕ дублируется."""
    gen = EventGenerator(db)
    bad = {"U": Reading("U", 460.0, "suspect")}
    good = {"U": Reading("U", 230.0, "good")}

    first = await gen.process(bad)
    assert any(e.cls == "abnormal" for e in first)

    # Ещё раз плохое значение — дубликата быть не должно
    second = await gen.process(bad)
    assert not any(e.cls == "abnormal" for e in second)

    # Возврат в норму -> информационное событие
    third = await gen.process(good)
    assert any(e.cls == "state" and "вернулся" in e.message for e in third)


async def test_events_api_filter(db):
    """Выборка журналов с фильтром по классу (основа GET /events?cls=alarm)."""
    gen = EventGenerator(db)
    await gen.process({"FAULT": Reading("FAULT", 1.0, "good"),
                       "I": Reading("I", 150.0, "suspect")})
    await gen.process({"U": Reading("U", 460.0, "suspect")})

    from titan.collector.events import get_events
    alarms = await get_events(db, cls="alarm")
    abnormals = await get_events(db, cls="abnormal")
    assert len(alarms) == 1
    # два ненормальных: ток при КЗ (suspect) и напряжение 460 В
    assert len(abnormals) == 2
