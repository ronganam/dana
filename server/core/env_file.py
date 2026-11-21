"""Dana's own environment file (docs/configuration.md, "The .env file").

:func:`load` runs first thing in ``main.py`` and ``bin/dana``, before any
other module of Dana (several read their configuration at import) and before
anything imports LiteLLM:

- it loads exactly one file, ``server/.env`` (``DANA_ENV_FILE`` names another;
  ``""``: none, what the tests use), without overriding a variable already set;
- it sets ``LITELLM_MODE=PRODUCTION``: otherwise LiteLLM, when imported, loads
  whatever ``.env`` python-dotenv finds walking up from LiteLLM's own install
  folder (a virtual environment inside ``server/`` finds the ``server/.env``
  of that checkout, whichever checkout is running).
"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def path() -> Optional[Path]:
    """The file :func:`load` reads: ``DANA_ENV_FILE`` when set (None for ``""``), else ``server/.env``."""
    raw = os.environ.get("DANA_ENV_FILE")
    if raw is None:
        return ENV_FILE
    return Path(raw).expanduser() if raw.strip() else None


def load() -> None:
    """Load Dana's ``.env`` (if it exists; set variables win) and keep LiteLLM from loading any."""
    target = path()
    if target is not None and target.is_file():
        load_dotenv(target, override=False)
    os.environ["LITELLM_MODE"] = "PRODUCTION"
