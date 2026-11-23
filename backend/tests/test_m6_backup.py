# -*- coding: utf-8 -*-
"""
Тесты M6: резервное копирование и восстановление БД.

Проверяем функцию бэкапа на реальном файле SQLite:
- бэкап создаётся с временной меткой в имени;
- восстановление из бэкапа даёт идентичные данные.
"""
import sqlite3
from pathlib import Path

from titan.backup import create_backup, restore_backup, list_backups


def test_backup_creates_file(tmp_path):
    """Бэкап создаёт копию БД с timestamp в имени в папке backups/."""
    # Рабочая БД с данными
    db = tmp_path / "titan.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('hello')")
    conn.commit()
    conn.close()

    backup_dir = tmp_path / "backups"
    path = create_backup(db_path=db, backup_dir=backup_dir)

    assert path.exists()
    assert path.suffix == ".db"
    assert path.name.startswith("titan_")            # titan_YYYYMMDD_HHMMSS.db
    assert path.parent == backup_dir
    # Копия читаема и содержит данные
    c = sqlite3.connect(path)
    rows = c.execute("SELECT v FROM t").fetchall()
    c.close()
    assert rows == [("hello",)]


def test_list_backups_sorted(tmp_path):
    """Список бэкапов отсортирован: свежие сверху."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()                                # каталог должен существовать
    db = tmp_path / "titan.db"
    db.write_text("x")

    paths = []
    for i in range(5):
        p = backup_dir / f"titan_2026011{i}_120000.db"
        p.write_text(f"backup{i}")
        paths.append(p)

    listed = list_backups(backup_dir)
    assert listed[0] == paths[4]                     # свежий (последний) первым
    assert len(listed) == 5


def test_rotation_keeps_max_n(tmp_path):
    """Ротация: старше N копий удаляются (специф §11: «хранить N копий»)."""
    from titan.backup import rotate_backups
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    for i in range(7):
        (backup_dir / f"titan_2026011{i}_120000.db").write_text("x")

    rotate_backups(backup_dir, keep=5)
    remaining = sorted(p.name for p in backup_dir.glob("*.db"))
    assert len(remaining) == 5
    assert remaining[0] == "titan_20260112_120000.db"   # два старейших удалены


def test_restore_backup(tmp_path):
    """Восстановление: из бэкапа данные возвращаются в рабочую БД."""
    # Исходная БД
    db = tmp_path / "titan.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('important')")
    conn.commit()
    conn.close()

    backup_dir = tmp_path / "backups"
    backup_path = create_backup(db_path=db, backup_dir=backup_dir)

    # Портируем рабочую БД
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE t")
    conn.commit()
    conn.close()

    # Восстанавливаем
    restore_backup(backup_path=backup_path, db_path=db)
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT v FROM t").fetchall()
    conn.close()
    assert rows == [("important",)]
