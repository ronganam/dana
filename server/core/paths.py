"""Filesystem locations for everything the assistant persists.

All state lives in one data directory: the folder that holds the SQLite
database (``DATABASE_PATH``). In Kubernetes that folder is the mounted
PersistentVolume (``/app/db``), so recordings, media, uploads and published
APKs survive pod restarts together with the database. Anything written outside
it is lost on the next rollout.
"""

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

DATABASE_PATH = Path(os.getenv("DATABASE_PATH") or _REPO_ROOT / "db.sqlite3").resolve()
DATA_DIR = DATABASE_PATH.parent

RECORDINGS_DIR = DATA_DIR / "recordings"
MEDIA_DIR = DATA_DIR / "media"
UPLOADS_DIR = DATA_DIR / "uploads"
IMAGES_DIR = UPLOADS_DIR / "images"
APP_UPDATES_DIR = DATA_DIR / "app-updates"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
# The money addon: the bank worker's token file and its browser profiles (shared with the sidecar).
MONEY_DIR = DATA_DIR / "money"
# Files kept on knowledge entries (quotes, voice notes; addons/knowledge/files.py).
KNOWLEDGE_FILES_DIR = DATA_DIR / "knowledge"
# Addons installed from the catalog, one folder each (core/addon_dirs.py).
ADDONS_DIR = DATA_DIR / "addons"


def ensure_data_dirs() -> None:
    """Create the data sub-directories (idempotent)."""
    for d in (RECORDINGS_DIR, MEDIA_DIR, IMAGES_DIR, APP_UPDATES_DIR, KNOWLEDGE_FILES_DIR):
        d.mkdir(parents=True, exist_ok=True)
