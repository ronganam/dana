"""Setup shared by the standalone test scripts: a clean environment and a
temporary database that is removed at exit.

Call :func:`isolate` first, before importing anything from the app or LiteLLM
(both read the environment at import; a script that loads ``.env`` on purpose
for a model key calls it right after that), then set the values the test
needs::

    import testenv
    DB_PATH = testenv.isolate("about_test")
    os.environ["HOMEASSISTANT_TOKEN"] = "test-ha"   # a fake value, on purpose

Why: LiteLLM loads the repo's ``.env`` when it is imported, and the startup
env import (addons/bootstrap.py) encrypts whatever Home Assistant /
WAHA / Google values it finds into the database. A test must never pick up,
store or use the developer's real credentials, so this:

- stops LiteLLM from loading ``.env`` (``LITELLM_MODE=PRODUCTION``; that is all
  the flag changes) and Dana from loading one in a subprocess such as
  ``bin/dana`` (``DANA_ENV_FILE=""``, core/env_file.py);
- removes every env name the startup import or the identity bootstrap reads
  (whatever the shell exported or something loaded before), so the code
  sees exactly what an unset value gives in production: its defaults;
- points ``DATABASE_PATH`` at a fresh temporary directory, removed with
  everything in it when the process exits;
- gives the first admin a made-up profile (:data:`OWNER_NAME` and the rest
  below), so the prompts under test have someone to talk about. A test that
  needs another one sets the ``OWNER_*`` env values after :func:`isolate`.
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

# The server folder (where tests run from: bin/test puts it and tests/ on the
# import path) and the shared captured fixtures.
SERVER = Path(__file__).resolve().parent.parent
FIXTURES = SERVER / "tests" / "fixtures"

# The old env import's per-account names (read by nothing now: removed so a
# value in your shell cannot hide that), plus the credentials key and the
# identity / locale values core/users.py and core/user_locale.py read.
ADDON_ENV = (
    "HOMEASSISTANT_HOST", "HOMEASSISTANT_TOKEN", "PRIMARY_PERSON", "PHONE_INTERACTIVE_SENSOR",
    "PHONE_BATTERY_SENSOR", "PHONE_NOTIFY_SERVICE", "BRIEFING_WEATHER_ENTITY",
    "WAHA_URL", "WAHA_API_KEY", "WAHA_TOKEN", "WAHA_SESSION", "WHATSAPP_VOICE_TELEGRAM_CHAT_ID",
    "GOOGLE_REFRESH_TOKEN", "GOOGLE_CALENDAR_ID", "FRIGATE_URL",
)
IDENTITY_ENV = ("DANA_ADDON_DIRS", "DANA_SITE", "DANA_CATALOG_URL", "DANA_CATALOG_FILE", "CREDENTIALS_KEY", "API_TOKEN",
                "DANA_INSECURE_DEV", "WAHA_WEBHOOK_SECRET", "ALLOWED_TELEGRAM_USER_IDS", "TELEGRAM_CHAT_ID",
                "OWNER_NAME", "OWNER_CONTEXT", "OWNER_PRONOUN", "OWNER_OTHER_NAMES", "OWNER_LANGUAGES",
                "OWNER_CURRENCY", "DEFAULT_LANGUAGES", "CURRENCY", "DEFAULT_COUNTRY_CODE")

# The first admin of every test database: a made-up person.
OWNER_NAME = "Robin Levi"
OWNER_PRONOUN = "he"
OWNER_OTHER_NAMES = "רובין"
OWNER_CONTEXT = (
    "Robin (רובין) lives in Israel and works in tech. Gal (גל, phone contact 'גל כהן 🚀❤️🔥') is Robin's partner; "
    "they are planning their wedding. 'אמא' is Robin's mother. נועה is Robin's sibling. "
    "Pepper (פפר) is Robin and Gal's dog."
)
# Robin speaks Hebrew and English and pays in shekels (the server's defaults stay en / USD).
OWNER_LANGUAGES = "he,en"
OWNER_CURRENCY = "ILS"
OWNER_ENV = {"OWNER_NAME": OWNER_NAME, "OWNER_PRONOUN": OWNER_PRONOUN,
             "OWNER_OTHER_NAMES": OWNER_OTHER_NAMES, "OWNER_CONTEXT": OWNER_CONTEXT,
             "OWNER_LANGUAGES": OWNER_LANGUAGES, "OWNER_CURRENCY": OWNER_CURRENCY}

_dir: Optional[str] = None


def scratch_dir() -> str:
    """This process's temporary directory (created once, removed at exit)."""
    global _dir
    if _dir is None:
        _dir = tempfile.mkdtemp(prefix="dana-test-")
        atexit.register(shutil.rmtree, _dir, True)
    return _dir


def scratch_path(name: str) -> str:
    """A path inside :func:`scratch_dir` (nothing is created)."""
    return os.path.join(scratch_dir(), name)


# Set by git while a hook runs: a test's own git commands would act on the real repository.
GIT_LOCATION_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_PREFIX", "GIT_NAMESPACE",
                    "GIT_QUARANTINE_PATH")


def isolate(name: str) -> str:
    """Clean the environment for a test process and point ``DATABASE_PATH``
    at ``<scratch dir>/<name>.sqlite3``. Returns that path."""
    os.environ["LITELLM_MODE"] = "PRODUCTION"
    os.environ["DANA_ENV_FILE"] = ""  # a subprocess (bin/dana) never reads a real server/.env
    for env in ADDON_ENV + IDENTITY_ENV + GIT_LOCATION_ENV:
        os.environ.pop(env, None)
    os.environ.update(OWNER_ENV)
    os.environ["DANA_ADDON_DIRS"] = ""  # never your site's addons (a test that wants one sets it)
    os.environ["APP_RELEASES_REPO"] = ""  # no app updates read from GitHub (test_app_updates fakes it)
    path = scratch_path(f"{name}.sqlite3")
    os.environ["DATABASE_PATH"] = path
    return path


def connect_admin(addon: str, credentials: Optional[dict] = None, settings: Optional[dict] = None) -> None:
    """Connect ``addon`` for the first admin, as the server (``by=None``), the
    way an operator does with ``bin/dana addons connect``: for a test whose
    first admin has an addon with credentials or settings. Call it after
    ``users.bootstrap()``; the app is imported here, not at this module's import."""
    from core import users
    from core.addons import store
    admin = users.first_admin()
    assert admin is not None, "connect_admin needs users.bootstrap() first"
    if store.is_connected(admin, addon):
        store.update(admin, addon, credentials=credentials, settings=settings)
    else:
        store.connect(admin, addon, credentials=credentials, settings=settings)
