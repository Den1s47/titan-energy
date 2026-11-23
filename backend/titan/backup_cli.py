# -*- coding: utf-8 -*-
"""
CLI бэкапа БД «Титан-Энергия» (запускается планировщиком Windows, специф §11-12).

Использование (из backend/):
    uv run python -m titan.backup_cli            # создать копию + ротация (keep=7)
    uv run python -m titan.backup_cli --keep 14  # хранить 14 копий
    uv run python -m titan.backup_cli --list     # показать список копий
    uv run python -m titan.backup_cli --restore titan_20261121_120000.db
"""
import argparse
import logging
import sys
from pathlib import Path

from titan.backup import create_backup, list_backups, restore_backup, rotate_backups

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Пути по умолчанию относительно корня backend (файл titan.db лежит в backend/)
BACKEND_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BACKEND_DIR / "titan.db"
BACKUP_DIR = BACKEND_DIR / "backups"


def main() -> int:
    parser = argparse.ArgumentParser(description="Бэкап БД «Титан-Энергия»")
    parser.add_argument("--keep", type=int, default=7, help="сколько копий хранить (ротация)")
    parser.add_argument("--list", action="store_true", help="показать список копий")
    parser.add_argument("--restore", metavar="BACKUP_NAME", help="восстановить из копии")
    args = parser.parse_args()

    # --- Восстановление ---
    if args.restore:
        src = BACKUP_DIR / args.restore
        if not src.exists():
            log.error("Копия не найдена: %s", src)
            return 1
        restore_backup(backup_path=src, db_path=DB_PATH)
        log.info("БД восстановлена из %s -> %s", src.name, DB_PATH.name)
        log.warning("Перезапустите приложение, чтобы коллектор писал в новую БД")
        return 0

    # --- Список ---
    if args.list:
        backups = list_backups(BACKUP_DIR)
        if not backups:
            log.info("Копий пока нет")
        for p in backups:
            size_kb = p.stat().st_size / 1024
            log.info("%s  (%.1f КБ)", p.name, size_kb)
        return 0

    # --- Бэкап + ротация (основной режим для планировщика) ---
    if not DB_PATH.exists():
        log.error("Рабочая БД не найдена: %s", DB_PATH)
        return 1
    path = create_backup(db_path=DB_PATH, backup_dir=BACKUP_DIR)
    log.info("Копия создана: %s (%.1f КБ)", path.name, path.stat().st_size / 1024)
    removed = rotate_backups(BACKUP_DIR, keep=args.keep)
    if removed:
        log.info("Ротация: удалено старых копий — %d", len(removed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
