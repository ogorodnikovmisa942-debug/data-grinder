"""Резервные копии базы: сжатая проверенная копия, ротация, внешняя папка."""
import gzip
import os
import sqlite3
import time

import pytest

from app.services import backup


def _db(path):
    con = sqlite3.connect(path)
    con.execute("create table cards (id integer primary key, text text)")
    con.executemany("insert into cards (text) values (?)", [(f"карточка {i}",) for i in range(50)])
    con.commit()
    con.close()


def test_backup_is_a_valid_compressed_copy_with_an_external_second_copy(tmp_path):
    db = str(tmp_path / "data.db")
    _db(db)
    info = backup.make_backup(db, str(tmp_path / "b"), keep=5, external_dir=str(tmp_path / "ext"))
    assert info["file"].endswith(".db.gz") and os.path.exists(info["external"]) and info["removed_old"] == 0
    restored = tmp_path / "restored.db"
    with gzip.open(info["file"], "rb") as f:
        restored.write_bytes(f.read())
    con = sqlite3.connect(restored)
    assert con.execute("select count(*) from cards").fetchone()[0] == 50 and con.execute("pragma integrity_check").fetchone()[0] == "ok"
    con.close()


def test_rotation_keeps_only_the_newest_copies(tmp_path):
    dest = tmp_path / "b"
    dest.mkdir()
    for i in range(6):
        (dest / f"data_grinder_2026010{i}_000000.db.gz").write_bytes(b"x")
    assert backup.rotate(str(dest), 3) == 3
    assert sorted(os.listdir(dest)) == [f"data_grinder_2026010{i}_000000.db.gz" for i in (3, 4, 5)]


def test_missing_or_empty_database_is_an_error_and_age_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup.make_backup(str(tmp_path / "none.db"), str(tmp_path / "b"))
    (tmp_path / "empty.db").write_bytes(b"")
    with pytest.raises(FileNotFoundError):
        backup.make_backup(str(tmp_path / "empty.db"), str(tmp_path / "b"))
    assert backup.newest_backup_age_hours(str(tmp_path / "b")) is None
    db = str(tmp_path / "data.db")
    _db(db)
    backup.make_backup(db, str(tmp_path / "b"))
    age = backup.newest_backup_age_hours(str(tmp_path / "b"))
    assert age is not None and age < 0.1
