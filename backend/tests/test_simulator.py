# -*- coding: utf-8 -*-
"""
Тесты симулятора: сценарный движок и карта регистров.

Проверяем ЛОГИКУ сценариев без сети — движок чистая функция tick().
Это быстро и детерминированно; сетевой путь (Modbus сервер + клиент)
проверяется в test_collector.py / test_api.py.
"""
from titan.simulator.engine import ScenarioEngine, REG_BREAKER, REG_FAULT, REG_I, REG_U


def test_base_profile_normal():
    """Нормальный режим: U в допуске, выключатель включён, аварий нет."""
    eng = ScenarioEngine()
    regs = eng.tick()
    assert 198 <= regs[REG_U] <= 242          # U около 230 В, в техно-границах
    assert regs[REG_BREAKER] == 1             # выключатель включён
    assert regs[REG_FAULT] == 0               # код аварии: «нет»


def test_short_circuit_injection():
    """КЗ: ток вырастает в разы, ставится код аварии 1, МТЗ отключает выключатель."""
    eng = ScenarioEngine()
    eng.inject("short_circuit", duration_s=5.0)
    regs = eng.tick()
    assert regs[REG_FAULT] == 1               # код аварии = КЗ
    assert regs[REG_I] > 100                  # ток вырос (норма ~25 А)
    assert regs[REG_BREAKER] == 0             # сработка МТЗ -> отключение


def test_overvoltage_injection():
    """Перенапряжение: U = 460 В — заведомо выше техно-границы 242 В."""
    eng = ScenarioEngine()
    eng.inject("overvoltage", duration_s=5.0)
    regs = eng.tick()
    assert regs[REG_U] > 400                  # U -> 460 В


def test_overload_injection():
    """Перегрузка: код аварии 2 (ток/мощность ×2.2 проверяем через валидацию)."""
    eng = ScenarioEngine()
    eng.inject("overload", duration_s=5.0)
    regs = eng.tick()
    assert regs[REG_FAULT] == 2


def test_injection_expires():
    """Сценарий истёк — значения вернулись к норме (авто-возврат)."""
    eng = ScenarioEngine()
    eng.inject("overvoltage", duration_s=0.0)
    eng.injection_until = 0.0                 # время сценария уже истекло
    regs = eng.tick()
    assert regs[REG_U] < 400                  # U вернулся к ~230 В
