# -*- coding: utf-8 -*-
"""
Тесты симулятора (multi-object, 23.11): движок ТП, точечные инъекции.

Каждый тест создаёт движок ТП (unit_id=1) и проверяет ЛОГИКУ сценариев
без сети — tick() чистая функция {регистр: значение}.
Сетевой путь (2 устройства на порту) покрывают тесты коллектора.
"""
from titan.simulator.engine import (
    ScenarioEngine, REG_BREAKER, REG_FAULT, REG_FAULT_OBJ, REG_I, REG_U,
)


def test_base_profile_normal():
    """Норма: U в допуске, ввод включён, аварий нет."""
    eng = ScenarioEngine(1)
    regs = eng.tick()
    assert 198 <= regs[REG_U] <= 242
    assert regs[REG_BREAKER] == 1
    assert regs[REG_FAULT] == 0


def test_short_circuit_on_feeder():
    """КЗ на Ф-2: код 1, ток ×6, МТЗ отключает ввод, владелец = фидер 2."""
    eng = ScenarioEngine(1)
    eng.inject("tp1_f2", "short_circuit", duration_s=5)
    regs = eng.tick()
    assert regs[REG_FAULT] == 1
    assert regs[REG_I] > 100
    assert regs[REG_BREAKER] == 0
    assert regs[REG_FAULT_OBJ] == 2       # Ф-2


def test_overload_on_feeder():
    """Перегрузка Ф-1: код 2, владелец = фидер 1."""
    eng = ScenarioEngine(1)
    eng.inject("tp1_f1", "overload", duration_s=5)
    regs = eng.tick()
    assert regs[REG_FAULT] == 2
    assert regs[REG_FAULT_OBJ] == 1


def test_overvoltage_on_bus():
    """Перенапряжение на секции: код 3, владелец = 0 (ввод/секция)."""
    eng = ScenarioEngine(1)
    eng.inject("tp1_bus", "overvoltage", duration_s=5)
    regs = eng.tick()
    assert regs[REG_FAULT] == 3
    assert regs[REG_U] > 400
    assert regs[REG_FAULT_OBJ] == 0


def test_injection_expires():
    """Инъекция истекла — значения вернулись к норме."""
    eng = ScenarioEngine(1)
    eng.inject("tp1_bus", "overvoltage", duration_s=0)
    eng.injection["until"] = 0.0          # время уже истекло
    regs = eng.tick()
    assert regs[REG_U] < 400
    assert regs[REG_FAULT] == 0


def test_tp2_independent():
    """Движки ТП независимы: авария на ТП-2 не влияет на ТП-1."""
    e1, e2 = ScenarioEngine(1), ScenarioEngine(2)
    e2.inject("tp2_f3", "short_circuit", duration_s=5)
    r2 = e2.tick()
    assert r2[REG_FAULT] == 1 and r2[REG_FAULT_OBJ] == 3
    r1 = e1.tick()                        # ТП-1 в норме
    assert r1[REG_FAULT] == 0
    assert r1[REG_BREAKER] == 1
