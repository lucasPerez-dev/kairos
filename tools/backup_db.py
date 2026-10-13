#!/usr/bin/env python3
"""Sauvegarde de la base Kairos (API sqlite3, sûre même base ouverte) + rotation.

Usage :
  python tools/backup_db.py --db data/kairos.db --dest backups --keep 60
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Sauvegarde de la base Kairos")
    parser.add_argument("--db", default="data/kairos.db")
    parser.add_argument("--dest", default="backups")
    parser.add_argument("--keep", type=int, default=60)
    args = parser.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"Base introuvable : {db}")
        return 1

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"kairos-{time.strftime('%Y-%m-%d_%H%M')}.db"

    source = sqlite3.connect(db)
    target = sqlite3.connect(out)
    with target:
        source.backup(target)
    target.close()
    source.close()

    backups = sorted(dest.glob("kairos-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[args.keep :]:
        old.unlink()

    print(f"Sauvegarde : {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
