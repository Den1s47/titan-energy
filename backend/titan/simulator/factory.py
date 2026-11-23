# -*- coding: utf-8 -*-
"""
Модель учебной ПС 110/10 кВ (MVP_SCADA_Denis.docx, решение 23.11).

Архитектура (по однолинейной схеме из документа):
  Ввод 110 кВ №1 -> Q110-1 -> T1 -> Q10-1 (ввод 10 кВ) -> Секция I  -> Ф1..Ф4
  Ввод 110 кВ №2 -> Q110-2 -> T2 -> Q10-2 (ввод 10 кВ) -> Секция II -> Ф5..Ф8
                                    Секционный QF (норм. ОТКЛ) между секциями

Ключевой сценарий: авария/потеря ввода №1 -> переброс питания Секции I на
Секцию II через секционный выключатель (ТУ вручную, с блокировками).

Modbus: unit_id 1 = «плечо А» (ввод 1, T1, секция I, Ф1-4),
        unit_id 2 = «плечо Б» (ввод 2, T2, секция II, Ф5-8).
Секционный выключатель моделируется в регистрах unit 2 (регистр 12).

Иерархия уровней мнемосхемы:
  Уровень 0: ПС 110/10 (обзор: два плеча, статусы, аварии)
  Уровень 1: Схема ПС (полная однолинейная, как на рисунке из docx)
  Уровень 2: Объект (фидер/ввод/трансформатор — детальные показатели)
"""
from dataclasses import dataclass, field


@dataclass
class Obj:
    """Узел дерева подстанции: контейнер или конечный объект."""
    id: str                        # уникальный ключ ('ps', 'sec1_f2', ...)
    name: str                      # человекочитаемое имя ('Ф-1', 'Ввод 110 кВ №1')
    unit_id: int | None = None     # Modbus unit (1 или 2)
    kind: str = "group"            # group | feeder | transformer | bus | incomer110 | incomer10 | sectional
    voltage: str = ""              # '110 кВ' | '10 кВ' | '' (для подписей)
    children: list["Obj"] = field(default_factory=list)

    def walk(self):
        """Итерация по всему поддереву (включая себя)."""
        yield self
        for ch in self.children:
            yield from ch.walk()


def _make_arm(arm_id: str, label: str, unit_id: int,
              n110: str, t_name: str) -> Obj:
    """Плечо подстанции: ввод 110 -> Q110 -> транс -> ввод 10 кВ."""
    arm = Obj(arm_id, label, unit_id=unit_id, kind="group")
    arm.children = [
        Obj(f"{arm_id}_in110", f"Ввод 110 кВ {n110}", unit_id=unit_id,
            kind="incomer110", voltage="110 кВ"),
        Obj(f"{arm_id}_q110", f"Q-110 {n110}", unit_id=unit_id,
            kind="incomer110", voltage="110 кВ"),
        Obj(f"{arm_id}_tr", t_name, unit_id=unit_id,
            kind="transformer", voltage="110/10 кВ"),
        Obj(f"{arm_id}_incomer", f"Ввод 10 кВ {n110}", unit_id=unit_id,
            kind="incomer10", voltage="10 кВ"),
    ]
    return arm


def _make_section(sec_id: str, name: str, unit_id: int,
                  feeders: list[str]) -> Obj:
    """Секция шин 10 кВ с отходящими фидерами."""
    sec = Obj(sec_id, name, unit_id=unit_id, kind="bus", voltage="10 кВ")
    for i, fname in enumerate(feeders, start=1):
        sec.children.append(Obj(
            f"{sec_id}_f{i}", f"Ф-{i}", unit_id=unit_id,
            kind="feeder", voltage="10 кВ",
        ))
    sec.meta_feeders = feeders  # type: ignore[attr-defined]
    return sec


# ---------- Единственный источник правды по структуре ПС ----------
PS = Obj("ps", "ПС 110/10 кВ", kind="group")

# Плечо А (unit 1): ввод 110 №1, Q-110 №1, T1, ввод 10 кВ №1
ARM_A = _make_arm("arm1", "Плечо А", 1, "№1", "T1")
SEC1 = _make_section("sec1", "Секция I, 10 кВ", 1,
                     ["Насосная", "Вентиляция", "Освещение", "Резерв"])

# Плечо Б (unit 2): ввод 110 №2, Q-110 №2, T2, ввод 10 кВ №2
ARM_B = _make_arm("arm2", "Плечо Б", 2, "№2", "T2")
SEC2 = _make_section("sec2", "Секция II, 10 кВ", 2,
                     ["Станочный цех", "Компрессорная", "Освещение", "Резерв"])

# Секционный выключатель (моделируется в unit 2, регистр 12)
SECTIONAL = Obj("sec_qf", "Секционный QF", unit_id=2,
                kind="sectional", voltage="10 кВ")

# Финальное дерево ПС
PS.children = [ARM_A, ARM_B, SEC1, SEC2, SECTIONAL]

# Индекс id -> объект
OBJECT_INDEX: dict[str, Obj] = {o.id: o for o in PS.walk()}

# unit_id -> список объектов плеча (для распределения значений по фидерам)
UNITS: dict[int, list[Obj]] = {}
for o in PS.walk():
    if o.unit_id is not None:
        UNITS.setdefault(o.unit_id, []).append(o)


def find_object(obj_id: str) -> Obj | None:
    """Найти объект по id ('sec1_f2'). None — если id неизвестен."""
    return OBJECT_INDEX.get(obj_id)


def get_parent(obj_id: str) -> Obj | None:
    """Родитель объекта (для хлебных крошек и имён событий)."""
    obj = find_object(obj_id)
    if obj is None:
        return None
    for candidate in PS.walk():
        if obj in candidate.children:
            return candidate
    return None


def obj_display_name(obj_id: str) -> str:
    """Человекочитаемое имя с родителем: «Секция I, Ф-2»."""
    obj = find_object(obj_id)
    if obj is None:
        return obj_id
    parent = get_parent(obj.id)
    if parent and parent.id != "ps":
        return f"{parent.name}, {obj.name}"
    return obj.name


def tree_to_api() -> dict:
    """Дерево для фронтенда (GET /api/objects/tree)."""
    def to_dict(o: Obj) -> dict:
        d = {"id": o.id, "name": o.name, "kind": o.kind,
             "voltage": o.voltage,
             "children": [to_dict(c) for c in o.children]}
        feeders = getattr(o, "meta_feeders", None)
        if feeders:
            d["feeders"] = feeders
        return d
    return to_dict(PS)
