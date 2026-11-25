# -*- coding: utf-8 -*-
"""
Симулятор «полевого уровня»: Modbus TCP сервер со сценарным движком.

Архитектура после многоуровневой модернизации (специф §16):
- ДВА Modbus-устройства (unit_id 1 = ТП-1, unit_id 2 = ТП-2) на одном порту;
- у каждого устройства свой ScenarioEngine (независимые аварии на ТП);
- ТОЧЕЧНЫЕ аварии на фидерах — оверлей per-object поверх базовой карты ТП:
  инъекция {'object': 'tp1_f2', 'scenario': 'short_circuit'} видна как
  скачок тока ТП + код аварии, «владелец» аварии — в регистре 20.

Карта holding registers на ТП (специф §6.2 + расширение):
  0: U, В      1: I, А      2: P, кВт     3: Q, кВАр
  4: THDu %×10 5: THDi %×10 6: PF ×100
  7: состояние вводного выключателя (0=откл,1=вкл,2=местное)
  8: код аварии (0=нет,1=КЗ,2=перегрузка,3=перенапряжение,4=обрыв кабеля)
  9: отложенная команда ТУ (0=нет,1=вкл,2=откл)
 20: индекс объекта аварии (1..4 = номер фидера; 0 = ввод/секция)
 21: мощность фидеров, кВт (распределение по фидерам на UI)
 12: состояние СЕКЦИОННОГО выключателя (только unit 2; 0=откл,1=вкл)
"""
import asyncio
import logging
import random
import time

from pymodbus.server import ModbusTcpServer
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusSlaveContext, ModbusServerContext

from titan.config import settings
from titan.simulator.factory import UNITS, find_object

log = logging.getLogger(__name__)

# Имена регистров — чтобы код читался адресами-смыслами, а не «магическими числами»
REG_U, REG_I, REG_P, REG_Q = 0, 1, 2, 3
REG_THDU, REG_THDI, REG_PF = 4, 5, 6
REG_BREAKER, REG_FAULT, REG_CMD = 7, 8, 9
REG_FAULT_OBJ, REG_FEEDER_P = 20, 21
REG_SECTIONAL = 12                  # секционный QF (только unit 2)

# Коды аварий симулятора (регистр 8)
FAULT_CODES = {
    "short_circuit": 1,   # КЗ (сработка МТЗ)
    "overload": 2,        # перегрузка
    "overvoltage": 3,     # перенапряжение
    "cable_break": 4,     # обрыв кабеля / потеря связи с объектом
    "line_loss_110": 5,   # потеря ввода 110 кВ (плечо обесточено целиком)
}


class ScenarioEngine:
    """Сценарный движок одной ТП: базовый профиль + точечные инъекции на объекты.

    Активная инъекция: dict {object_id, scenario, until} или None.
    События в журнал пишет коллектор, увидев код аварии в регистре 8.
    """

    def __init__(self, unit_id: int) -> None:
        self.unit_id = unit_id                 # 1 или 2 (плечо подстанции)
        self.injection: dict | None = None     # {object_id, scenario, until}
        self.breaker: int = 1                  # выключатель ввода: 1=вкл, 0=откл
        self.pending_cmd: int = 0              # отложенная ТУ-команда
        self.cmd_deadline: float = 0.0         # когда актуатор исполнит команду
        self.actuator_delay: float = 2.0       # задержка привода, с (специф §6.3)
        # Секционный QF (учитывается только у unit 2, но поле у обоих — проще)
        self.sectional: int = 0                # 0=откл (норм. режим), 1=вкл
        self.pending_sectional: int = 0        # отложенная ТУ на секционный
        # Колбэк «линия 110 восстановлена» — вызывает SimulatorService,
        # чтобы сбросить СВ (он живёт в другом движке — unit 2)
        self.on_line_restored = None

    def command_sectional(self, cmd: int) -> None:
        """ТУ-команда на СЕКЦИОННЫЙ выключатель (1=вкл, 2=откл)."""
        self.pending_sectional = cmd
        self.cmd_deadline = time.monotonic() + self.actuator_delay

    def inject(self, object_id: str, scenario: str, duration_s: float) -> None:
        """Точечная инъекция: сценарий на конкретный объект своей ТП."""
        self.injection = {
            "object_id": object_id,
            "scenario": scenario,
            "until": time.monotonic() + duration_s,
        }
        log.info("ТП-%d инъекция %s на %s (%.1f с)",
                 self.unit_id, scenario, object_id, duration_s)

    def command(self, cmd: int) -> None:
        """ТУ-команда на ввод (1=вкл, 2=откл), исполнение с задержкой привода."""
        self.pending_cmd = cmd
        self.cmd_deadline = time.monotonic() + self.actuator_delay

    def tick(self) -> dict[int, int]:
        """Один шаг: пересчитать регистры ТП (4 раза/с)."""
        now = time.monotonic()

        # Исполнение отложенной ТУ-команды (ввод)
        if self.pending_cmd and now >= self.cmd_deadline:
            self.breaker = 1 if self.pending_cmd == 1 else 0
            log.info("Плечо %d актуатор: ввод 10 кВ -> %s",
                     self.unit_id, "вкл" if self.breaker else "откл")
            self.pending_cmd = 0

        # Исполнение отложенной ТУ на СЕКЦИОННЫЙ выключатель
        if self.pending_sectional and now >= self.cmd_deadline:
            self.sectional = 1 if self.pending_sectional == 1 else 0
            log.info("Плечо %d актуатор: секционный QF -> %s",
                     self.unit_id, "вкл" if self.sectional else "откл")
            self.pending_sectional = 0

        # Завершение инъекции по времени
        if self.injection and now >= self.injection["until"]:
            log.info("Плечо %d инъекция %s завершена",
                     self.unit_id, self.injection["scenario"])
            self.injection = None
            # После ПОТЕРИ ВВОДА 110 линия «починилась» — ввод включается
            # обратно (на реальной ПС это АПВ — автоматическое повторное
            # включение). Секционный QF при этом ОТКЛЮЧАЕТСЯ автоматически:
            # держать параллельную работу двух вводов нельзя (блокировка ТУ
            # её запрещает — и симулятор соблюдает те же правила).
            # ВНИМАНИЕ: СВ физически живёт в движке unit 2 (REG_SECTIONAL
            # только у него), поэтому сброс делает SimulatorService через
            # колбэк on_line_restored — здесь сбрасываем только ввод.
            if self.breaker == 0:
                self.breaker = 1
                log.info("Плечо %d: ввод 110 кВ восстановлен (АПВ)", self.unit_id)
            if self.on_line_restored:
                self.on_line_restored(self.unit_id)

        # --- Базовый нормальный режим плеча ---
        hour = time.localtime().tm_hour
        base_p = 120 + 60 * (1 - abs(hour - 14) / 12)
        u, i, p, q = 230.0, 25.0, base_p, 40.0
        thdu, thdi, pf = 3.0, 7.0, 0.96
        fault_code, fault_obj_idx = 0, 0

        inj = self.injection
        if inj:
            scenario = inj["scenario"]
            obj = find_object(inj["object_id"])
            # Индекс фидера (1..4); 0 = авария на вводе/секции/трансформаторе
            fault_obj_idx = 0
            if obj is not None and obj.kind == "feeder":
                fault_obj_idx = int(obj.id.rsplit("_f", 1)[1])

            if scenario == "short_circuit":
                # КЗ на линии: ток ×6, сработка МТЗ отключает ВВОД плеча
                i *= 6.0
                pf = 0.15
                self.breaker = 0
                fault_code = FAULT_CODES["short_circuit"]
            elif scenario == "overload":
                # Перегрузка: выход за техно-границы (suspect/оранжевый на UI)
                i *= 6.0
                p *= 6.0
                thdi += 12.0
                q *= 2.0
                fault_code = FAULT_CODES["overload"]
            elif scenario == "overvoltage":
                # Перенапряжение на секции: U → 460 В
                u = 460.0
                fault_code = FAULT_CODES["overvoltage"]
            elif scenario == "cable_break":
                # Обрыв кабеля фидера: мощность плеча проседает (фидер отвалился),
                # вводные значения остаются «нормальными» по границам
                p *= 0.55
                i *= 0.55
                fault_code = FAULT_CODES["cable_break"]
            elif scenario == "line_loss_110":
                # ПОТЕРЯ ВВОДА 110 кВ: всё плечо обесточено (Q-110 отключается
                # защитой ЛЭП), секция I/II без питания. Переброс — через
                # секционный QF вручную диспетчером (ключевой сценарий ТЗ).
                self.breaker = 0
                fault_code = FAULT_CODES["line_loss_110"]

        # Шум — «реальность» измерений
        u += random.uniform(-1.5, 1.5)
        i *= random.uniform(0.97, 1.03)
        p *= random.uniform(0.97, 1.03)
        thdu += random.uniform(-0.3, 0.3)
        thdi += random.uniform(-0.5, 0.5)

        # Обесточено: выключатель ввода отключён (КЗ — исключение: ток виден
        # до сработки, перегрузка при отключённом вводе не имеет смысла).
        # ИСКЛЮЧЕНИЕ для секционного режима: если секционный QF ВКЛЮЧЕН и
        # чужой ввод работает — секция ЗАПИТАНА «через СВ» (переброс).
        # Проверку делает SimulatorService._run: он знает состояние ОБЕИХ половин.
        if self.breaker == 0 and (
            inj is None or inj["scenario"] in ("overload", "cable_break", "line_loss_110")
        ):
            u, i, p, q = 0.0, 0.0, 0.0, 0.0

        return {
            REG_U: int(u), REG_I: int(i), REG_P: int(p), REG_Q: int(q),
            REG_THDU: int(thdu * 10), REG_THDI: int(thdi * 10), REG_PF: int(pf * 100),
            REG_BREAKER: self.breaker,
            REG_FAULT: fault_code,
            REG_CMD: self.pending_cmd,
            REG_FAULT_OBJ: fault_obj_idx,
            REG_FEEDER_P: int(p * 0.6),   # ~60% мощности плеча уходит в фидеры
            REG_SECTIONAL: self.sectional,
        }


class SimulatorService:
    """Modbus TCP сервер с ДВУМЯ устройствами (unit 1 = ТП-1, unit 2 = ТП-2).

    Жизненный цикл — из lifespan FastAPI (embedded-режим, специф §2.3).
    """

    def __init__(self) -> None:
        # Движок на каждую ТП
        self.engines: dict[int, ScenarioEngine] = {
            uid: ScenarioEngine(uid) for uid in UNITS
        }
        self._task: asyncio.Task | None = None
        self._server = None
        self._tcp_server = None

    def get_engine(self, unit_id: int) -> ScenarioEngine:
        """Движок ТП по unit_id (для API инъекций и ТУ)."""
        return self.engines[unit_id]

    def start(self) -> None:
        """Запустить Modbus TCP сервер (оба устройства на одном порту)."""
        # Хранилище на каждое устройство: unit_id -> SlaveContext
        slaves = {
            uid: ModbusSlaveContext(hr=ModbusSequentialDataBlock(0, [0] * 64))
            for uid in UNITS
        }
        self._server = ModbusServerContext(slaves=slaves, single=False)
        self._task = asyncio.create_task(self._run())
        log.info("Симулятор Modbus TCP: 2 устройства на %s:%s (unit 1,2)",
                 settings.sim_host, settings.sim_port)

    async def stop(self) -> None:
        """Остановить симулятор (с ожиданием завершения цикла и TCP-сервера).

        Нужен именно await-вариант: тесты поднимают/гасят lifespan многократно —
        без ожидания старый TCP-сервер не успевает освободить порт 5021,
        и следующий lifespan получает «address already in use»/мёртвые регистры.
        """
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("Симулятор остановлен")

    async def _run(self) -> None:
        """Главный цикл: сервер + обновление регистров обеих ТП каждые 0.25 с.

        «Обрыв кабеля» НА ВВОДЕ ТП = потеря связи: регистры этой ТП замирают
        (устройство «умирает»), вторая ТП продолжает работать — авария
        локальна, как на реальном заводе. Восстановление — по истечении.
        """
        server = ModbusTcpServer(
            context=self._server, address=(settings.sim_host, settings.sim_port)
        )
        self._tcp_server = server
        server_task = asyncio.create_task(server.serve_forever())
        await asyncio.sleep(0.3)   # дать серверу занять порт

        # Активные comm_loss per-ТП: {unit_id: until_monotonic}
        comm_lost: dict[int, float] = {}

        # Колбэк восстановления линии 110: сбросить СВ в unit 2 (параллельная
        # работа вводов запрещена — те же правила, что проверяет ТУ-блокировка)
        def _on_line_restored(uid: int) -> None:
            if self.engines[2].sectional == 1:
                self.engines[2].sectional = 0
                log.info("Секционный QF автоматически отключён после "
                         "восстановления ввода 110 (параллельная работа запрещена)")

        for engine in self.engines.values():
            engine.on_line_restored = _on_line_restored

        try:
            while True:
                now = time.monotonic()
                for uid, engine in self.engines.items():
                    inj = engine.injection

                    # «Обрыв кабеля» на ВВОДЕ = потеря связи всей ТП
                    if inj and inj["scenario"] == "cable_break" and \
                            inj["object_id"].endswith("_incomer"):
                        comm_lost[uid] = inj["until"]
                        engine.injection = None    # сценарий «сыграл»
                        log.info("ТП-%d потеря связи (comm_loss)", uid)

                    # ТП в comm_loss: регистры заморожены (не обновляем)
                    if uid in comm_lost:
                        if now >= comm_lost[uid]:
                            del comm_lost[uid]
                            log.info("ТП-%d связь восстановлена", uid)
                        continue

                    regs = engine.tick()

                    # --- СЕКЦИОННЫЙ ПЕРЕБРОС (ключевой сценарий ТЗ) ---
                    # Плечо обесточено (ввод 0) + секционный QF включён +
                    # СОСЕДНЕЕ плечо в работе -> секция ЗАПИТАНА через СВ:
                    # имитируем U/I/P от соседа (≈90% номинала плеча).
                    other = self.engines[2 if uid == 1 else 1]
                    sectional = self.engines[2].sectional   # СВ живёт в unit 2
                    if (engine.breaker == 0 and sectional == 1
                            and other.breaker == 1
                            and not (engine.injection
                                     and engine.injection["scenario"] == "short_circuit")):
                        hour = time.localtime().tm_hour
                        base_p = 120 + 60 * (1 - abs(hour - 14) / 12)
                        regs[REG_U] = 228
                        regs[REG_I] = int(base_p / 230 * 0.45 * 10)
                        regs[REG_P] = int(base_p * 0.9)
                        regs[REG_Q] = 36
                        regs[REG_THDU] = 30
                        regs[REG_THDI] = 72
                        regs[REG_PF] = 95
                        # FAULT остаётся как есть (аларм «потеря ввода» виден,
                        # но секция запитана — диспетчер видит результат переброса)

                    store = self._server[uid].store["h"]
                    # pymodbus 3.x смещает адреса на +1 — пишем addr+1
                    for addr, val in regs.items():
                        store.setValues(addr + 1, [val])
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            await server.shutdown()
            raise
