#!/usr/bin/env python3
"""Резервная копия базы SQLite одной командой (для cron): сжатая, проверенная, с ротацией и копией во внешнюю папку.

    python scripts/backup_db.py                       # backups/, хранить 14 копий
    python scripts/backup_db.py --keep 30 --external D:/облако/grinder
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.backup import DEFAULT_KEEP, make_backup  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data_grinder.db")
    ap.add_argument("--dest", default="backups")
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    ap.add_argument("--external", default="", help="внешняя папка для второй копии (например, синхронизируемый в облако диск)")
    args = ap.parse_args()
    info = make_backup(args.db, args.dest, args.keep, args.external or None)
    print(f"Копия: {info['file']} ({info['bytes'] // 1024} КБ); внешняя: {info['external'] or '—'}; старых удалено: {info['removed_old']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
