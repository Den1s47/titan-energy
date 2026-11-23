# -*- coding: utf-8 -*-
"""
Симулятор «полевого уровня»: Modbus TCP сервер со сценарным движком.

Это «виртуальное оборудование» вместо реальных счётчиков/РЗА (специф §6):
- поднимает настоящий Modbus TCP сервер (порт 5020);
- 4 раза в секунду пересчитывает регистры по сценарию;
- умеет «играть» аварии: КЗ, перегрузка, перенапряжение, отказ привода,
  потеря связи — чтобы демонстрировать реакции системы.

Карта holding registers (специф §6.2):
  0: U, В      1: I, А      2: P, кВт     3: Q, кВАр
  4: THDu %×10 5: THDi %×10 6: PF ×100
  7: состояние выключателя (0=откл, 1=вкл, 2=местное)
  8: код аварии (0=нет, 1=КЗ, 2=перегрузка, 3=перенапряжение)
  9: отложенная команда ТУ (0=нет, 1=вкл, 2=откл)
"""
import asyncio
import logging
import random
import time

from pymodbus.server import ModbusTcpServer
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusSlaveContext, ModbusServerContext

from titan.config import settings

log = logging.getLogger(__name__)

# Имена регистров — чтобы код читался адресами-смыслами, а не «магическими числами»
REG_U, REG_I, REG_P, REG_Q = 0, 1, 2, 3
REG_THDU, REG_THDI, REG_PF = 4, 5, 6
REG_BREAKER, REG_FAULT, REG_CMD = 7, 8, 9


class ScenarioEngine:
    """Сценарный движок: базовый профиль нагрузки + инъекции аварий.

    Ничего не знает про сеть — просто при каждом tick() выдаёт словарь
    {адрес регистра: значение}. Сетью занимается SimulatorService.
    """

    def __init__(self) -> None:
        self.injection: str | None = None      # активный сценарий (None = норма)
        self.injection_until: float = 0.0      # момент окончания сценария (monotonic-время)
        self.breaker: int = 1                  # выключатель: 1=вкл, 0=откл
        self.pending_cmd: int = 0              # отложенная ТУ-команда (0=нет, 1=вкл, 2=откл)
        self.cmd_deadline: float = 0.0         # когда актуатор должен исполнить команду
        self.actuator_delay: float = 2.0       # задержка привода, с (специф §6.3: 1-3 с)

    def inject(self, scenario: str, duration_s: float = 10.0) -> None:
        """Запустить сценарий аварии на заданное время, потом авто-возврат в норму."""
        self.injection = scenario
        self.injection_until = time.monotonic() + duration_s
        log.info("Инъекция сценария: %s на %.1f с", scenario, duration_s)

    def command(self, cmd: int) -> None:
        """Принять команду телеуправления (1=вкл, 2=откл).

        Мгновенно не исполняется: реальный привод переключается с задержкой,
        поэтому запоминаем дедлайн — исполнение произойдёт в tick().
        """
        self.pending_cmd = cmd
        self.cmd_deadline = time.monotonic() + self.actuator_delay

    def tick(self) -> dict[int, int]:
        """Один шаг сценария: пересчитать все регистры (вызывается 4 раза/с)."""
        now = time.monotonic()

        # Завершение сценария, когда его время истекло
        # ('comm_loss' не сбрасываем сам — потеря связи снимается рестартом симулятора)
        if self.injection and now >= self.injection_until and self.injection != "comm_loss":
            log.info("Сценарий %s завершён", self.injection)
            self.injection = None

        # Исполнение отложенной ТУ-команды: время пришло и привод не «сломан»
        # (при actuator_fail команда «зависает» — коллектор получит таймаут ТУ)
        if self.pending_cmd and now >= self.cmd_deadline:
            if self.injection != "actuator_fail":
                self.breaker = 1 if self.pending_cmd == 1 else 0
                log.info("Актуатор исполнил команду: выключатель -> %s",
                         "вкл" if self.breaker else "откл")
            self.pending_cmd = 0

        # Базовый (нормальный) режим: суточный профиль — пик потребления около 14:00
        hour = time.localtime().tm_hour
        base_p = 120 + 60 * (1 - abs(hour - 14) / 12)  # базовая активная мощность, кВт
        u, i, p, q = 230.0, 25.0, base_p, 40.0          # U В / I А / P кВт / Q кВАр
        thdu, thdi, pf = 3.0, 7.0, 0.96                 # THDu % / THDi % / коэф. мощности

        # Инъекции: модифицируем нормальные значения под активный сценарий
        inj = self.injection
        if inj == "short_circuit":
            # КЗ: ток ×6, PF падает (ток почти реактивный),
            # защита МТЗ «срабатывает» — выключатель отключается
            i *= 6.0
            pf = 0.15
            self.breaker = 0
        elif inj == "overload":
            # Перегрузка: ток/мощность ×2.2, гармоники растут так, чтобы
            # ВЫЙТИ за технологические границы (I > 200 А, THDi > 15 %) —
            # тогда коллектор пометит их suspect (оранжевый ⚠️ на UI).
            i *= 6.0            # ~25 А -> ~150 А (выход за границу 200 при шуме)
            p *= 6.0
            thdi += 12.0        # 7 % -> ~19 % (выход за 15 %)
            q *= 2.0
        elif inj == "overvoltage":
            # Перенапряжение: U → 460 В (граница 242 В) → suspect по U
            u = 460.0

        # Небольшой шум — чтобы данные выглядели как реальные измерения
        u += random.uniform(-1.5, 1.5)
        i *= random.uniform(0.97, 1.03)
        p *= random.uniform(0.97, 1.03)
        thdu += random.uniform(-0.3, 0.3)
        thdi += random.uniform(-0.5, 0.5)

        # Обесточено: если выключатель отключён — напряжений и токов нет.
        # Исключение — КЗ: ток КЗ должен быть виден ДО сработки защиты.
        if self.breaker == 0 and inj != "short_circuit":
            u, i, p, q = 0.0, 0.0, 0.0, 0.0

        # Кодирование в регистры: дробные значения пишем целыми с множителем
        # (THDu/THDi ×10, PF ×100 — так они «влезают» в 16-битный регистр)
        return {
            REG_U: int(u), REG_I: int(i), REG_P: int(p), REG_Q: int(q),
            REG_THDU: int(thdu * 10), REG_THDI: int(thdi * 10), REG_PF: int(pf * 100),
            REG_BREAKER: self.breaker,
            REG_FAULT: {"short_circuit": 1, "overload": 2, "overvoltage": 3}.get(inj or "", 0),
            REG_CMD: self.pending_cmd,
        }


class SimulatorService:
    """Обёртка над симулятором: запуск/остановка Modbus сервера + цикл сценария.

    Жизненный цикл управляется из lifespan FastAPI (embedded-режим, специф §2.3).
    """

    def __init__(self) -> None:
        self.engine = ScenarioEngine()           # логика значений регистров
        self._task: asyncio.Task | None = None   # фоновая задача: сервер + цикл обновления
        self._server = None                      # контекст Modbus (datastore с регистрами)
        self._tcp_server = None                  # объект ModbusTcpServer (для shutdown)

    def start(self) -> None:
        """Запустить Modbus TCP сервер на host:port из настроек."""
        # Хранилище holding-регистров (hr): 100 ячеек от адреса 0
        store = ModbusSlaveContext(hr=ModbusSequentialDataBlock(0, [0] * 100))
        # single=True: один узел, unit_id игнорируется (как у простых счётчиков)
        self._server = ModbusServerContext(slaves=store, single=True)
        self._task = asyncio.create_task(self._run())
        log.info("Симулятор Modbus TCP запускается на %s:%s", settings.sim_host, settings.sim_port)

    async def stop(self) -> None:
        """Остановить симулятор: отменить фоновую задачу."""
        if self._task:
            self._task.cancel()
        log.info("Симулятор остановлен")

    async def _run(self) -> None:
        """Главный цикл: держит сервер + каждые 0.25 с обновляет значения регистров.

        При comm_loss: server.shutdown() закрывает TCP-соединения (в т.ч.
        активное соединение коллектора) — клиент получает отказ/таймаут,
        коллектор помечает все точки suspect («неопределённое состояние» ТЗ).
        """
        # Создаём ОБЪЕКТ сервера (не хелпер StartAsyncTcpServer), чтобы иметь
        # доступ к .shutdown() для сценария «потеря связи»
        from pymodbus.server import ModbusTcpServer
        server = ModbusTcpServer(
            context=self._server, address=(settings.sim_host, settings.sim_port)
        )
        self._tcp_server = server   # ссылка для остановки
        server_task = asyncio.create_task(server.serve_forever())
        # Даём серверу время занять порт, прежде чем принимать сценарии
        await asyncio.sleep(0.3)
        try:
            while True:
                # Потеря связи: гасим сервер целиком (рвёт активные соединения)
                if self.engine.injection == "comm_loss":
                    log.warning("Потеря связи: Modbus-сервер отключается (comm_loss)")
                    await server.shutdown()   # закрыть слушателя и соединения
                    # Ждём окончания сценария: когда comm_loss снимется (инъекция
                    # истекает по времени), ЗАПУСКАЕМ СЕРВЕР ЗАНОВО — «связь
                    # восстановилась». Без этого система оставалась мёртвой
                    # навсегда и ТУ не работало после потери связи.
                    log.info("Связь потеряна — ожидаем восстановления...")
                    while self.engine.injection == "comm_loss":
                        await asyncio.sleep(0.25)
                        # comm_loss НЕ снимается в tick() (там исключение), поэтому
                        # тут сами снимаем: инъекция "потеря связи" конечна по времени
                        if time.monotonic() >= self.engine.injection_until:
                            self.engine.injection = None
                            log.info("Сценарий comm_loss завершён")
                    log.info("Связь восстановлена: перезапускаем Modbus-сервер")
                    break                     # выходим во внешний цикл: пересоздадим сервер
                regs = self.engine.tick()
                # pymodbus 3.x внутри смещает адреса на +1 (getValues делает address+=1),
                # поэтому пишем по адресу addr+1, чтобы клиент читал с адреса addr.
                for addr, val in regs.items():
                    self._server[0].store["h"].setValues(addr + 1, [val])
                await asyncio.sleep(0.25)  # 4 пересчёта в секунду (специф §2.1)
        except asyncio.CancelledError:
            # Корректная остановка: гасим и сервер
            await server.shutdown()
            raise

        # Рестарт после потери связи: пересоздаём сервер и цикл заново
        # (рекурсия безопасна: глубина = число comm_loss-сценариев за жизнь,
        # на практике единицы; состояние движка сохраняется)
        await self._run()
