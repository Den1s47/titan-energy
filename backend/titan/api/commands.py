# -*- coding: utf-8 -*-
"""
Телеуправление (ТУ) — полный алгоритм ТЗ 3.3.2 / специф §7.

Двухшаговый алгоритм:
1. SELECT: диспетчер выбирает объект и команду -> система проверяет блокировки
   и выдаёт одноразовый токен операции (живает 10 с — «таймаут выбора»).
2. OPERATE: по токену команда уходит в «оборудование» (write-канал адаптера)
   и система ждёт изменения состояния до 5 с («окно ожидания»).

Все шаги — в журнале оператора, включая неудачные (ТЗ 3.3.2, примечание).
"""
import asyncio
import logging
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from titan.api.auth import log_action, require_role
from titan.collector.service import Collector
from titan.config import settings
from titan.models.session import sessionmaker
from titan.simulator.engine import REG_CMD

log = logging.getLogger(__name__)

# Таймауты из ТЗ (специф §7): выбор 10 с, ожидание смены состояния 5 с
SELECT_TIMEOUT_S = 10.0
OPERATE_WAIT_S = 5.0

router = APIRouter(prefix="/api/commands")

# Глобальные ссылки устанавливаются из app.py при старте (см. set_services)
_collector: Collector | None = None
_simulator = None

# Активные токены выбора: token -> {object, action, expires_at, username}
_pending: dict[str, dict] = {}

# Программные блокировки ТУ (специф §7.2: таблица interlocks).
# Функция получает состояние системы и возвращает текст блокировки или None.
# Multi-object: ключи live-кэша — 'arm1_incomer:FAULT', 'sec_qf:BREAKER'.
def _check_interlocks(object_name: str, action: str, live: dict) -> str | None:
    # Блокировка «аварийный режим»: код аварии на ЛЮБОМ плече != 0
    for key, reading in live.items():
        if key.endswith(":FAULT") and reading.value != 0:
            return "аварийный режим — ТУ запрещено"

    # --- Блокировки СЕКЦИОННОГО выключателя (MVP_SCADA_Denis) ---
    if object_name == "Секционный QF":
        if action == "close":
            # 1) Параллельная работа источников запрещена (ключевая блокировка
            #    из документа: оба ввода должны быть в работе — переброс допустим
            #    только когда одно плечо ОБЕСТОЧЕНО)
            a = live.get("arm1_incomer:BREAKER")
            b = live.get("arm2_incomer:BREAKER")
            if a and b and int(a.value) == 1 and int(b.value) == 1:
                return "параллельная работа вводов запрещена — отключите один из вводов перед включением СВ"
            # 2) Если ОБА ввода отключены — включать СВ некуда (нечего перебрасывать)
            if a and b and int(a.value) == 0 and int(b.value) == 0:
                return "оба ввода обесточены — включение СВ бессмысленно"
        # Открывать СВ при запитанной секции можно (возврат на свой ввод)
        return None

    if object_name == "Ввод 10 кВ №1" and action == "open":
        for key, reading in live.items():
            if key.endswith(":P") and reading.value > 300:
                return "отключение ввода при мощности > 300 кВт запрещено (демо-блокировка)"
    return None


def set_services(collector: Collector, simulator) -> None:
    """Передать ссылки на коллектор и симулятор (вызывается из lifespan)."""
    global _collector, _simulator
    _collector = collector
    _simulator = simulator


class SelectRequest(BaseModel):
    """Шаг 1: выбор объекта и команды."""
    object_name: str      # 'Ввод' (в MVP один управляемый аппарат)
    action: str           # 'close' | 'open'


class OperateRequest(BaseModel):
    """Шаг 2: подтверждение операции по токену."""
    token: str


@router.post("/select")
async def select_operation(body: SelectRequest,
                           user: dict = Depends(require_role("dispatcher"))) -> dict:
    """Шаг 1 ТУ: проверить блокировки и выдать токен операции.

    - viewer получает 403 (зависимость require_role);
    - при блокировке — 409 «Телеуправление запрещено» + тип блокировки
      (кнопка исполнения на UI будет неактивна);
    - токен одноразовый и истекает через 10 с.
    """
    if body.action not in ("close", "open"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "action must be 'close' or 'open'")

    lock_reason = _check_interlocks(body.object_name, body.action, _collector.live)
    if lock_reason:
        # Аудит: отказ из-за блокировки (специф §7.6)
        await log_action(sessionmaker, user["username"], "tu_select", "denied",
                         object_name=body.object_name,
                         details=f"{body.action}: блокировка — {lock_reason}")
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail=f"Телеуправление запрещено: {lock_reason}")

    token = secrets.token_hex(16)
    _pending[token] = {
        "object": body.object_name,
        "action": body.action,
        "expires_at": time.monotonic() + SELECT_TIMEOUT_S,
        "username": user["username"],
    }
    # Подчищаем просроченные токены
    now = time.monotonic()
    for t in [t for t, v in _pending.items() if v["expires_at"] < now]:
        del _pending[t]

    await log_action(sessionmaker, user["username"], "tu_select", "success",
                     object_name=body.object_name,
                     details=f"выбрана команда {body.action} (токен выдан, 10 с)")
    return {"token": token, "expires_in_s": SELECT_TIMEOUT_S}


@router.post("/operate")
async def operate(body: OperateRequest,
                  user: dict = Depends(require_role("dispatcher"))) -> dict:
    """Шаг 2 ТУ: исполнить команду и дождаться смены состояния.

    Алгоритм (специф §7.5):
    - токен валиден и не истёк (иначе 410 — «выбор сброшен»);
    - write-команда в симулятор (регистр 9) — актуатор исполнит её с задержкой;
    - поллим live-кэша раз в 0.25 с до 5 с: ждём BREAKER == целевому состоянию;
    - успех -> сообщение об успехе; таймаут -> «неопределённое» состояние,
      команда сброшена, сообщение о неуспехе.
    В любом исходе — запись в журнал оператора.
    """
    op = _pending.get(body.token)
    if op is None or op["expires_at"] < time.monotonic():
        _pending.pop(body.token, None)
        await log_action(sessionmaker, user["username"], "tu_operate", "failed",
                         details="токен истёк или отсутствует (выбор сброшен)")
        raise HTTPException(status.HTTP_410_GONE,
                            detail="Время выбора истекло — операция сброшена. Начните заново.")

    # Токен одноразовый: забираем сразу
    _pending.pop(body.token, None)
    target_state = 1 if op["action"] == "close" else 0
    cmd_code = 1 if op["action"] == "close" else 2

    # --- Маршрутизация команды по объекту (модель ПС 110/10) ---
    # 'Секционный QF' -> unit 2, метод command_sectional (регистр 12);
    # 'Ввод 10 кВ №1' -> unit 1, command (регистр 9);
    # 'Ввод 10 кВ №2' -> unit 2, command (регистр 9).
    if op["object"] == "Секционный QF":
        _simulator.get_engine(2).command_sectional(cmd_code)
        breaker_key = "sec_qf:BREAKER"
    elif op["object"] == "Ввод 10 кВ №2":
        _simulator.get_engine(2).command(cmd_code)
        breaker_key = "arm2_incomer:BREAKER"
    else:
        _simulator.get_engine(1).command(cmd_code)
        breaker_key = "arm1_incomer:BREAKER"
    log.info("ТУ %s: команда %d выдана пользователем %s",
             op["object"], cmd_code, user["username"])

    # --- Ожидание изменения состояния (окно 5 с, поллинг 0.25 с) ---
    deadline = time.monotonic() + OPERATE_WAIT_S
    success = False
    while time.monotonic() < deadline:
        await asyncio.sleep(0.25)
        breaker = _collector.live.get(breaker_key)
        if breaker is not None and int(breaker.value) == target_state:
            success = True
            break

    # --- Аудит исхода (ТЗ 3.3.2: включая неудачные) ---
    if success:
        await log_action(sessionmaker, user["username"], "tu_operate", "success",
                         object_name=op["object"],
                         details=f"команда {op['action']} исполнена: состояние подтверждено")
        return {
            "ok": True,
            "state": target_state,
            "message": f"Команда исполнена: выключатель {'включён' if target_state else 'отключён'}",
        }

    # Неуспех: актуатор не подтвердил (отказ привода / таймаут)
    await log_action(sessionmaker, user["username"], "tu_operate", "failed",
                     object_name=op["object"],
                     details=f"команда {op['action']}: нет смены состояния за {OPERATE_WAIT_S} с")
    raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT,
                        detail="Команда не исполнена: состояние не изменилось за 5 с. "
                               "Состояние — «неопределённое», операция сброшена.")
