# -*- coding: utf-8 -*-
"""
Модель завода (специф §16 «Многоуровневая мнемосхема», решение 23.11).

Три уровня навигации:
  Уровень 0: Завод  -> ТП-1, ТП-2 (обзор, агрегированные статусы)
  Уровень 1: ТП-x   -> ввод, трансформатор, секция, фидеры Ф-1..Ф-4
  Уровень 2: Объект -> детальная схема фидера (кабель -> QF -> ТТ -> счётчик)

Объекты и регистры: две ТП = два независимых Modbus-устройства
(unit_id 1 и 2), карта регистров одинаковая (специф §6.2).
Иерархия задаётся единственным источником правды — FACTORY_TREE.
"""
from dataclasses import dataclass, field


@dataclass
class Obj:
    """Узел дерева завода: контейнер или конечный объект (аппарат/линия)."""
    id: str                       # уникальный ключ ('tp1', 'tp1_f2', ...)
    name: str                     # человекочитаемое имя ('ТП-1', 'Ф-2')
    unit_id: int | None = None    # Modbus unit_id (для конечных объектов ТП)
    kind: str = "group"           # group | feeder | transformer | bus | incomer
    children: list["Obj"] = field(default_factory=list)

    def walk(self):
        """Итерация по всему поддереву (включая себя)."""
        yield self
        for ch in self.children:
            yield from ch.walk()


def _make_tp(tp_id: str, name: str, unit_id: int, feeders: list[str]) -> Obj:
    """Шаблон ТП: ввод -> трансформатор -> секция -> 4 фидера."""
    tp = Obj(tp_id, name, unit_id=unit_id, kind="group")
    tp.children = [
        Obj(f"{tp_id}_incomer", "Ввод QF1", unit_id=unit_id, kind="incomer"),
        Obj(f"{tp_id}_tr", f"Т-{tp_id[-1]}", unit_id=unit_id, kind="transformer"),
        Obj(f"{tp_id}_bus", "Секция 0,4 кВ", unit_id=unit_id, kind="bus"),
    ]
    for i, fname in enumerate(feeders, start=1):
        tp.children.append(Obj(f"{tp_id}_f{i}", f"Ф-{i}", unit_id=unit_id, kind="feeder"))
    # описание фидеров хранится рядом (для подписей на уровне 2)
    tp.meta_feeders = feeders  # type: ignore[attr-defined]
    return tp


# ---------- Единственный источник правды по структуре завода ----------
FACTORY = Obj("factory", "Завод", kind="group")
FACTORY.children = [
    _make_tp("tp1", "ТП-1", unit_id=1,
             feeders=["Насосная", "Вентиляция", "Освещение", "Резерв"]),
    _make_tp("tp2", "ТП-2", unit_id=2,
             feeders=["Станочный цех", "Компрессорная", "Освещение", "Резерв"]),
]

# Индекс id -> объект (для быстрого поиска по строке из API/ПКМ)
OBJECT_INDEX: dict[str, Obj] = {o.id: o for o in FACTORY.walk()}

# unit_id -> список конечных объектов ТП (все элементы шарят регистры своей ТП)
UNITS: dict[int, list[Obj]] = {}
for o in FACTORY.walk():
    if o.unit_id is not None:
        UNITS.setdefault(o.unit_id, []).append(o)


def find_object(obj_id: str) -> Obj | None:
    """Найти объект по id ('tp1_f2'). None — если id неизвестен."""
    return OBJECT_INDEX.get(obj_id)


def get_parent(obj_id: str) -> Obj | None:
    """Родитель объекта (для хлебных крошек и навигации вверх)."""
    obj = find_object(obj_id)
    if obj is None:
        return None
    for candidate in FACTORY.walk():
        if obj in candidate.children:
            return candidate
    return None


def tree_to_api() -> dict:
    """Дерево для фронтенда (GET /api/objects/tree): id, name, kind, children."""
    def to_dict(o: Obj) -> dict:
        d = {"id": o.id, "name": o.name, "kind": o.kind, "children": [to_dict(c) for c in o.children]}
        feeders = getattr(o, "meta_feeders", None)
        if feeders:
            d["feeders"] = feeders
        return d
    return to_dict(FACTORY)
