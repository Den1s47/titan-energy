# -*- coding: utf-8 -*-
"""
Тесты коллектора (multi-object): валидация, quality, comm_loss per-ТП.

Валидация — чистая функция, без сети. Сетевой путь — тесты API.
"""
from titan.collector.service import Collector, Reading, validate


def test_validate_good():
    """Значения в техно-границах -> good."""
    assert validate("U", 230.0) == "good"
    assert validate("THDi", 7.0) == "good"


def test_validate_suspect_out_of_tech_limits():
    """Выход за техно-границы -> suspect (оранжевый ⚠️)."""
    assert validate("U", 260.0) == "suspect"
    assert validate("THDi", 20.0) == "suspect"
    assert validate("PF", 0.3) == "suspect"


def test_validate_deenergized_zero_is_good():
    """Обесточено (BREAKER=0): нулевые аналоги — good (штатный режим)."""
    assert validate("U", 0.0, breaker_on=False) == "good"
    assert validate("I", 0.0, breaker_on=False) == "good"
    # то же значение при включённом вводе — suspect
    assert validate("U", 0.0, breaker_on=True) == "suspect"


async def test_collector_poll_comm_loss():
    """Оба сервера недоступны -> ВСЕ точки обеих ТП suspect."""
    c = Collector()                            # к серверу не подключены
    readings = await c.poll_once()
    # multi-object ключи обеих ТП
    assert "tp1_incomer:U" in readings
    assert "tp2_incomer:U" in readings
    assert all(r.quality == "suspect" for r in readings.values())


def test_live_merge():
    """Свойство live объединяет кэши обеих ТП без конфликтов ключей."""
    from unittest.mock import patch as mock_patch
    # Collector() создаёт Modbus-клиента — требует event loop; в тесте с loop
    with mock_patch("titan.collector.service.AsyncModbusTcpClient"):
        c = Collector()
    c.pollers[1].live = {"tp1_incomer:U": Reading("U", 230.0, "good")}
    c.pollers[2].live = {"tp2_incomer:U": Reading("U", 228.0, "good")}
    merged = c.live
    assert len(merged) == 2
    assert merged["tp1_incomer:U"].value == 230.0
    assert merged["tp2_incomer:U"].value == 228.0
