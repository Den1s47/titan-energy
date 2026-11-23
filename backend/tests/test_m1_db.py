# -*- coding: utf-8 -*-
"""
Тесты M1 (модель ПС 110/10): БД, события с «владельцем», журналы.

Изолированная in-memory SQLite — рабочая база не трогается.
"""
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from titan.collector.events import EventGenerator, get_events
from titan.collector.service import Collector, Reading
from titan.models.db import Base, Measurement


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
    """Снапшот телеметрии (multi-object ключи) записывается одним батчем."""
    c = Collector(sessionmaker=db)
    snapshot = {
        "arm1_incomer:U": Reading("U", 230.0, "good"),
        "sec1_f1:I": Reading("I", 25.0, "good"),
    }
    await c.pollers[1].persist(snapshot)
    async with db() as session:
        rows = (await session.execute(Measurement.__table__.select())).fetchall()
    assert len(rows) == 2
    codes = {r.point_code for r in rows}
    assert codes == {"arm1_incomer:U", "sec1_f1:I"}


async def test_fault_event_with_owner(db):
    """КЗ на Ф-2 секции I: alarm с object_name='Секция I, 10 кВ, Ф-2' + уставка МТЗ."""
    gen = EventGenerator(db, unit_id=1)
    ev = await gen.process_fault(1, 2, unit_id=1, tp_values={"I": 150.0})
    assert ev.cls == "alarm"
    assert ev.priority == "critical"
    assert ev.object_name == "Секция I, 10 кВ, Ф-2"    # ВЛАДЕЛЕЦ аварии
    assert "МТЗ" in ev.message
    assert ev.setpoint == 400.0
    assert ev.value == 150.0


async def test_fault_on_incomer(db):
    """КЗ на вводе (reg20=0): object_name = «Плечо А, Ввод 10 кВ №1»."""
    gen = EventGenerator(db, unit_id=1)
    ev = await gen.process_fault(1, 0, unit_id=1, tp_values={"I": 150.0})
    assert ev.object_name == "Плечо А, Ввод 10 кВ №1"


async def test_arm2_fault_names(db):
    """Авария на плече Б: имена из секции II (unit 2)."""
    gen = EventGenerator(db, unit_id=2)
    ev = await gen.process_fault(2, 1, unit_id=2, tp_values={"I": 150.0})
    assert ev.object_name == "Секция II, 10 кВ, Ф-1"


async def test_line_loss_event(db):
    """Потеря ввода 110 кВ (код 5): critical с указанием переброса."""
    gen = EventGenerator(db, unit_id=2)
    ev = await gen.process_fault(5, 0, unit_id=2, tp_values={"U": 0.0})
    assert ev.priority == "critical"
    assert "110 кВ" in ev.message
    assert "секционный" in ev.message.lower()


async def test_abnormal_per_object_and_no_duplication(db):
    """Выход per-object -> abnormal; без дублирования; возврат -> state."""
    gen = EventGenerator(db, unit_id=1)
    bad = {"sec1_f2:U": Reading("U", 460.0, "suspect")}
    good = {"sec1_f2:U": Reading("U", 230.0, "good")}

    first = await gen.process(bad)
    assert len(first) == 1 and first[0].cls == "abnormal"
    assert first[0].object_name == "Секция I, 10 кВ, Ф-2"

    second = await gen.process(bad)      # дубликата нет
    assert not second

    third = await gen.process(good)      # возврат в норму
    assert len(third) == 1 and third[0].cls == "state"


async def test_events_filter(db):
    """Выборка журналов с фильтром (основа GET /events?cls=alarm)."""
    gen = EventGenerator(db, unit_id=1)
    await gen.process_fault(1, 2, unit_id=1, tp_values={"I": 150.0})
    snap = {"sec1_f2:U": Reading("U", 460.0, "suspect")}
    await gen.process(snap)

    alarms = await get_events(db, cls="alarm")
    abnormals = await get_events(db, cls="abnormal")
    assert len(alarms) == 1
    assert len(abnormals) == 1
    assert alarms[0].object_name == "Секция I, 10 кВ, Ф-2"
