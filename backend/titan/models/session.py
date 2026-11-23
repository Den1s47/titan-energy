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

# Async-движок: URL берётся из настроек (SQLite сейчас, Postgres потом).
# connect_args: busy_timeout — сколько ждать снятия блокировки SQLite,
# чтобы параллельные записи (коллектор + журнал действий) не падали с 'database is locked'.
import os as _os

_connect_args = {}
if settings.database_url.startswith("sqlite"):
    # timeout — ожидание снятия блокировки (сек); check_same_thread не нужен для aiosqlite.
    # 30 с: коллектор пишет каждую секунду, журнал оператора — в пиках одновременно.
    _connect_args = {"timeout": 30}
engine = create_async_engine(
    settings.database_url,
    echo=False,
    connect_args=_connect_args,
    pool_pre_ping=True,   # проверять соединение перед использованием (устойчивость)
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
