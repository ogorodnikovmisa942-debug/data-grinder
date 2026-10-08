"""Резервные копии базы SQLite: ежедневно, с проверкой целостности, ротацией и копией во внешнюю папку (диск, синхронизируемый в облако).

Копия делается через SQLite Backup API (безопасно при работающем сервере и WAL), сжимается gzip. Старше BACKUP_KEEP копий удаляются.
Запуск вручную или из cron: python scripts/backup_db.py [--keep 14] [--external /путь]. Внутри сервера работает backup_scheduler_loop.
"""
import asyncio
import glob
import gzip
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone

PREFIX = "data_grinder_"
SUFFIX = ".db.gz"
DEFAULT_KEEP = 14


def _integrity_ok(path: str) -> bool:
    con = sqlite3.connect(path)
    try:
        return con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        con.close()


def rotate(dest_dir: str, keep: int) -> int:
    """Оставляет keep самых свежих копий, остальные удаляет. Возвращает, сколько удалено."""
    files = sorted(glob.glob(os.path.join(dest_dir, f"{PREFIX}*{SUFFIX}")))
    old = files[:-keep] if keep > 0 else []
    for f in old:
        try:
            os.unlink(f)
        except OSError:
            pass
    return len(old)


def make_backup(db_file: str = "data_grinder.db", dest_dir: str = "backups", keep: int = DEFAULT_KEEP, external_dir: str | None = None) -> dict:
    """Одна копия: SQLite Backup API → проверка целостности → gzip → внешняя папка → ротация. Бросает исключение, если копия повреждена."""
    if not os.path.exists(db_file) or os.path.getsize(db_file) == 0:
        raise FileNotFoundError(f"Нет базы для копии: {db_file}")
    os.makedirs(dest_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    target = os.path.join(dest_dir, f"{PREFIX}{stamp}{SUFFIX}")
    fd, tmp = tempfile.mkstemp(suffix=".db", dir=dest_dir)
    os.close(fd)
    try:
        src = sqlite3.connect(db_file)
        try:
            dst = sqlite3.connect(tmp)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        if not _integrity_ok(tmp):
            raise RuntimeError("Копия базы не прошла проверку целостности")
        with open(tmp, "rb") as f_in, gzip.open(target, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    copied = None
    if external_dir:
        os.makedirs(external_dir, exist_ok=True)
        copied = shutil.copy2(target, external_dir)
    removed = rotate(dest_dir, keep)
    if external_dir:
        rotate(external_dir, keep)
    return {"file": target, "bytes": os.path.getsize(target), "external": copied, "removed_old": removed}


def newest_backup_age_hours(dest_dir: str = "backups") -> float | None:
    files = glob.glob(os.path.join(dest_dir, f"{PREFIX}*{SUFFIX}"))
    if not files:
        return None
    return (datetime.now().timestamp() - max(os.path.getmtime(f) for f in files)) / 3600


async def backup_scheduler_loop(db_file: str = "data_grinder.db", every_hours: float = 24.0) -> None:
    """Раз в сутки (проверка раз в час): свежей копии нет — делаем. Сбой не останавливает сервер."""
    from app.core.config import settings
    while True:
        try:
            age = newest_backup_age_hours()
            if age is None or age >= every_hours:
                info = await asyncio.to_thread(make_backup, db_file, "backups", settings.BACKUP_KEEP, settings.BACKUP_EXTERNAL_DIR or None)
                print(f"[Backup] Копия базы: {info['file']} ({info['bytes'] // 1024} КБ), старых удалено {info['removed_old']}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[Backup WARN] Не удалось сделать копию базы: {e}", flush=True)
        await asyncio.sleep(3600)
