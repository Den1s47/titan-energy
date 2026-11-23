# -*- coding: utf-8 -*-
"""
Тесты модели завода: дерево, поиск, родители, API-сериализация.
"""
from titan.simulator.factory import FACTORY, UNITS, find_object, get_parent, tree_to_api


def test_tree_structure():
    """Завод -> 2 ТП, в каждой ТП ввод + транс + секция + 4 фидера."""
    assert len(FACTORY.children) == 2
    tp1, tp2 = FACTORY.children
    assert tp1.name == "ТП-1" and tp1.unit_id == 1
    assert tp2.name == "ТП-2" and tp2.unit_id == 2
    # ввод, транс, секция, 4 фидера = 7 детей
    assert len(tp1.children) == 7
    kinds = [c.kind for c in tp1.children]
    assert kinds.count("feeder") == 4
    assert "incomer" in kinds and "transformer" in kinds and "bus" in kinds


def test_find_object():
    """Поиск по id: конечный объект, группа, неизвестный id."""
    f2 = find_object("tp1_f2")
    assert f2 is not None and f2.name == "Ф-2" and f2.kind == "feeder"
    assert find_object("tp2").unit_id == 2
    assert find_object("no_such") is None


def test_get_parent():
    """Родитель фидера — его ТП; родитель ТП — Завод; у Завода нет родителя."""
    assert get_parent("tp1_f2").id == "tp1"
    assert get_parent("tp2").id == "factory"
    assert get_parent("factory") is None


def test_units_mapping():
    """Каждая ТП — своё Modbus-устройство: 8 объектов на unit."""
    assert set(UNITS.keys()) == {1, 2}
    assert len(UNITS[1]) == 8       # incomer + tr + bus + 4 feeders
    assert len(UNITS[2]) == 8


def test_tree_to_api():
    """Сериализация для фронтенда: factory -> children -> leaves."""
    tree = tree_to_api()
    assert tree["id"] == "factory"
    assert len(tree["children"]) == 2
    tp1 = tree["children"][0]
    assert tp1["id"] == "tp1"
    assert len(tp1["children"]) == 7
    # у ТП есть подписи фидеров для уровня 2
    assert tp1["feeders"][0] == "Насосная"
