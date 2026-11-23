# -*- coding: utf-8 -*-
"""
Время в системе (специф: метки событий с точностью до мс, ТЗ 3.3.3).

Хранение: всё в UTC (правильная практика, никаких сдвигов между машинами).
Отдача клиенту: UTC -> системная таймзона диспетчера (по умолчанию GMT+3 —
Москва/Минск, требование пользователя). Формат ISO с миллисекундами.
"""
from datetime import datetime, timezone, timedelta

# Таймзона диспетчерских меток: GMT+3 (Москва/Минск), без перехода на летнее время
LOCAL_TZ = timezone(timedelta(hours=3), name="GMT+3")

# Часовой пояс берётся из окружения (TITAN_TZ_OFFSET_HOURS), по умолчанию +3
import os
_offset = int(os.environ.get("TITAN_TZ_OFFSET_HOURS", "3"))
LOCAL_TZ = timezone(timedelta(hours=_offset), name=f"GMT{_offset:+d}")


def utcnow() -> datetime:
    """Текущее время в UTC (для записи в БД)."""
    return datetime.now(timezone.utc)


def to_local(dt: datetime) -> datetime:
    """UTC -> таймзона диспетчера (GMT+3 по умолчанию)."""
    if dt.tzinfo is None:
        # Naive datetime из БД (SQLite отдаёт без tzinfo) — считаем его UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ)


def fmt_iso_ms(dt: datetime) -> str:
    """Формат для клиентов: локальное время ISO с миллисекундами.

    Пример: '2026-11-21T03:45:58.041' (без хвоста '+03:00' — UI ждёт
    чистое время, показываем диспетчеру его таймзону).
    """
    local = to_local(dt)
    return local.strftime("%Y-%m-%dT%H:%M:%S.") + f"{local.microsecond // 1000:03d}"
