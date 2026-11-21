"""Writing user rows on an open connection, and the first user's values from env.

Shared by :mod:`core.users` (``create``, ``bootstrap``) and by migrations:
``m0003_user_scope`` may have to create the first user to own the
conversations, learned phrases and usage rows already in the database, and
``m0016_user_profile`` gives the first admin their prompt profile. It imports
nothing from ``core.db`` (the migrations package is loaded by ``core.db``
itself).

``OWNER_NAME``, ``OWNER_CONTEXT``, ``OWNER_PRONOUN``, ``OWNER_OTHER_NAMES``,
``ALLOWED_TELEGRAM_USER_IDS`` and ``TELEGRAM_CHAT_ID`` are read only here, when the first user is created or
migrated; afterwards the user row is what counts (``bin/dana users update``).
"""

import datetime
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

from core import access

logger = logging.getLogger(__name__)

DEFAULT_FIRST_USER_NAME = "Owner"
# How prompts name the first user when OWNER_PROMPT_NAME is not set.
FIRST_USER_PROMPT_NAME = "the owner"

PRONOUNS = ("he", "she", "they")


@dataclass(frozen=True)
class FirstUser:
    """What the first user is created with (read from env once)."""
    name: str
    telegram_ids: Tuple[int, ...]
    telegram_chat_id: Optional[int]


@dataclass(frozen=True)
class Profile:
    """What prompts know about a user (columns of ``users`` from ``m0016_user_profile``)."""
    context: str = ""
    pronoun: str = "they"
    other_names: str = ""
    prompt_name: str = ""


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="microseconds")


def env_telegram_ids() -> Tuple[int, ...]:
    """``ALLOWED_TELEGRAM_USER_IDS`` (non-numeric entries skipped)."""
    raw = os.getenv("ALLOWED_TELEGRAM_USER_IDS", "")
    return tuple(int(u.strip()) for u in raw.split(",") if u.strip().isdigit())


def _env_chat_id() -> Optional[int]:
    raw = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("TELEGRAM_CHAT_ID is not a numeric chat id; the first user gets no proactive Telegram chat.")
        return None


def first_user_from_env() -> FirstUser:
    """Named ``OWNER_NAME`` (default :data:`DEFAULT_FIRST_USER_NAME`), Telegram
    ids from ``ALLOWED_TELEGRAM_USER_IDS``, proactive chat ``TELEGRAM_CHAT_ID``."""
    name = (os.getenv("OWNER_NAME") or "").strip() or DEFAULT_FIRST_USER_NAME
    return FirstUser(name=name, telegram_ids=env_telegram_ids(), telegram_chat_id=_env_chat_id())


def first_user_profile() -> Profile:
    """The first admin's prompt profile from env: ``OWNER_CONTEXT`` (a few
    sentences about them and the people around them), ``OWNER_PRONOUN``
    (he / she / they, default they) and ``OWNER_OTHER_NAMES`` (their first
    name in another script, e.g. Hebrew, for transcripts)."""
    pronoun = (os.getenv("OWNER_PRONOUN") or "").strip().lower() or "they"
    if pronoun not in PRONOUNS:
        logger.warning(f"OWNER_PRONOUN={pronoun!r} is not one of {', '.join(PRONOUNS)}; using 'they'.")
        pronoun = "they"
    return Profile(context=(os.getenv("OWNER_CONTEXT") or "").strip(), pronoun=pronoun,
                   other_names=(os.getenv("OWNER_OTHER_NAMES") or "").strip(), prompt_name=FIRST_USER_PROMPT_NAME)


def check_pronoun(pronoun: str) -> str:
    """``pronoun`` when it is one of :data:`PRONOUNS`.

    Raises:
        ValueError: anything else.
    """
    if pronoun not in PRONOUNS:
        raise ValueError(f"Pronoun must be one of {', '.join(PRONOUNS)} (not {pronoun!r}).")
    return pronoun


def write_profile(conn: sqlite3.Connection, user_id: str, profile: Profile) -> None:
    """Store ``profile`` on ``user_id``'s row on ``conn`` (no commit).

    Raises:
        ValueError: an unknown pronoun.
    """
    conn.execute("UPDATE users SET context = ?, pronoun = ?, other_names = ?, prompt_name = ? WHERE id = ?",
                 (profile.context.strip(), check_pronoun(profile.pronoun), profile.other_names.strip(),
                  profile.prompt_name.strip(), user_id))


def insert_user(conn: sqlite3.Connection, name: str, access_level: str, telegram_ids: Iterable[int] = (),
                telegram_chat_id: Optional[int] = None) -> str:
    """Insert a user and their Telegram ids on ``conn`` (no commit). Returns the new id.

    Raises:
        ValueError: unknown access level.
        sqlite3.IntegrityError: a Telegram id already belongs to someone.
    """
    access.level(access_level)
    user_id = uuid.uuid4().hex[:12]
    now = now_iso()
    conn.execute("INSERT INTO users (id, name, access, telegram_chat_id, created_at) VALUES (?, ?, ?, ?, ?)",
                 (user_id, name, access_level, telegram_chat_id, now))
    for tid in dict.fromkeys(int(t) for t in telegram_ids):
        conn.execute("INSERT INTO user_telegram (telegram_user_id, user_id, created_at) VALUES (?, ?, ?)",
                     (tid, user_id, now))
    return user_id


def insert_first_user(conn: sqlite3.Connection) -> str:
    """Insert the first user (admin) from env on ``conn`` (no commit). Returns the id."""
    spec = first_user_from_env()
    return insert_user(conn, spec.name, access.ADMIN, spec.telegram_ids, spec.telegram_chat_id)


def owner_of_existing_rows(conn: sqlite3.Connection, tables: Iterable[str]) -> Optional[str]:
    """For a migration that gives existing rows an owner: the first admin's
    id; when there is no user yet and one of ``tables`` has rows, the first
    user is created from env here (as ``users.bootstrap()`` would); None when
    there is no user and nothing to own.

    Raises:
        RuntimeError: users exist but none is an admin (nobody to give the rows to).
    """
    row = conn.execute("SELECT id FROM users WHERE access = ? ORDER BY created_at, rowid LIMIT 1",
                       (access.ADMIN,)).fetchone()
    if row:
        return row[0]
    if not any(conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone() for t in tables):
        return None
    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        raise RuntimeError("Users exist but none is an admin: cannot choose who owns the existing rows.")
    return insert_first_user(conn)
