# -*- coding: utf-8 -*-
"""
Генератор событий: превращает изменения телеметрии в записи журнала.

Логика (специф §5.3, §8 / ТЗ 3.3.3):
- код аварии (FAULT != 0) -> событие cls=alarm (приоритет по типу аварии);
- переход quality good -> suspect/bad -> событие cls=abnormal;
- пока параметр не вернётся в норму, событие НЕ дублируется
  (защита от спама в журнале — состояние хранится в self._active).
"""
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from titan.models.db import Event, utcnow

log = logging.getLogger(__name__)

# Человекочитаемые сообщения для кодов аварий симулятора (специф §6.2, регистр 8)
FAULT_MESSAGES = {
    1: ("short_circuit", "critical", "Аварийное отключение по сигналу МТЗ (КЗ)"),
    2: ("overload", "high", "Перегрузка: ток превышает допустимый"),
    3: ("overvoltage", "high", "Перенапряжение: выход за верхнюю границу"),
}

# Уставки/границы для сообщений (специф §8: «значение сработки / уставка»)
FAULT_SETPOINTS = {
    1: (400.0, "уставка МТЗ: 400 А"),   # ток срабатывания защиты
    2: (200.0, "граница: 200 А"),
    3: (242.0, "верхняя граница: 242 В"),
}


class EventGenerator:
    """Сопоставляет снапшоты телеметрии с активными событиями и создаёт новые.

    Держит в памяти self._active — какие события уже «подняты» и ещё
    не закрыты (параметр не вернулся в норму), чтобы не дублировать их.
    """

    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self.sessionmaker = sessionmaker
        # Активные ненормальные режимы: {code: True} — событие уже в журнале
        self._active: dict[str, bool] = {}
        # Последний виденный код аварии — чтобы создать alarm один раз на код
        self._last_fault: int = 0

    async def process(self, snapshot: dict) -> list[Event]:
        """Обработать один снапшот телеметрии -> список новых событий.

        Args:
            snapshot: {code: Reading} из Collector.poll_once()
        Returns:
            список новых Event (уже записанных в БД)
        """
        events: list[Event] = []

        # --- 1. Аварии по коду FAULT ---
        fault_code = int(snapshot.get("FAULT").value) if "FAULT" in snapshot else 0
        if fault_code != 0 and fault_code != self._last_fault:
            # Новый код аварии -> создаём событие класса alarm
            events.append(self._make_fault_event(fault_code, snapshot))
            # При КЗ — синтезируем и архивируем осциллограмму (ТЗ: осциллограммы
            # при сработках; async-задача, чтобы не тормозить цикл опроса)
            if fault_code == 1:
                asyncio.get_running_loop().create_task(self._store_waveform())
        self._last_fault = fault_code

        # --- 2. Ненормальные режимы: переходы quality good -> suspect/bad ---
        # Обесточивание не считается аварией: если выключатель ОТКЛЮЧЁН,
        # то U=0 / I=0 — это штатный режим, а не потеря напряжения.
        breaker_on = "BREAKER" in snapshot and snapshot["BREAKER"].value == 1
        for code, reading in snapshot.items():
            if code in ("FAULT", "BREAKER"):
                continue  # уже обработаны или не являются параметрами
            if not breaker_on and reading.value == 0.0:
                # Обесточено: нулевые значения — норма, пропускаем валидацию
                if self._active.get(code, False):
                    # если параметр был в аварии до отключения — закрыть событие
                    self._active[code] = False
                continue
            was_active = self._active.get(code, False)
            is_bad = reading.quality in ("suspect", "bad")
            if is_bad and not was_active:
                # Вход в ненормальный режим -> событие
                events.append(self._make_abnormal_event(code, reading))
                self._active[code] = True
            elif not is_bad and was_active:
                # Возврат в норму -> закрываем (новое событие о возврате)
                events.append(self._make_return_event(code, reading))
                self._active[code] = False

        # --- 3. Запись новых событий в БД ---
        if events:
            async with self.sessionmaker() as session:
                session.add_all(events)
                await session.commit()
            log.info("Новых событий: %d", len(events))
        return events

    async def _store_waveform(self) -> None:
        """Синтезировать и сохранить осциллограмму КЗ (фоновая задача)."""
        from titan.api.waveforms import generate_fault_waveform
        try:
            await generate_fault_waveform(fault_code=1, object_name="ТП-1")
        except Exception:
            # Осциллограмма не должна ронять цикл опроса
            log.exception("Не удалось записать осциллограмму")

    def _make_fault_event(self, fault_code: int, snapshot: dict) -> Event:
        """Сформировать событие класса alarm по коду аварии."""
        code, priority, message = FAULT_MESSAGES[fault_code]
        value = setpoint = None
        # Подставляем значение сработки и уставку (ТЗ 3.3.3: обязательные поля)
        if fault_code in FAULT_SETPOINTS:
            setpoint = FAULT_SETPOINTS[fault_code][0]
        if fault_code == 1 and "I" in snapshot:      # для КЗ значением является ток
            value = snapshot["I"].value
        elif fault_code == 2 and "I" in snapshot:
            value = snapshot["I"].value
        elif fault_code == 3 and "U" in snapshot:    # для перенапряжения — напряжение
            value = snapshot["U"].value
        return Event(
            time=utcnow(),
            object_name="ТП-1",                # в MVP один объект
            cls="alarm",
            priority=priority,
            code=code,
            message=message,
            value=value,
            setpoint=setpoint,
        )

    def _make_abnormal_event(self, code: str, reading) -> Event:
        """Сформировать событие о входе в ненормальный режим (quality suspect/bad)."""
        return Event(
            time=utcnow(),
            object_name="ТП-1",
            cls="abnormal",
            priority="high",                       # предаварийный режим
            code=f"quality_{code.lower()}",
            message=f"Выход параметра {code} за технологические границы",
            value=reading.value,
        )

    def _make_return_event(self, code: str, reading) -> Event:
        """Сформировать событие о возврате параметра в норму (информационное)."""
        return Event(
            time=utcnow(),
            object_name="ТП-1",
            cls="state",
            priority="info",
            code=f"return_{code.lower()}",
            message=f"Параметр {code} вернулся в норму",
            value=reading.value,
        )


async def get_events(sessionmaker: async_sessionmaker, cls: str | None = None,
                     acked: bool | None = None, limit: int = 200) -> list[Event]:
    """Выборка событий для журналов (специф §10: GET /events).

    Args:
        cls: фильтр по классу (alarm|abnormal|state|operator) или None — все
        acked: фильтр по квитированию (True/False/None — все)
        limit: максимум записей
    """
    async with sessionmaker() as session:
        stmt = select(Event).order_by(Event.time.desc()).limit(limit)
        if cls:
            stmt = stmt.where(Event.cls == cls)
        if acked is not None:
            stmt = stmt.where(Event.acked == acked)
        result = await session.execute(stmt)
        return list(result.scalars().all())
