# -*- coding: utf-8 -*-
"""
Тесты коллектора: валидация и quality-флаги (специф §5.3).

Валидация — чистая функция, тестируем без сети.
Сетевой путь (poll_once с реальным Modbus) покрывают тесты API.
"""
from titan.collector.service import Collector, validate


def test_validate_good():
    """Значения в техно-границах -> good (обычная индикация)."""
    assert validate("U", 230.0) == "good"      # в пределах 198–242 В
    assert validate("THDi", 7.0) == "good"     # в пределах 0–15 %


def test_validate_suspect_out_of_tech_limits():
    """Выход за техно-границы -> suspect (оранжевый + ⚠️ на мнемосхеме)."""
    assert validate("U", 260.0) == "suspect"   # выше 242 В
    assert validate("THDi", 20.0) == "suspect" # выше 15 %
    assert validate("PF", 0.3) == "suspect"    # ниже 0.5


def test_validate_unknown_code():
    """Для точки без границ (дискреты) валидация всегда good."""
    assert validate("BREAKER", 1.0) == "good"


async def test_collector_poll_comm_loss():
    """Потеря связи (сервер не запущен) -> все точки suspect (ТЗ: неопределённое состояние)."""
    c = Collector()                            # клиент ещё не подключён к серверу
    readings = await c.poll_once()
    assert "U" in readings                     # структура ответа сохраняется
    assert readings["U"].quality == "suspect"  # но качество: потеря связи
