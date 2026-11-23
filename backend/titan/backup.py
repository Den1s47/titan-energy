# -*- coding: utf-8 -*-
"""
Резервное копирование БД (специф §11, §12; ТЗ 3.1: «резервное копирование БД
и конфигурационных файлов — автоматически, по расписанию»).

- create_backup: копия файла SQLite в backups/ с меткой времени;
- rotate_backups: ротация — хранить N последних копий (специф §11);
- restore_backup: восстановление рабочей БД из копии;
- list_backups: список копий (свежие сверху) — для Admin UI и CLI.

Для PostgreSQL путь будет другим (pg_dump/pg_restore), но контракты функций
те же — меняется только реализация при переходе с SQLite (специф §2.3).
"""
import re
import shutil
from datetime import datetime
from pathlib import Path

# Формат имени: titan_YYYYMMDD_HHMMSS.db (сортируется лексикографически = хронологически)
BACKUP_PREFIX = "titan_"
BACKUP_SUFFIX = ".db"


def create_backup(db_path: Path | str, backup_dir: Path | str) -> Path:
    """Создать копию БД: backups/titan_<timestamp>.db.

    Args:
        db_path: путь к рабочей базе (titan.db)
        backup_dir: каталог для копий (создаётся при отсутствии)
    Returns:
        путь к созданной копии
    """
    db_path = Path(db_path)
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"{BACKUP_PREFIX}{timestamp}{BACKUP_SUFFIX}"
    shutil.copy2(db_path, dest)   # copy2 сохраняет метаданные файла
    return dest


def list_backups(backup_dir: Path | str) -> list[Path]:
    """Список бэкапов, свежие сверху (имена с timestamp сортируются сами)."""
    backup_dir = Path(backup_dir)
    if not backup_dir.exists():
        return []
    pattern = re.compile(rf"^{BACKUP_PREFIX}\d{{8}}_\d{{6}}{BACKUP_SUFFIX}$")
    files = [p for p in backup_dir.iterdir() if p.is_file() and pattern.match(p.name)]
    return sorted(files, reverse=True)


def rotate_backups(backup_dir: Path | str, keep: int = 7) -> list[Path]:
    """Ротация: оставить keep свежих копий, удалить остальные.

    Returns:
        список удалённых файлов (для лога)
    """
    backups = list_backups(backup_dir)
    removed = []
    for old in backups[keep:]:
        old.unlink()
        removed.append(old)
    return removed


def restore_backup(backup_path: Path | str, db_path: Path | str) -> None:
    """Восстановить рабочую БД из копии (перезаписывает текущий файл!).

    ВАЖНО: вызывать только при остановленном приложении — иначе активные
    записи коллектора могут попасть в перезаписанный файл.
    """
    shutil.copy2(backup_path, db_path)
