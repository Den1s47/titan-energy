# -*- coding: utf-8 -*-
"""
Модели БД (SQLAlchemy 2.0, async).

Схема данных MVP (специф §5.2):
- measurements — поток телеметрии (аналог гипертаблицы TimescaleDB);
- events — журнал событий (аварии / ненормальные / состояния / действия).

На старте SQLite; переход на PostgreSQL+TimescaleDB = смена database_url,
модели останутся теми же (специф §2.3).
"""
from datetime import datetime, timezone

from sqlalchemy import Float, ForeignKey, Integer, String, Text, DateTime, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Базовый класс всех моделей SQLAlchemy."""
    pass


def utcnow() -> datetime:
    """Текущее время в UTC (все метки времени в БД храним в UTC)."""
    return datetime.now(timezone.utc)


class Measurement(Base):
    """Одна точка телеметрии в один момент времени.

    Индекс (point_code, time) — для быстрой выборки истории по точке и периоду
    (требование ТЗ: фильтр по объекту/параметру/временным меткам).
    """
    __tablename__ = "measurements"
    __table_args__ = (
        Index("ix_measurements_code_time", "point_code", "time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    point_code: Mapped[str] = mapped_column(String(32))          # 'U', 'I', 'P'...
    value: Mapped[float] = mapped_column(Float)                  # физическое значение
    quality: Mapped[str] = mapped_column(String(16))             # good | suspect | bad


class Event(Base):
    """Событие в журнале (специф §8, ISA-18.2).

    cls:  alarm     — аварийное событие (сработка защиты)
          abnormal  — ненормальный режим (выход за границы)
          state     — изменение состояния
          operator  — действие оператора (квитирование, ТУ)
    priority: critical | high | low | info  — цвет и порядок в аларм-баре
    """
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    object_name: Mapped[str] = mapped_column(String(128))        # «ТП-1», «Ввод»...
    cls: Mapped[str] = mapped_column(String(16), index=True)     # alarm|abnormal|state|operator
    priority: Mapped[str] = mapped_column(String(16))            # critical|high|low|info
    code: Mapped[str] = mapped_column(String(64))                # машинный код ('short_circuit')
    message: Mapped[str] = mapped_column(Text)                   # человекочитаемый текст
    value: Mapped[float | None] = mapped_column(Float, nullable=True)   # значение сработки
    setpoint: Mapped[float | None] = mapped_column(Float, nullable=True)  # уставка/граница
    # Квитирование (ТЗ 3.3.3): мигание аларма прекращается после ack
    acked: Mapped[bool] = mapped_column(default=False)
    acked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class User(Base):
    """Учётная запись пользователя (специф §5.1: users с ролями RBAC).

    Роли (специф §10):
      admin      — управление пользователями, справочники, пульт симулятора
      dispatcher — квитирование, телеуправление, экспорт
      viewer     — только просмотр (мнемосхема, графики, журналы без действий)
    Пароли хранятся ТОЛЬКО хэшем PBKDF2 с индивидуальной солью (специф §11).
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))      # 'salt$hash' (PBKDF2)
    role: Mapped[str] = mapped_column(String(16), default="viewer")  # admin|dispatcher|viewer
    is_active: Mapped[bool] = mapped_column(default=True)        # деактивация вместо удаления


class OperatorAction(Base):
    """Журнал действий оператора (ТЗ 3.3.2, примечание: ВСЕ действия диспетчера,
    включая неудачные попытки ТУ, с указанием учётной записи)."""
    __tablename__ = "operator_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    username: Mapped[str] = mapped_column(String(64))            # учётка оператора
    action: Mapped[str] = mapped_column(String(64))              # 'ack', 'login', 'tu'...
    object_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result: Mapped[str] = mapped_column(String(32))              # success | failed | denied
    details: Mapped[str | None] = mapped_column(Text, nullable=True)


class Waveform(Base):
    """Осциллограмма при аварийном событии (ТЗ: архив осциллограмм токов/напряжений).

    Каналы — списки сэмплов (JSON-сериализация списков средствами SQLite/JSON-колонок
    не используется: храним через PickleType-free JSON в Text). MVP: фазные каналы.
    """
    __tablename__ = "waveforms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    object_name: Mapped[str] = mapped_column(String(128))
    fault_code: Mapped[int] = mapped_column(Integer)             # 1=КЗ (пока только КЗ)
    sample_rate_hz: Mapped[int] = mapped_column(Integer)         # частота дискретизации
    # 6 каналов: токи и напряжения фаз A/B/C — сериализуем в JSON-строку
    ia: Mapped[str] = mapped_column(Text)   # JSON list[float]
    ua: Mapped[str] = mapped_column(Text)
    ib: Mapped[str] = mapped_column(Text)
    ub: Mapped[str] = mapped_column(Text)
    ic: Mapped[str] = mapped_column(Text)
    uc: Mapped[str] = mapped_column(Text)
