# -*- coding: utf-8 -*-
"""
Тесты модели ПС 110/10 (MVP_SCADA_Denis): дерево, поиск, родители, API-сериализация.
"""
from titan.simulator.factory import (
    PS, UNITS, find_object, get_parent, obj_display_name, tree_to_api,
)


def test_tree_structure():
    """ПС -> 2 плеча, 2 секции, секционный QF; в каждой секции 4 фидера."""
    kinds = [c.id for c in PS.children]
    assert "arm1" in kinds and "arm2" in kinds
    assert "sec1" in kinds and "sec2" in kinds
    assert "sec_qf" in kinds                    # секционный выключатель
    # В плече: ввод 110, Q-110, транс, ввод 10 кВ
    arm1 = find_object("arm1")
    assert [c.kind for c in arm1.children] == [
        "incomer110", "incomer110", "transformer", "incomer10"
    ]
    assert len(find_object("sec1").children) == 4
    assert len(find_object("sec2").children) == 4


def test_find_object():
    """Поиск по id: фидер секции, секционный, неизвестный id."""
    f2 = find_object("sec1_f2")
    assert f2 is not None and f2.name == "Ф-2" and f2.kind == "feeder"
    assert find_object("sec_qf").kind == "sectional"
    assert find_object("no_such") is None


def test_get_parent():
    """Родитель фидера — его секция; у секционного — ПС; у ПС — None."""
    assert get_parent("sec1_f2").id == "sec1"
    assert get_parent("sec2_f5" if False else "arm2_tr").id == "arm2"
    assert get_parent("sec_qf").id == "ps"
    assert get_parent("ps") is None


def test_obj_display_name():
    """Имя события: «Секция I, Ф-2» (привязка алармов к уровням схемы)."""
    assert obj_display_name("sec1_f2") == "Секция I, 10 кВ, Ф-2"
    assert obj_display_name("arm1_incomer") == "Плечо А, Ввод 10 кВ №1"
    assert obj_display_name("sec_qf") == "Секционный QF"


def test_units_mapping():
    """Два Modbus-устройства; unit 1 — плечо А (10 объектов), unit 2 — плечо Б."""
    assert set(UNITS.keys()) == {1, 2}
    # arm1(группа+4 объекта) + sec1(группа+4 фидера) = 10
    assert len(UNITS[1]) == 10
    assert len(UNITS[2]) == 11                    # arm2(5) + sec2(5) + sec_qf


def test_tree_to_api():
    """Сериализация для фронтенда: ps -> дети, у секций есть подписи фидеров."""
    tree = tree_to_api()
    assert tree["id"] == "ps"
    ids = [c["id"] for c in tree["children"]]
    assert ids == ["arm1", "arm2", "sec1", "sec2", "sec_qf"]
    sec1 = tree["children"][2]
    assert sec1["feeders"][0] == "Насосная"
