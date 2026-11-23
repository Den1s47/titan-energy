# -*- coding: utf-8 -*-
"""
Коллектор (SCADA-уровень): опрос оборудования ≥1 Гц, валидация, quality-флаги.

Это «сердце» сбора данных (специф §6):
- ходит настоящим Modbus-клиентом в симулятор (потом — в реальное оборудование);
- декодирует регистры в физические величины (масштабирование ×10, ×100);
- валидирует значения по технологическим границам (специф §5.3 / ТЗ 3.1);
- ведёт live-кэш последних значений — его отдают фронтенду и API.

Запись в БД добавляется на этапе M1.
"""
import asyncio
import logging

from pymodbus.client import AsyncModbusTcpClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from titan.collector.events import EventGenerator
from titan.config import settings
from titan.models.db import Measurement
from titan.models.session import sessionmaker as default_sessionmaker
from titan.simulator.engine import (
    REG_BREAKER, REG_FAULT, REG_I, REG_PF, REG_P, REG_Q, REG_THDI, REG_THDU, REG_U,
)

log = logging.getLogger(__name__)

# Справочник точек телеметрии (специф §5.1).
# code -> (адрес регистра, масштаб для декодирования, единица измерения, тип)
# Масштаб — обратный к кодированию в симуляторе: THDu кодируется ×10, значит
# здесь делим на 10 (scale=0.1).
POINTS = {
    "U":       (REG_U, 1.0, "В", "analog"),      # напряжение
    "I":       (REG_I, 1.0, "А", "analog"),      # ток
    "P":       (REG_P, 1.0, "кВт", "analog"),    # активная мощность
    "Q":       (REG_Q, 1.0, "кВАр", "analog"),   # реактивная мощность
    "THDu":    (REG_THDU, 0.1, "%", "analog"),   # нелинейные искажения напряжения
    "THDi":    (REG_THDI, 0.1, "%", "analog"),   # нелинейные искажения тока
    "PF":      (REG_PF, 0.01, "о.е.", "analog"), # коэффициент мощности
    "BREAKER": (REG_BREAKER, 1.0, "", "discrete"),  # состояние выключателя
    "FAULT":   (REG_FAULT, 1.0, "", "discrete"),      # код аварии
}

# Технологические границы для валидации (специф §5.3):
# code -> (нижняя граница, верхняя граница). Выход за них = suspect.
# По ТЗ значения должны становиться оранжевыми с ⚠️ — это и есть suspect.
TECH_LIMITS = {
    "U":    (198.0, 242.0),   # ±10 % от номинальных 220 В
    "I":    (0.0, 200.0),
    "P":    (0.0, 400.0),
    "Q":    (0.0, 150.0),
    "THDu": (0.0, 8.0),       # норматив качества электроэнергии
    "THDi": (0.0, 15.0),
    "PF":   (0.5, 1.0),
}


class Reading:
    """Одно телеизмерение: код точки, значение и флаг качества."""

    __slots__ = ("code", "value", "quality")  # экономия памяти: фиксированные поля

    def __init__(self, code: str, value: float, quality: str) -> None:
        self.code = code          # имя точки ('U', 'I', ...)
        self.value = value        # физическое значение после декодирования
        self.quality = quality    # good | suspect | bad


def validate(code: str, value: float, breaker_on: bool = True) -> str:
    """Валидация одного значения -> quality: good | suspect | bad.

    Правила из ТЗ 3.1 / специф §5.3:
    - в границах -> good (обычная индикация);
    - за техно-границами -> suspect (оранжевый + ⚠️ на мнемосхеме);
    - физические границы и логическая несовместимость дискрет (bad)
      добавляются в последующих срезах.

    Исключение: обесточенное состояние (выключатель отключён). Нулевые U/I/P/Q
    при BREAKER=0 — ШТАТНЫЙ режим, а не потеря напряжения -> good.
    Без этого U=0 после сработки защиты висел бы оранжевым навсегда.
    """
    # Обесточено: аналоговые нули — норма (дискреты валидируем как обычно)
    if not breaker_on and code in ("U", "I", "P", "Q") and value == 0.0:
        return "good"

    limits = TECH_LIMITS.get(code)
    if limits is None:
        return "good"              # для точки нет границ — считаем достоверной
    lo, hi = limits
    return "good" if lo <= value <= hi else "suspect"


class Collector:
    """Опрос симулятора по Modbus TCP с валидацией, live-кэшем и записью в БД."""

    def __init__(self, sessionmaker: async_sessionmaker | None = None) -> None:
        # Клиент создаётся ТОЛЬКО внутри работающего event loop
        # (требование pymodbus 3.x) — поэтому Collector инстанцируется в lifespan.
        self.client = AsyncModbusTcpClient(host=settings.sim_host, port=settings.sim_port)
        self.live: dict[str, Reading] = {}    # последние значения всех точек
        self._task: asyncio.Task | None = None  # фоновая задача цикла опроса
        # БД: фабрика сессий (по умолчанию — глобальная из models.session)
        self.sessionmaker = sessionmaker or default_sessionmaker
        # Генератор событий журнала (аварии + ненормальные режимы)
        self.events = EventGenerator(self.sessionmaker)

    async def poll_once(self) -> dict[str, Reading]:
        """Один цикл опроса: прочитать 10 регистров, декодировать, провалидировать.

        Возвращает словарь {code: Reading} и обновляет self.live.
        При потере связи все точки помечаются suspect (ТЗ: «неопределённое состояние»).
        """
        try:
            # Читаем все регистры одним пакетом (адрес 0, количество 10)
            rr = await self.client.read_holding_registers(address=0, count=10)
            if rr.isError():
                raise ConnectionError(f"Modbus error: {rr}")
            raw = rr.registers
            comm_ok = True
        except Exception as exc:
            # Потеря связи: таймаут, отказ соединения и т.п.
            # Всё помечаем suspect — но данные НЕ подменяем нулями в БД.
            log.warning("Потеря связи с симулятором: %s", exc)
            raw, comm_ok = None, False

        new: dict[str, Reading] = {}
        # Состояние выключателя читаем ПЕРВЫМ: от него зависит валидация
        # аналоговых нулей при обесточивании (см. validate docstring)
        breaker_on = comm_ok and raw is not None and raw[REG_BREAKER] == 1
        for code, (addr, scale, _unit, kind) in POINTS.items():
            if not comm_ok:
                # Потеря связи: все точки suspect, значение обнуляем для UI
                new[code] = Reading(code, 0.0, "suspect")
                continue
            # Декодирование: сырой регистр × масштаб (например, THDi 74 -> 7.4 %)
            value = raw[addr] * scale
            # Валидация по техно-границам (с учётом обесточенного состояния)
            quality = validate(code, value, breaker_on=breaker_on)
            # Логическая несовместимость дискрет: состояние вне {0,1,2} — bad
            if kind == "discrete" and value not in (0, 1, 2):
                quality = "bad"
            new[code] = Reading(code, value, quality)
        self.live = new
        return new

    async def persist(self, snapshot: dict[str, Reading]) -> None:
        """Записать снапшот телеметрии в БД (батч: одна транзакция на опрос).

        При потере связи записи нет — в архиве не должно быть подставных нулей
        (ТЗ: недостоверные данные отделяются от реальных).
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        rows = [
            Measurement(time=now, point_code=code, value=r.value, quality=r.quality)
            for code, r in snapshot.items()
        ]
        async with self.sessionmaker() as session:
            session.add_all(rows)
            await session.commit()

    async def start(self) -> None:
        """Подключиться к симулятору и запустить фоновый цикл опроса."""
        await self.client.connect()
        self._task = asyncio.create_task(self._run())
        log.info("Коллектор запущен (опрос %.1f с)", settings.poll_interval)

    async def stop(self) -> None:
        """Остановить цикл опроса и закрыть соединение."""
        if self._task:
            self._task.cancel()
        # close() в pymodbus 3.9 синхронный (не await)
        self.client.close()
        log.info("Коллектор остановлен")

    async def _run(self) -> None:
        """Фоновый цикл: опрос -> запись в БД -> генерация событий, раз в poll_interval."""
        while True:
            try:
                snapshot = await self.poll_once()
                # Запись в архив — только если связь была (не при comm_loss)
                if any(r.quality != "suspect" or r.value != 0.0 for r in snapshot.values()):
                    await self.persist(snapshot)
                # Генерация событий (аварии/ненормальные) по изменениям
                await self.events.process(snapshot)
            except Exception:
                # Ошибка одного цикла не должна убивать весь коллектор
                log.exception("Ошибка цикла опроса")
            await asyncio.sleep(settings.poll_interval)
