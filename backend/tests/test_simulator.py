# -*- coding: utf-8 -*-
"""
Тесты симулятора (модель ПС 110/10): движок плеча, точечные инъекции,
секционный выключатель, потеря ввода 110 кВ.
"""
from titan.simulator.engine import (
    ScenarioEngine, REG_BREAKER, REG_FAULT, REG_FAULT_OBJ, REG_I, REG_U,
    REG_SECTIONAL,
)


def test_base_profile_normal():
    """Норма: U в допуске, ввод включён, аварий нет, СВ отключён."""
    eng = ScenarioEngine(1)
    regs = eng.tick()
    assert 198 <= regs[REG_U] <= 242
    assert regs[REG_BREAKER] == 1
    assert regs[REG_FAULT] == 0
    assert regs[REG_SECTIONAL] == 0


def test_short_circuit_on_feeder():
    """КЗ на Ф-2 секции I: код 1, ток ×6, МТЗ отключает ввод, владелец = фидер 2."""
    eng = ScenarioEngine(1)
    eng.inject("sec1_f2", "short_circuit", duration_s=5)
    regs = eng.tick()
    assert regs[REG_FAULT] == 1
    assert regs[REG_I] > 100
    assert regs[REG_BREAKER] == 0
    assert regs[REG_FAULT_OBJ] == 2


def test_overvoltage_on_bus():
    """Перенапряжение на секции: код 3, U > 400 В."""
    eng = ScenarioEngine(1)
    eng.inject("sec1", "overvoltage", duration_s=5)
    regs = eng.tick()
    assert regs[REG_FAULT] == 3
    assert regs[REG_U] > 400


def test_line_loss_110():
    """Потеря ввода 110 кВ: код 5, ввод отключается, плечо обесточено."""
    eng = ScenarioEngine(2)
    eng.inject("arm2_in110", "line_loss_110", duration_s=30)
    regs = eng.tick()
    assert regs[REG_FAULT] == 5
    assert regs[REG_BREAKER] == 0
    assert regs[REG_U] == 0


def test_injection_expires():
    """Инъекция истекла — значения вернулись к норме."""
    eng = ScenarioEngine(1)
    eng.inject("sec1", "overvoltage", duration_s=0)
    eng.injection["until"] = 0.0
    regs = eng.tick()
    assert regs[REG_U] < 400
    assert regs[REG_FAULT] == 0


def test_arms_independent():
    """Плечи независимы: авария на плече Б не влияет на плечо А."""
    e1, e2 = ScenarioEngine(1), ScenarioEngine(2)
    e2.inject("sec2_f3", "short_circuit", duration_s=5)
    r2 = e2.tick()
    assert r2[REG_FAULT] == 1 and r2[REG_FAULT_OBJ] == 3
    r1 = e1.tick()
    assert r1[REG_FAULT] == 0 and r1[REG_BREAKER] == 1


def test_sectional_command():
    """ТУ на секционный QF: команда 1=вкл исполняется с задержкой актуатора."""
    eng = ScenarioEngine(2)
    eng.command_sectional(1)
    eng.cmd_deadline = 0.0          # время уже истекло
    regs = eng.tick()
    assert regs[REG_SECTIONAL] == 1
    # Обратная команда
    eng.command_sectional(2)
    eng.cmd_deadline = 0.0
    regs = eng.tick()
    assert regs[REG_SECTIONAL] == 0
