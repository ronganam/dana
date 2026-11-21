"""Startup for integrations: check ``CREDENTIALS_KEY``, then import the first
admin's integrations from env, once per kind (docs/access.md, "Credentials").

This module is the one place that reads the env names below; the kinds read
their configuration only from the user's connection. After the first start has imported a kind, its env names can be
removed from the secret store (docs/configuration.md).

Each kind is imported at most once: a kind connected here is recorded in
``integration_imports``, so one the first admin disconnects later is not
created again. A credentialed kind whose env values are absent is not
recorded, so it is imported at the first start that has them.
"""

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from core import users
from core.users import User
from integrations.framework import store
from integrations.framework.kind import InvalidValue, get_kind
from integrations.cameras import config as cameras_config
from integrations.homeassistant import config as ha_config
from integrations.whatsapp import config as wa_config

logger = logging.getLogger(__name__)


# The env names whose values the import stores as credentials (encrypted):
# the debug export masks them for an admin too (core/debug_env.py).
CREDENTIAL_ENV = ("HOMEASSISTANT_HOST", "HOMEASSISTANT_TOKEN", "WAHA_URL", "WAHA_API_KEY", "WAHA_TOKEN",
                  "WAHA_SESSION", "GOOGLE_REFRESH_TOKEN")


@dataclass(frozen=True)
class EnvImport:
    """What the first admin gets for one kind: ``None`` when the env does not configure it."""
    credentials: Dict[str, str]
    settings: Dict[str, Any]


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _env_or(name: str, default: str) -> str:
    """The env value, else today's code default (production sets most of these only through defaults)."""
    return _env(name) or default


def _env_chat_id(name: str) -> Optional[int]:
    """A Telegram chat id from env, parsed as the code parses it today (None: unset or not a number)."""
    raw = _env(name)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning(f"{name} is not a numeric chat id; not imported.")
        return None


def _setting(kind: str, name: str, env: str) -> Any:
    """Setting ``name`` of ``kind`` from env ``env``, else the kind's declared
    default (= what the code falls back to today)."""
    default = next(f.default for f in get_kind(kind).settings if f.name == name)
    return _env(env) or default


def _homeassistant() -> Optional[EnvImport]:
    if not _env("HOMEASSISTANT_TOKEN"):
        return None
    # Entities without an env value stay unset: the user picks them on the
    # Integrations page (or with bin/users.py).
    return EnvImport(
        credentials={"host": _env_or("HOMEASSISTANT_HOST", ha_config.DEFAULT_HOST),
                     "token": _env("HOMEASSISTANT_TOKEN")},
        settings={
            ha_config.PERSON: _env("PRIMARY_PERSON") or None,
            ha_config.SCREEN_SENSOR: _env("PHONE_INTERACTIVE_SENSOR") or None,
            ha_config.BATTERY_SENSOR: _env("PHONE_BATTERY_SENSOR") or None,
            ha_config.NOTIFY_SERVICE: _env("PHONE_NOTIFY_SERVICE") or None,
            ha_config.WEATHER: _setting(ha_config.KIND, ha_config.WEATHER, "BRIEFING_WEATHER_ENTITY"),
        })


def _whatsapp() -> Optional[EnvImport]:
    api_key = _env("WAHA_API_KEY") or _env("WAHA_TOKEN")
    if not _env("WAHA_URL"):
        return None
    credentials = {"url": _env("WAHA_URL"),
                   "session": _env_or("WAHA_SESSION", wa_config.DEFAULT_SESSION)}
    if api_key:
        credentials["api_key"] = api_key
    return EnvImport(credentials=credentials,
                     settings={wa_config.VOICE_CHAT: _env_chat_id("WHATSAPP_VOICE_TELEGRAM_CHAT_ID")})


def _calendar() -> Optional[EnvImport]:
    if not _env("GOOGLE_REFRESH_TOKEN"):
        return None
    return EnvImport(credentials={"refresh_token": _env("GOOGLE_REFRESH_TOKEN")},
                     settings={"calendar_id": _setting("calendar", "calendar_id", "GOOGLE_CALENDAR_ID")})


def _cameras() -> Optional[EnvImport]:
    if not _env("FRIGATE_URL"):
        return None
    return EnvImport(credentials={}, settings={cameras_config.URL: _env("FRIGATE_URL").rstrip("/")})


def _built_in() -> EnvImport:
    return EnvImport(credentials={}, settings={})


# kind -> what to import for the first admin (in the fixed kind order).
IMPORTS: Dict[str, Callable[[], Optional[EnvImport]]] = {
    "tasks": _built_in,
    "shopping": _built_in,
    "knowledge": _built_in,
    "about": _built_in,
    "people": _built_in,
    "phone": _built_in,
    "homeassistant": _homeassistant,
    "whatsapp": _whatsapp,
    "calendar": _calendar,
    "cameras": _cameras,
    "ops": _built_in,
}


def pending(user: User) -> Dict[str, EnvImport]:
    """Kinds not imported yet that the env configures, with their values."""
    done = set(store.imported_kinds())
    out: Dict[str, EnvImport] = {}
    for kind, read in IMPORTS.items():
        if kind in done:
            continue
        values = read()
        if values is not None:
            waiting = store.import_settings(kind)  # moved here by a migration (m0012: selected_calendars)
            out[kind] = EnvImport(values.credentials, {**values.settings, **waiting}) if waiting else values
    return out


def _connect(admin: User, kind: str, values: EnvImport) -> bool:
    """Connect ``kind`` for the first admin. A setting the rules refuse
    (docs/access.md, "Security rules": an address with a query, a user name
    or a password, …) is left out and logged; a refused
    credential (an address) leaves the kind out. Returns whether it connected."""
    settings = {k: v for k, v in values.settings.items() if v is not None}
    while True:
        try:
            store.connect(admin, kind, credentials=values.credentials or None, settings=settings)
            return True
        except InvalidValue as e:
            if e.field in settings:
                logger.error(f"Env import of {kind}: setting {e.field} left out ({e}); set it with bin/users.py.")
                settings.pop(e.field)
                continue
            logger.error(f"Env import of {kind} skipped: {e}. Fix the env value; it is imported at the next start.")
            return False


def bootstrap() -> List[str]:
    """Check ``CREDENTIALS_KEY`` and import the first admin's integrations.
    Run at startup after ``users.bootstrap()``. Returns the kinds imported now.

    Raises:
        CredentialsKeyError: credentials are stored, or waiting in env to be
            imported, and the key is missing, malformed or wrong (the message
            says what to do).
    """
    admin = users.first_admin()
    todo = pending(admin) if admin else {}
    store.verify_key(pending=any(v.credentials for v in todo.values()))
    if admin is None:
        return []
    imported: List[str] = []
    for kind, values in todo.items():
        if not store.is_connected(admin, kind):
            if not _connect(admin, kind, values):
                continue  # not recorded: imported at the first start whose env values are valid
            imported.append(kind)
        store.mark_imported(admin, kind)
    if imported:
        logger.info(f"Integrations imported for {admin.name}: {', '.join(imported)}")
    return imported
