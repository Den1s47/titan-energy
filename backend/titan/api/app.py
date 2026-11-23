# -*- coding: utf-8 -*-
"""
web-api: FastAPI-приложение — REST-эндпоинты + раздача UI.

Embedded-режим (специф §2.3): симулятор и коллектор стартуют ВМЕСТЕ с API
через lifespan — один запуск, один лог. Позже их можно разнести на
отдельные процессы конфигурацией, без изменения кода.

Эндпоинты (специф §10, срез M2 — RBAC):
- POST /api/auth/login       — вход -> JWT [все]
- GET  /api/me               — текущий пользователь [токен]
- GET  /api/health           — жив ли сервер [все]
- GET  /api/live             — снапшот телеметрии [токен]
- GET  /api/history          — история измерений [токен]
- GET  /api/events           — журналы событий [токен]
- POST /api/events/{id}/ack  — квитирование [dispatcher+]
- GET  /api/actions          — журнал действий оператора [admin]
- CRUD /api/admin/users      — управление учётками [admin]
- POST /api/sim/fault        — пульт симулятора [admin]
"""
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from titan.api.auth import log_action, require_role
from titan.api.users import router as users_router
from titan.api.commands import router as commands_router, set_services
from titan.api.waveforms import router as waveforms_router, generate_fault_waveform
from titan.api.analytics import router as analytics_router
from titan.collector.service import Collector
from titan.config import settings
from titan.models.db import Event, Measurement, OperatorAction, User
from titan.models.session import engine, init_db, sessionmaker
from titan.simulator.engine import SimulatorService
from titan.timeutil import fmt_iso_ms, utcnow

# Единый формат логов для всех компонентов
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)  # логгер этого модуля (lifespan, ensure_admin)

# Симулятор создаётся сразу (он не требует event loop при конструировании)
simulator = SimulatorService()

# Коллектор создаётся в lifespan: его клиент pymodbus требует
# работающий event loop, а на уровне модуля его ещё нет.
collector: Collector | None = None


async def ensure_admin_user() -> None:
    """Создать учётку администратора по умолчанию при первом старте.

    Если в БД ещё нет ни одного admin — создаём admin/admin (MVP!).
    В проде пароль задаётся через переменную окружения TITAN_ADMIN_PASSWORD.
    Реальные учётки заказчик меняет через Admin UI (специф §9.3).
    """
    import os
    async with sessionmaker() as session:
        result = await session.execute(select(User).where(User.role == "admin"))
        if result.scalar_one_or_none() is not None:
            return  # админ уже есть — ничего не делаем
        from titan.api.auth import hash_password
        admin = User(
            username="admin",
            password_hash=hash_password(os.environ.get("TITAN_ADMIN_PASSWORD", "admin")),
            role="admin",
        )
        session.add(admin)
        await session.commit()
        log.warning("Создан администратор по умолчанию: admin (смени пароль через Admin UI!)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Жизненный цикл приложения: стартуем компоненты до запросов, гасим после.

    Порядок важен: сначала БД и симулятор («оборудование»), потом коллектор —
    иначе первый опрос упадёт в никуда.
    """
    global collector  # noqa: PLW0603 — переприсвоение глобальной ссылки на коллектор

    await init_db()            # создать таблицы, если их нет
    await ensure_admin_user()  # первый admin, если учёток ещё нет
    if settings.sim_enabled:
        simulator.start()
    if settings.collector_enabled:
        collector = Collector()
        await collector.start()
        # ТУ-модулю нужны ссылки на коллектор и симулятор (write-канал, live-кэш)
        set_services(collector, simulator)

    yield  # <- здесь приложение обслуживает запросы

    # Корректная остановка при выключении сервера
    if collector:
        await collector.stop()
    await engine.dispose()  # закрыть пул соединений с БД
    await simulator.stop()


# FastAPI-приложение с подключённым жизненным циклом
app = FastAPI(title=settings.app_name, version="0.4.0", lifespan=lifespan)
app.include_router(users_router)     # /auth, /me, /admin/users
app.include_router(commands_router)  # /commands/select, /commands/operate (ТУ)
app.include_router(waveforms_router)  # /waveforms/{id} (осциллограммы)
app.include_router(analytics_router)  # /analytics/*, /export/* (M5)


@app.get("/api/health")
async def health() -> dict:
    """Health-check: жив ли сервер (без авторизации — для мониторинга)."""
    return {"status": "ok", "app": settings.app_name}


@app.get("/api/live")
async def live(user: dict = Depends(require_role("viewer"))) -> dict:
    """Последний снапшот телеметрии (специф §10: GET /live).

    Возвращает {code: {value, quality}} — фронтенд рисует это на мнемосхеме:
    quality=good -> обычный цвет, suspect/bad -> оранжевый с ⚠️.
    """
    if collector is None:
        return {}  # коллектор выключен конфигом — пустой снапшот
    return {
        code: {"value": r.value, "quality": r.quality}
        for code, r in collector.live.items()
    }


@app.get("/api/history")
async def history(point_code: str, minutes: float = 15.0,
                  user: dict = Depends(require_role("viewer"))) -> dict:
    """История измерений одной точки за последние N минут (специф §10: GET /history).

    Это база для графиков MES-уровня (раздел «Выбор», усреднения добавятся позже).
    Args:
        point_code: имя точки ('U', 'I', ...)
        minutes: глубина периода в минутах (по умолчанию 15)
    """
    since = utcnow() - timedelta(minutes=minutes)
    async with sessionmaker() as session:
        stmt = (
            select(Measurement)
            .where(Measurement.point_code == point_code, Measurement.time >= since)
            .order_by(Measurement.time)
            .limit(5000)  # защита от слишком больших выборок
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()
    return {
        "point_code": point_code,
        "count": len(rows),
        "values": [
            {"time": fmt_iso_ms(r.time), "value": r.value, "quality": r.quality}
            for r in rows
        ],
    }


@app.get("/api/events")
async def events(cls: str | None = None, acked: bool | None = None, limit: int = 200,
                 user: dict = Depends(require_role("viewer"))) -> dict:
    """Журнал событий (специф §10: GET /events).

    Фильтры соответствуют разделу «Журналы» ТЗ:
      cls=alarm    -> журнал аварийных событий
      cls=abnormal -> журнал ненормальных режимов
      cls=operator -> журнал действий оператора
      (без фильтра) -> журнал всех событий
    """
    async with sessionmaker() as session:
        stmt = select(Event).order_by(Event.time.desc()).limit(limit)
        if cls:
            stmt = stmt.where(Event.cls == cls)
        if acked is not None:
            stmt = stmt.where(Event.acked == acked)
        result = await session.execute(stmt)
        rows = result.scalars().all()
    return {"count": len(rows), "events": [_event_to_dict(e) for e in rows]}


@app.post("/api/events/{event_id}/ack")
async def ack_event(event_id: int,
                    user: dict = Depends(require_role("dispatcher"))) -> dict:
    """Квитирование события (ТЗ 3.3.3): только dispatcher и выше; viewer -> 403.

    После ack мигание аларма прекращается (цвет остаётся). Действие пишется
    в журнал оператора с учёткой (ТЗ 3.3.2).
    """
    async with sessionmaker() as session:
        result = await session.execute(select(Event).where(Event.id == event_id))
        event = result.scalar_one_or_none()
        if event is None:
            raise HTTPException(status_code=404, detail="Событие не найдено")
        if not event.acked:  # повторное ack не меняет время/автора
            event.acked = True
            event.acked_by = user["username"]  # учётка квитировавшего (ТЗ 3.3.2)
            event.acked_at = utcnow()
            await session.commit()
    await log_action(sessionmaker, user["username"], "ack", "success",
                     object_name=event.object_name, details=f"событие #{event_id}")
    return {"ok": True, "id": event_id}


@app.get("/api/actions")
async def actions(limit: int = 200,
                  admin: dict = Depends(require_role("admin"))) -> dict:
    """Журнал действий оператора (специф §10: GET /actions) — только admin.

    Включает входы в систему, квитирования, изменения учёток, ТУ (потом),
    в том числе НЕУДАЧНЫЕ попытки (ТЗ 3.3.2, примечание).
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(OperatorAction).order_by(OperatorAction.time.desc()).limit(limit)
        )
        rows = result.scalars().all()
    return {"count": len(rows), "actions": [
        {
            "time": fmt_iso_ms(a.time),
            "username": a.username,
            "action": a.action,
            "object": a.object_name,
            "result": a.result,
            "details": a.details,
        } for a in rows
    ]}


@app.post("/api/sim/fault")
async def sim_fault(object_id: str, scenario: str, duration_s: float = 10.0,
                    admin: dict = Depends(require_role("admin"))) -> dict:
    """Пульт симулятора: ТОЧЕЧНАЯ инъекция на объект (специф §16) — admin.

    Args:
        object_id: id объекта дерева завода ('tp1', 'tp1_f2', 'tp2_bus', ...)
        scenario: short_circuit | overload | overvoltage | cable_break
        duration_s: длительность сценария в секундах
    """
    allowed = {"short_circuit", "overload", "overvoltage", "cable_break"}
    if scenario not in allowed:
        return {"ok": False, "error": f"scenario must be one of {sorted(allowed)}"}

    from titan.simulator.factory import find_object
    obj = find_object(object_id)
    if obj is None:
        return {"ok": False, "error": f"unknown object: {object_id}"}
    if obj.unit_id is None:
        return {"ok": False, "error": "объект без привязки к ТП (только группы)"}

    engine = simulator.get_engine(obj.unit_id)
    # «Обрыв кабеля» на вводе ТП = потеря связи всей ТП (comm_loss)
    if scenario == "cable_break" and obj.kind == "incomer":
        engine.inject(object_id, "cable_break", duration_s)
        msg = f"обрыв кабеля на вводе {obj.name}: потеря связи ТП-{obj.unit_id}"
    else:
        engine.inject(object_id, scenario, duration_s)
        msg = f"{obj.name}: {scenario} на {duration_s} с"

    await log_action(sessionmaker, admin["username"], "sim_fault", "success",
                     object_name=obj.name, details=msg)
    return {"ok": True, "object": obj.name, "object_id": object_id,
            "scenario": scenario, "duration_s": duration_s}


@app.get("/api/objects/tree")
async def objects_tree(user: dict = Depends(require_role("viewer"))) -> dict:
    """Дерево объектов завода для многоуровневой мнемосхемы (специф §16).

    Уровни: factory -> tp1/tp2 -> incomer/tr/bus/feeders.
    Фронтенд строит уровни 0/1/2 из этого дерева.
    """
    from titan.simulator.factory import tree_to_api
    return tree_to_api()


@app.get("/api/objects/alarm-stats")
async def objects_alarm_stats(minutes: int = 60,
                              user: dict = Depends(require_role("viewer"))) -> dict:
    """Статистика аварий по объектам для тепловой карты завода (специф §16).

    Возвращает число алармов (cls='alarm') на каждый объект за последние
    N минут. Поле object_name хранит «ТП-1, Ф-2» — сопоставляется с деревом.
    """
    from datetime import timedelta
    since = utcnow() - timedelta(minutes=minutes)
    async with sessionmaker() as session:
        result = await session.execute(
            select(Event.object_name, func.count(Event.id))
            .where(Event.cls == "alarm", Event.time >= since)
            .group_by(Event.object_name)
        )
        rows = result.all()
    return {"minutes": minutes, "objects": [
        {"object": name, "alarms": cnt} for name, cnt in rows
    ]}


def _event_to_dict(e: Event) -> dict:
    """Сериализация события в JSON-совместимый словарь."""
    return {
        "id": e.id,
        "time": fmt_iso_ms(e.time),
        "object": e.object_name,
        "cls": e.cls,
        "priority": e.priority,
        "code": e.code,
        "message": e.message,
        "value": e.value,
        "setpoint": e.setpoint,
        "acked": e.acked,
        "acked_by": e.acked_by,
        "acked_at": fmt_iso_ms(e.acked_at) if e.acked_at else None,
    }


# ---------- Раздача SPA (собранный Vite-фронтенд из frontend/dist) ----------
# ВАЖНО: объявляется ПОСЛЕ всех /api/* маршрутов, иначе catch-all перехватит их.
# Статика отдаётся самим FastAPI (специф §2.1): один сервис на бэкенд+UI.
FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"

if FRONTEND_DIST.is_dir():
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        """SPA-catch-all: файлы ассетов — с диска, остальные пути — index.html
        (react-router сам разберёт путь на клиенте)."""
        candidate = FRONTEND_DIST / full_path
        # защита от выхода за пределы dist (path traversal)
        if full_path and candidate.is_file() and FRONTEND_DIST.resolve() in candidate.resolve().parents:
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    log.info("frontend/dist не найден — SPA не раздаётся (соберите: npm run build)")
