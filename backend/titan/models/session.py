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
_connect_args = {}
_engine_kwargs = {"echo": False, "pool_pre_ping": True}
if settings.database_url.startswith("sqlite"):
    _connect_args = {"timeout": 30}       # сек ожидания блокировки
engine = create_async_engine(
    settings.database_url, connect_args=_connect_args, **_engine_kwargs
)

# Фабрика сессий: каждая операция с БД — короткая сессия
sessionmaker = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """Создать все таблицы, если их ещё нет + включить WAL для SQLite.

    WAL (Write-Ahead Logging) позволяет читать и писать одновременно —
    обязательный режим, когда коллектор пишет телеметрию, а API работает
    с журналами в той же базе.
    В MVP используем create_all (просто и достаточно);
    миграции Alembic добавим, когда схема стабилизируется.
    """
    async with engine.begin() as conn:
        if settings.database_url.startswith("sqlite"):
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            await conn.exec_driver_sql("PRAGMA busy_timeout=10000")
        await conn.run_sync(Base.metadata.create_all)
