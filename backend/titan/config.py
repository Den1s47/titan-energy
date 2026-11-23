# -*- coding: utf-8 -*-
"""Конфигурация приложения.

Все настройки читаются из .env / переменных окружения (специф §11: секреты —
в .env вне репозитория). Значения по умолчанию подобраны для локальной разработки.

Тесты: при TITAN_TEST_DB=1 БД подменяется на временный файл — тесты не трогают
рабочую titan.db и всегда стартуют с чистой схемой.
"""
import os
import tempfile

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Все параметры приложения в одном месте; переопределяются через .env."""

    # Приложение
    app_name: str = "Титан-Энергия EMS"

    # База данных. MVP-старт: SQLite (файл рядом с проектом).
    # Прод-цель: PostgreSQL+TimescaleDB — меняется ТОЛЬКО эта строка (специф §2.3).
    database_url: str = "sqlite+aiosqlite:///./titan.db"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Режим тестов: уникальная временная БД на процесс (изоляция прогонов)
        # + отдельный порт Modbus (5021), чтобы тестовый симулятор не конфликтовал
        # с живым сервером разработчика на 5020 (иначе коллектор теста подключается
        # к чужому симулятору и ТУ-тесты получают «неизменяемое» состояние).
        if os.environ.get("TITAN_TEST_DB") == "1":
            test_path = os.path.join(
                tempfile.gettempdir(), f"titan_test_{os.getpid()}.db"
            )
            self.database_url = f"sqlite+aiosqlite:///{test_path}"
            self.sim_port = 5021

    # Симулятор Modbus TCP
    sim_host: str = "127.0.0.1"      # адрес «оборудования»
    sim_port: int = 5020             # порт Modbus TCP (стандартный 502 занят системой)
    sim_enabled: bool = True         # embedded-режим: симулятор стартует вместе с API
    collector_enabled: bool = True   # опрос включён
    poll_interval: float = 1.0       # период опроса, с (ТЗ: не реже 1 раза в секунду)

    # Безопасность (специф §11): JWT-токены для аутентификации
    jwt_secret: str = "dev-secret-change-me"  # В ПРОДЕ менять через .env!
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480    # срок жизни токена: 8 часов (рабочая смена)

    # Сеть: биндимся на localhost (в LAN-режиме меняется на IP интерфейса)
    host: str = "127.0.0.1"
    port: int = 8000

    # pydantic-settings: разрешаем читать значения из файла .env
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


# Единственный экземпляр настроек, импортируется везде как `from titan.config import settings`
settings = Settings()

