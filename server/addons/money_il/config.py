"""money_il configuration: the addon's name and the bank worker's address and
token file (docs/money-il.md, docs/configuration.md).

The env value is read when used, so a test can point it elsewhere."""

import os
from pathlib import Path

from core import paths

ADDON = "money_il"

DEFAULT_WORKER_URL = "http://127.0.0.1:8090"


def worker_url() -> str:
    """The bank worker's base URL (``MONEY_WORKER_URL``)."""
    return (os.getenv("MONEY_WORKER_URL") or DEFAULT_WORKER_URL).strip().rstrip("/")


def token_file() -> Path:
    """The file holding the token the worker checks (the shared ``money/`` folder of the data)."""
    return paths.MONEY_DIR / "worker-token"
