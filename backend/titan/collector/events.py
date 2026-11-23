# -*- coding: utf-8 -*-
"""
Генератор событий: превращает изменения телеметрии в записи журнала.

Multi-object (специф §16) + модель ПС 110/10 (MVP_SCADA_Denis):
- process_fault() — авария с «владельцем»: object_name = «Секция I, Ф-2»
  (событие привязано к КОНКРЕТНОМУ объекту — по нему диспетчер ищет
  место аварии на уровнях мнемосхемы);
- process() — ненормальные режимы per-object (ключ кеша 'sec1_f2:U'),
  без дублирования до возврата в норму.
"""
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from titan.models.db import Event, utcnow

log = logging.getLogger(__name__)

# Человекочитаемые сообщения по кодам аварий симулятора (регистр 8)
FAULT_MESSAGES = {
    1: ("short_circuit", "critical", "КЗ на линии, сработка МТЗ"),
    2: ("overload", "high", "Перегрузка: ток превышает допустимый"),
    3: ("overvoltage", "high", "Перенапряжение на секции"),
    4: ("cable_break", "critical", "Обрыв кабеля: потеря связи с объектом"),
    5: ("line_loss_110", "critical", "Потеря ввода 110 кВ — плечо обесточено, требуется переброс через секционный QF"),
}

# Уставки/границы для сообщений (специф §8: «значение сработки / уставка»)
FAULT_SETPOINTS = {
    1: 400.0,     # уставка МТЗ, А
    2: 200.0,     # граница тока, А
    3: 242.0,     # верхняя граница напряжения, В
}

# Префикс id объектов по unit: unit 1 -> arm1/sec1, unit 2 -> arm2/sec2
UNIT_PREFIX = {1: {"arm": "arm1", "sec": "sec1"}, 2: {"arm": "arm2", "sec": "sec2"}}


class EventGenerator:
    """Создание событий журнала для ОДНОГО плеча ПС (передаётся unit_id).

    Активные ненормальные режимы хранятся per-object+per-code
    ('sec1_f2:U' -> True), чтобы не дублировать события.
    """

    def __init__(self, sessionmaker: async_sessionmaker, unit_id: int = 1) -> None:
        self.sessionmaker = sessionmaker
        self.unit_id = unit_id
        # Активные ненормальные режимы: {'sec1_f2:U': True}
        self._active: dict[str, bool] = {}
        self._last_fault = 0

    def _obj_name(self, obj_id: str) -> str:
        """Человекочитаемое имя объекта + родитель: 'Секция I, Ф-2'."""
        from titan.simulator.factory import find_object, get_parent
        obj = find_object(obj_id)
        if obj is None:
            return f"Плечо {self.unit_id}"
        parent = get_parent(obj.id)
        if parent and parent.id != "ps":
            return f"{parent.name}, {obj.name}"
        return obj.name

    async def process_fault(self, fault_code: int, fault_obj_idx: int,
                            unit_id: int, tp_values: dict) -> Event:
        """Событие класса alarm с «владельцем» аварии.

        Args:
            fault_code: код из регистра 8 (1=КЗ, 2=перегрузка, 3=перенапр, 4=обрыв)
            fault_obj_idx: индекс фидера из регистра 20 (0 = ввод/секция)
            unit_id: ТП, где произошла авария
            tp_values: декодированные общие значения ТП {code: value}
        """
        code, priority, message = FAULT_MESSAGES[fault_code]

        # Идентификация объекта-владельца (модель ПС 110/10)
        pref = UNIT_PREFIX.get(unit_id, UNIT_PREFIX[1])
        if fault_obj_idx > 0:
            obj_id = f"{pref['sec']}_f{fault_obj_idx}"    # фидер секции
        else:
            obj_id = f"{pref['arm']}_incomer"             # ввод 10 кВ плеча
        obj_name = self._obj_name(obj_id)

        # Значение сработки + уставка (ТЗ 3.3.3)
        setpoint = FAULT_SETPOINTS.get(fault_code)
        value = None
        if fault_code in (1, 2) and "I" in tp_values:
            value = tp_values["I"]
        elif fault_code == 3 and "U" in tp_values:
            value = tp_values["U"]

        event = Event(
            time=utcnow(),
            object_name=obj_name,
            cls="alarm",
            priority=priority,
            code=code,
            message=message,
            value=value,
            setpoint=setpoint,
        )
        async with self.sessionmaker() as session:
            session.add(event)
            await session.commit()
        log.info("Аларм: %s | %s (знач=%s, уставка=%s)", obj_name, message, value, setpoint)

        # Осциллограмму при КЗ пишет КОЛЛЕКТОР (см. service.py) после чтения
        # регистров — здесь фоновую задачу не создаём: «висячая» task держит
        # event loop теста и блокирует следующие тесты в прогоне.
        return event

    async def process(self, snapshot: dict) -> list[Event]:
        """Ненормальные режимы per-object: переходы quality good -> suspect/bad.

        Args:
            snapshot: {'tp1_f2:U': Reading, ...} (ключ = 'obj_id:code')
        """
        events: list[Event] = []
        now = utcnow()

        for key, reading in snapshot.items():
            if ":FAULT" in key or ":BREAKER" in key:
                continue   # дискреты не участвуют в «ненормальных режимах»
            obj_id, code = key.split(":", 1)

            was_active = self._active.get(key, False)
            is_bad = reading.quality in ("suspect", "bad")

            if is_bad and not was_active:
                events.append(Event(
                    time=now,
                    object_name=self._obj_name(obj_id),
                    cls="abnormal",
                    priority="high",
                    code=f"quality_{code.lower()}",
                    message=f"Выход параметра {code} за технологические границы",
                    value=reading.value,
                ))
                self._active[key] = True
            elif not is_bad and was_active:
                events.append(Event(
                    time=now,
                    object_name=self._obj_name(obj_id),
                    cls="state",
                    priority="info",
                    code=f"return_{code.lower()}",
                    message=f"Параметр {code} вернулся в норму",
                    value=reading.value,
                ))
                self._active[key] = False

        if events:
            async with self.sessionmaker() as session:
                session.add_all(events)
                await session.commit()
            log.info("Новых событий: %d", len(events))
        return events

    async def _store_waveform(self, object_name: str) -> None:
        """Синтезировать и сохранить осциллограмму КЗ (фоновая задача)."""
        from titan.api.waveforms import generate_fault_waveform
        try:
            await generate_fault_waveform(fault_code=1, object_name=object_name)
        except Exception:
            log.exception("Не удалось записать осциллограмму")


async def get_events(sessionmaker: async_sessionmaker, cls: str | None = None,
                     acked: bool | None = None, limit: int = 200) -> list[Event]:
    """Выборка событий для журналов (специф §10: GET /events)."""
    async with sessionmaker() as session:
        stmt = select(Event).order_by(Event.time.desc()).limit(limit)
        if cls:
            stmt = stmt.where(Event.cls == cls)
        if acked is not None:
            stmt = stmt.where(Event.acked == acked)
        result = await session.execute(stmt)
        return list(result.scalars().all())
