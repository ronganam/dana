"""Write a transactionally consistent copy of the SQLite database.

Usage: ``python -m core.db_snapshot``

Velero backs up the data volume by copying files, which can capture
``db.sqlite3`` halfway through a write. Its pre-backup hook runs this module
first, so every backup also holds ``snapshots/db.sqlite3`` — a consistent copy
made with SQLite's online backup API. Restore from that file.

``core.db.migrate`` uses the same function to keep a copy of the database as it
was before applying migrations (``snapshots/db-before-<version>.sqlite3``).
"""

import sqlite3
from pathlib import Path
from typing import Optional

from core.paths import DATABASE_PATH, SNAPSHOTS_DIR


def snapshot(source: Path = DATABASE_PATH, name: Optional[str] = None) -> Path:
    """Copy ``source`` into the ``snapshots/`` folder next to it, as ``name``
    (default: the source's own file name). Returns the written path."""
    snapshots_dir = source.parent / SNAPSHOTS_DIR.name
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    target = snapshots_dir / (name or source.name)
    tmp = target.with_name(target.name + ".tmp")
    tmp.unlink(missing_ok=True)

    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    tmp.replace(target)
    return target


if __name__ == "__main__":
    path = snapshot()
    print(f"Snapshot written to {path} ({path.stat().st_size:,} bytes)")
