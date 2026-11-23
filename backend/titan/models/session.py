# -*- coding: utf-8 -*-
"""
Подключение к БД: async-движок SQLAlchemy + создание таблиц.

Использование в lifespan:
    await init_db()                      # создать таблицы при старте
    async with sessionmaker() as s: ...  # рабочая сессия
"""
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from titan.config import settings
from titan.models.db import Base

# Async-движок: URL берётся из настроек (PostgreSQL сейчас, SQLite для тестов).
# connect_args: для SQLite — busy_timeout (ожидание блокировки);
# для PostgreSQL — пул настраивается по умолчанию.
#
# ЛЕНИВАЯ инициализация: engine/sessionmaker создаются при первом init_db(),
# а не на уровне модуля. Это критично для тестов: fixture разных модулей
# переустанавливает settings.database_url ДО первого init_db — все тесты
# одного прогона шарят один engine (и одну БД), без «database is locked».
_connect_args = {}
_engine_kwargs = {"echo": False, "pool_pre_ping": True}
if settings.database_url.startswith("sqlite"):
    _connect_args = {"timeout": 30}       # сек ожидания блокировки

engine = None          # создаётся лениво (см. _ensure_engine)
sessionmaker = None    # фабрика сессий, создаётся вместе с engine


def _ensure_engine():
    """Создать engine/sessionmaker один раз по текущим settings.

    Вызывается из init_db() И при импорте этого модуля (строка ниже):
    модули делают `from titan.models.session import sessionmaker` на уровне
    файла — без вызова они получили бы None до первого init_db().
    """
    global engine, sessionmaker
    if engine is None:
        # Для SQLite — NullPool: pytest-asyncio создаёт НОВЫЙ event loop на
        # каждый тест, а соединения aiosqlite привязаны к loop'у. Пул бы
        # отдавал «чужое» соединение → зависание await навсегда. NullPool
        # открывает свежее соединение на каждую сессию (для тестов — идеально).
        from sqlalchemy.pool import NullPool
        kwargs = dict(_engine_kwargs)
        if settings.database_url.startswith("sqlite"):
            kwargs["poolclass"] = NullPool
        engine = create_async_engine(
            settings.database_url, connect_args=_connect_args, **kwargs
        )
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)


# Создаём движок сразу при импорте: settings к этому моменту уже финальны
# (conftest и тестовые файлы выставляют TITAN_TEST_DB до импорта titan.*)
_ensure_engine()


async def init_db() -> None:
    """Создать все таблицы, если их ещё нет + включить WAL для SQLite.

    WAL (Write-Ahead Logging) позволяет читать и писать одновременно —
    обязательный режим, когда коллектор пишет телеметрию, а API работает
    с журналами в той же базе.
    """
    _ensure_engine()   # ленивое создание движка по текущим settings
    async with engine.begin() as conn:
        if settings.database_url.startswith("sqlite"):
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            await conn.exec_driver_sql("PRAGMA busy_timeout=10000")
        await conn.run_sync(Base.metadata.create_all)
