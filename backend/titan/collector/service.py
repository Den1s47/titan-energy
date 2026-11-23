# -*- coding: utf-8 -*-
"""
Коллектор (SCADA-уровень): опрос ДВУХ ТП ≥1 Гц, валидация, quality, запись в БД.

После многоуровневой модернизации (специф §16):
- опрашивает ДВА Modbus-устройства (unit 1 = ТП-1, unit 2 = ТП-2);
- live-кэш ключуется «объект:точка» ('tp1_incomer:U', 'tp2_f3:I', ...);
- каждая ТП опрашивается НЕЗАВИСИМО: потеря связи одной ТП не влияет
  на другую (реалистичная локализация аварии);
- «владелец» аварии читается из регистра 20 (индекс фидера) — событие
  в журнал попадает с конкретным объектом ('ТП-1, Ф-2').
"""
import asyncio
import logging
import random
from datetime import datetime, timezone

from pymodbus.client import AsyncModbusTcpClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from titan.collector.events import EventGenerator
from titan.config import settings
from titan.models.db import Measurement
from titan.models.session import sessionmaker as default_sessionmaker
from titan.simulator.engine import (
    REG_BREAKER, REG_FAULT, REG_FAULT_OBJ, REG_I, REG_PF, REG_P, REG_Q,
    REG_THDI, REG_THDU, REG_U,
)
from titan.simulator.factory import UNITS, get_parent

log = logging.getLogger(__name__)

# Справочник точек телеметрии (специф §5.1).
# code -> (адрес регистра, масштаб, единица, тип)
POINTS = {
    "U":       (REG_U, 1.0, "В", "analog"),
    "I":       (REG_I, 1.0, "А", "analog"),
    "P":       (REG_P, 1.0, "кВт", "analog"),
    "Q":       (REG_Q, 1.0, "кВАр", "analog"),
    "THDu":    (REG_THDU, 0.1, "%", "analog"),
    "THDi":    (REG_THDI, 0.1, "%", "analog"),
    "PF":      (REG_PF, 0.01, "о.е.", "analog"),
    "BREAKER": (REG_BREAKER, 1.0, "", "discrete"),
    "FAULT":   (REG_FAULT, 1.0, "", "discrete"),
}

# Технологические границы (специф §5.3). Выход = suspect (оранжевый ⚠️).
TECH_LIMITS = {
    "U":    (198.0, 242.0),
    "I":    (0.0, 200.0),
    "P":    (0.0, 400.0),
    "Q":    (0.0, 150.0),
    "THDu": (0.0, 8.0),
    "THDi": (0.0, 15.0),
    "PF":   (0.5, 1.0),
}

# Точки, тегируемые КОНКРЕТНЫМ объектам ТП (не «общие» регистры).
# Ввод/секция: U, BREAKER; фидера «распределяют» P/I — для MVP значения
# фидеров вычисляются из общих регистров ТП (сумма = REG_FEEDER_P).
PER_OBJECT_POINTS = {"U", "I", "P", "Q", "BREAKER"}


class Reading:
    """Одно телеизмерение: код точки, значение, quality."""

    __slots__ = ("code", "value", "quality")

    def __init__(self, code: str, value: float, quality: str) -> None:
        self.code = code
        self.value = value
        self.quality = quality


def validate(code: str, value: float, breaker_on: bool = True) -> str:
    """Валидация -> quality: good | suspect | bad (специф §5.3).

    Обесточено (BREAKER=0): нулевые аналоги — норма, не авария.
    """
    if not breaker_on and code in ("U", "I", "P", "Q") and value == 0.0:
        return "good"
    limits = TECH_LIMITS.get(code)
    if limits is None:
        return "good"
    lo, hi = limits
    return "good" if lo <= value <= hi else "suspect"


class TpPoller:
    """Опрос ОДНОЙ ТП (Modbus unit) + генерация её событий."""

    def __init__(self, unit_id: int, client: AsyncModbusTcpClient,
                 sessionmaker: async_sessionmaker) -> None:
        self.unit_id = unit_id
        self.client = client
        self.sessionmaker = sessionmaker
        self.objects = UNITS[unit_id]          # все объекты этой ТП
        self.live: dict[str, Reading] = {}     # 'tp1_incomer:U' -> Reading
        self.fault_seen = 0                    # последний виденный код аварии
        self.events = EventGenerator(sessionmaker)

    def _key(self, obj_id: str, code: str) -> str:
        """Ключ live-кэша: 'tp1_f2:I'."""
        return f"{obj_id}:{code}"

    async def read_registers(self) -> list[int] | None:
        """Прочитать регистры ТП (unit_id). None = потеря связи."""
        try:
            rr = await self.client.read_holding_registers(
                address=0, count=22, slave=self.unit_id
            )
            if rr.isError():
                raise ConnectionError(f"Modbus error: {rr}")
            return rr.registers
        except Exception as exc:
            log.warning("ТП-%d потеря связи: %s", self.unit_id, exc)
            return None

    async def poll_once(self) -> dict[str, Reading]:
        """Один опрос ТП: чтение регистров -> декодирование -> валидация.

        Распределение по фидерам: значения P/I/Q «разделяются» между
        фидерами пропорционально (MVP: равные доли + шум), общие значения
        ТП висят на вводе (incomer). BREAKER — на вводе и всех фидерах.
        """
        raw = await self.read_registers()
        comm_ok = raw is not None

        new: dict[str, Reading] = {}
        breaker_on = comm_ok and raw[REG_BREAKER] == 1
        fault_code = raw[REG_FAULT] if comm_ok else 0
        fault_obj_idx = raw[REG_FAULT_OBJ] if comm_ok else 0

        # Общие значения ТП (на вводе)
        tp_values = {}
        for code, (addr, scale, _unit, kind) in POINTS.items():
            if not comm_ok:
                continue
            tp_values[code] = raw[addr] * scale

        # --- Событие аварии (с «владельцем») ---
        if comm_ok and fault_code != 0 and fault_code != self.fault_seen:
            await self.events.process_fault(fault_code, fault_obj_idx, self.unit_id, tp_values)
        self.fault_seen = fault_code

        # --- Раскладка значений по объектам ТП ---
        feeders = [o for o in self.objects if o.kind == "feeder"]
        n_feeders = max(len(feeders), 1)
        # Имя ТП для событий (родитель любого объекта ТП)
        tp_obj = get_parent(self.objects[0].id)

        for obj in self.objects:
            for code, (addr, scale, _unit, kind) in POINTS.items():
                key = self._key(obj.id, code)
                if not comm_ok:
                    # Потеря связи этой ТП: suspect по всем её точкам
                    new[key] = Reading(code, 0.0, "suspect")
                    continue

                value = tp_values.get(code, 0.0)

                # Распределение «общих» аналогов по объектам
                if code in ("I", "P", "Q") and obj.kind == "feeder":
                    value = value / n_feeders * random.uniform(0.85, 1.15)
                elif code == "P" and obj.kind == "transformer":
                    value = value * random.uniform(0.98, 1.02)   # транс «пропускает» всё

                quality = validate(code, value, breaker_on=breaker_on)
                if kind == "discrete" and value not in (0, 1, 2):
                    quality = "bad"
                new[key] = Reading(code, value, quality)

        self.live = new
        return new

    async def persist(self, snapshot: dict[str, Reading]) -> None:
        """Батч-запись снапшота ТП в БД (только при наличии связи)."""
        now = datetime.now(timezone.utc)
        rows = [
            Measurement(time=now, point_code=key, value=r.value, quality=r.quality)
            for key, r in snapshot.items()
        ]
        async with self.sessionmaker() as session:
            session.add_all(rows)
            await session.commit()


class Collector:
    """Опрос всех ТП (2 устройства) с независимым состоянием связи."""

    def __init__(self, sessionmaker: async_sessionmaker | None = None) -> None:
        # Клиент создаётся ТОЛЬКО внутри работающего event loop (pymodbus 3.x)
        self.client = AsyncModbusTcpClient(host=settings.sim_host, port=settings.sim_port)
        self._task: asyncio.Task | None = None
        self.sessionmaker = sessionmaker or default_sessionmaker
        # Опросчик на каждую ТП
        self.pollers: dict[int, TpPoller] = {
            uid: TpPoller(uid, self.client, self.sessionmaker) for uid in UNITS
        }

    @property
    def live(self) -> dict[str, Reading]:
        """Суммарный live-кэш всех ТП ('tp1_incomer:U' -> Reading)."""
        merged: dict[str, Reading] = {}
        for poller in self.pollers.values():
            merged.update(poller.live)
        return merged

    async def poll_once(self) -> dict[str, Reading]:
        """Один цикл: опросить ОБЕ ТП (независимо), вернуть суммарный снапшот."""
        result: dict[str, Reading] = {}
        snapshots = []
        for uid, poller in self.pollers.items():
            snap = await poller.poll_once()
            snapshots.append((poller, snap))
            result.update(snap)
        # Запись в архив + события (per-ТП)
        for poller, snap in snapshots:
            if any(r.quality != "suspect" or r.value != 0.0 for r in snap.values()):
                await poller.persist(snap)
        return result

    async def start(self) -> None:
        """Подключиться и запустить фоновый цикл."""
        await self.client.connect()
        self._task = asyncio.create_task(self._run())
        log.info("Коллектор запущен: %d ТП, опрос %.1f с", len(UNITS), settings.poll_interval)

    async def stop(self) -> None:
        """Остановить цикл и соединение."""
        if self._task:
            self._task.cancel()
        self.client.close()
        log.info("Коллектор остановлен")

    async def _run(self) -> None:
        """Фоновый цикл: опрос -> БД -> события, раз в poll_interval."""
        while True:
            try:
                await self.poll_once()
            except Exception:
                log.exception("Ошибка цикла опроса")
            await asyncio.sleep(settings.poll_interval)
