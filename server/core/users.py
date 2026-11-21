"""Users and their identities (docs/access.md, "User").

A user is a person Dana serves: a name, an access level (core/access.py),
what prompts know about them (``context``, ``pronoun``, ``other_names``,
``prompt_name``: core/user_profile.py), Telegram user ids and API tokens that
resolve to them, and the Telegram chat proactive messages go to. The person
who runs the server is the first user, created at startup with access
``admin`` (:func:`bootstrap`). ``bin/users.py`` manages users on the server.

Tokens are stored hashed: the SHA-256 of the token (Bearer lookups) and the
SHA-256 of the web session cookie made from it (cookie lookups). A raw token
is returned once, by :func:`issue_token`, and never stored. ``API_TOKEN``
(env) is always a valid token of the first admin user and is not stored.

Lookups run on every request and every Telegram update, so they read an
in-memory copy of the tables. Changes made through this module refresh it at
once; changes made by another process are picked up within
:data:`CACHE_TTL_SECONDS`.

**The current user.** Work done for a user runs with that user bound to a
context variable: the web auth middleware binds the request's user, the
Telegram middleware the sender's, :meth:`core.runs.RunManager.start` the run's
(its task copies the context), and background entry points bind the user they
were given with :func:`acting_as`. Deep code (run events, undo entries, usage
records, tool execution) reads it with :func:`current` instead of taking a
parameter.
"""

import contextlib
import contextvars
import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from core import access, user_rows
from core.db import get_db_connection

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 10.0
# last_used_at is written at most this often per token.
TOUCH_INTERVAL_SECONDS = 3600.0
DEFAULT_FIRST_USER_NAME = user_rows.DEFAULT_FIRST_USER_NAME
# The web session cookie is an HMAC of the token with this constant; changing
# it signs everyone out.
_SESSION_KEY = b"ganam-web-session"


@dataclass(frozen=True)
class User:
    id: str
    name: str
    access: str
    telegram_chat_id: Optional[int]
    created_at: str
    telegram_ids: Tuple[int, ...] = ()
    # What prompts know about them (core/user_profile.py).
    context: str = ""
    pronoun: str = "they"
    other_names: str = ""
    prompt_name: str = ""

    @property
    def level(self) -> access.AccessLevel:
        return access.level(self.access)

    @property
    def is_admin(self) -> bool:
        return self.access == access.ADMIN


@dataclass(frozen=True)
class Token:
    """A stored API token (never the token itself)."""
    id: str
    user_id: str
    label: str
    created_at: str
    last_used_at: Optional[str]


@dataclass
class _Snapshot:
    loaded_at: float
    users: Dict[str, User]
    first_admin_id: Optional[str]
    token_by_hash: Dict[str, Tuple[str, str]]  # token_hash -> (token id, user id)
    token_by_session: Dict[str, Tuple[str, str]]  # session_hash -> (token id, user id)
    by_telegram: Dict[int, str]  # telegram user id -> user id


_snapshot: Optional[_Snapshot] = None
_touched: Dict[str, float] = {}


# --- Hashing ----------------------------------------------------------------------

def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def session_cookie_value(token: str) -> str:
    """The web session cookie for ``token`` (an HMAC, never the token)."""
    return hmac.new(token.encode(), _SESSION_KEY, hashlib.sha256).hexdigest()


def env_token() -> str:
    """``API_TOKEN`` from the environment ("" when unset: local development)."""
    return os.getenv("API_TOKEN", "").strip()


_now_iso = user_rows.now_iso


# --- Cache ------------------------------------------------------------------------

def _load() -> _Snapshot:
    with get_db_connection() as conn:
        tg_rows = conn.execute("SELECT telegram_user_id, user_id FROM user_telegram ORDER BY telegram_user_id").fetchall()
        tg_by_user: Dict[str, List[int]] = {}
        for r in tg_rows:
            tg_by_user.setdefault(r["user_id"], []).append(int(r["telegram_user_id"]))
        users: Dict[str, User] = {}
        first_admin_id: Optional[str] = None
        for r in conn.execute("SELECT * FROM users ORDER BY created_at, rowid").fetchall():
            users[r["id"]] = User(id=r["id"], name=r["name"], access=r["access"],
                                  telegram_chat_id=r["telegram_chat_id"], created_at=r["created_at"],
                                  telegram_ids=tuple(tg_by_user.get(r["id"], ())), context=r["context"],
                                  pronoun=r["pronoun"], other_names=r["other_names"],
                                  prompt_name=r["prompt_name"])
            if first_admin_id is None and r["access"] == access.ADMIN:
                first_admin_id = r["id"]
        tokens = conn.execute("SELECT id, user_id, token_hash, session_hash FROM user_tokens").fetchall()
    return _Snapshot(
        loaded_at=time.monotonic(),
        users=users,
        first_admin_id=first_admin_id,
        token_by_hash={t["token_hash"]: (t["id"], t["user_id"]) for t in tokens},
        token_by_session={t["session_hash"]: (t["id"], t["user_id"]) for t in tokens},
        by_telegram={int(r["telegram_user_id"]): r["user_id"] for r in tg_rows},
    )


def _current() -> _Snapshot:
    global _snapshot
    snap = _snapshot
    if snap is None or time.monotonic() - snap.loaded_at > CACHE_TTL_SECONDS:
        snap = _snapshot = _load()
    return snap


def invalidate() -> None:
    """Drop the in-memory copy; the next lookup reads the tables again."""
    global _snapshot
    _snapshot = None


def _touch(token_id: str) -> None:
    """Record that a token was used (at most once per TOUCH_INTERVAL_SECONDS). Never raises."""
    now = time.monotonic()
    if now - _touched.get(token_id, -TOUCH_INTERVAL_SECONDS) < TOUCH_INTERVAL_SECONDS:
        return
    _touched[token_id] = now
    try:
        with get_db_connection() as conn:
            conn.execute("UPDATE user_tokens SET last_used_at = ? WHERE id = ?", (_now_iso(), token_id))
            conn.commit()
    except Exception as e:
        logger.warning(f"Could not record token use: {e}")


# --- Lookups ----------------------------------------------------------------------

def get(user_id: str) -> Optional[User]:
    return _current().users.get(user_id)


def list_users() -> List[User]:
    """Every user, oldest first."""
    return list(_current().users.values())


def first_admin() -> Optional[User]:
    """The oldest admin user: the person who runs the server (None before :func:`bootstrap`)."""
    snap = _current()
    return snap.users.get(snap.first_admin_id) if snap.first_admin_id else None


def by_token(token: str) -> Optional[User]:
    """The user an API token belongs to: ``API_TOKEN`` is the first admin's."""
    token = (token or "").strip()
    if not token:
        return None
    env = env_token()
    if env and hmac.compare_digest(token, env):
        return first_admin()
    hit = _current().token_by_hash.get(_hash(token))
    return _resolve_token(hit)


def by_session_cookie(value: str) -> Optional[User]:
    """The user a web session cookie was made for (see :func:`session_cookie_value`)."""
    value = (value or "").strip()
    if not value:
        return None
    env = env_token()
    if env and hmac.compare_digest(value, session_cookie_value(env)):
        return first_admin()
    hit = _current().token_by_session.get(_hash(value))
    return _resolve_token(hit)


def _resolve_token(hit: Optional[Tuple[str, str]]) -> Optional[User]:
    if not hit:
        return None
    token_id, user_id = hit
    user = get(user_id)
    if user:
        _touch(token_id)
    return user


def telegram_open() -> bool:
    """No user has a Telegram id: the bot lets anyone in, as the first admin
    (what an empty ``ALLOWED_TELEGRAM_USER_IDS`` always meant)."""
    return not _current().by_telegram


def by_telegram_id(telegram_user_id: int) -> Optional[User]:
    """The user a Telegram user id resolves to; the first admin for anyone
    while :func:`telegram_open`; None for an unknown id otherwise."""
    snap = _current()
    if not snap.by_telegram:
        return first_admin()
    user_id = snap.by_telegram.get(int(telegram_user_id))
    return snap.users.get(user_id) if user_id else None


def list_tokens(user_id: str) -> List[Token]:
    with get_db_connection() as conn:
        rows = conn.execute("SELECT id, user_id, label, created_at, last_used_at FROM user_tokens "
                            "WHERE user_id = ? ORDER BY created_at, rowid", (user_id,)).fetchall()
    return [Token(id=r["id"], user_id=r["user_id"], label=r["label"], created_at=r["created_at"],
                  last_used_at=r["last_used_at"]) for r in rows]


# --- The current user ---------------------------------------------------------------

_current_user: contextvars.ContextVar[Optional[User]] = contextvars.ContextVar("ganam_current_user", default=None)


def current() -> Optional[User]:
    """The user the work running now is for (None outside any user's work)."""
    return _current_user.get()


def current_id() -> Optional[str]:
    user = _current_user.get()
    return user.id if user else None


@contextlib.contextmanager
def acting_as(user: Optional[User]) -> Iterator[Optional[User]]:
    """Run the block (and tasks created inside it) as ``user``."""
    token = _current_user.set(user)
    try:
        yield user
    finally:
        _current_user.reset(token)


# --- Changes ----------------------------------------------------------------------

def create(name: str, access_level: str = access.MEMBER, *, telegram_ids: Iterable[int] = (),
           telegram_chat_id: Optional[int] = None, profile: Optional[user_rows.Profile] = None) -> User:
    """Add a user (``profile``: what prompts know about them; default none, "they").

    Raises:
        ValueError: empty name, unknown access level or pronoun, or a
            Telegram id that already belongs to someone.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("A user needs a name.")
    try:
        with get_db_connection() as conn:
            user_id = user_rows.insert_user(conn, name, access_level, telegram_ids, telegram_chat_id)
            user_rows.write_profile(conn, user_id, profile or user_rows.Profile())
            conn.commit()
    except sqlite3.IntegrityError as e:
        raise ValueError(f"Could not add {name!r}: a Telegram id already belongs to another user ({e}).") from e
    finally:
        invalidate()
    user = get(user_id)
    assert user is not None
    return user


_UNSET: object = object()


def update(user_id: str, *, name: Optional[str] = None, access_level: Optional[str] = None,
           context: Optional[str] = None, pronoun: Optional[str] = None, other_names: Optional[str] = None,
           prompt_name: Optional[str] = None, telegram_chat_id: object = _UNSET) -> User:
    """Change a user; arguments left out stay as they are
    (``telegram_chat_id=None`` removes the proactive chat).

    Raises:
        LookupError: no such user.
        ValueError: an empty name, an unknown access level or pronoun, or
            taking ``admin`` away from the last admin.
    """
    user = get(user_id)
    if user is None:
        raise LookupError(f"No user {user_id!r}")
    if name is not None and not name.strip():
        raise ValueError("A user needs a name.")
    if access_level is not None:
        access.level(access_level)
        if user.is_admin and access_level != access.ADMIN and _admin_count() <= 1:
            raise ValueError(f"{user.name} is the last admin; make someone else admin first.")
    profile = user_rows.Profile(
        context=user.context if context is None else context,
        pronoun=user.pronoun if pronoun is None else pronoun,
        other_names=user.other_names if other_names is None else other_names,
        prompt_name=user.prompt_name if prompt_name is None else prompt_name)
    chat = user.telegram_chat_id if telegram_chat_id is _UNSET else telegram_chat_id
    try:
        with get_db_connection() as conn:
            conn.execute("UPDATE users SET name = ?, access = ?, telegram_chat_id = ? WHERE id = ?",
                         ((name or user.name).strip(), access_level or user.access,
                          None if chat is None else int(chat), user_id))  # type: ignore[call-overload]
            user_rows.write_profile(conn, user_id, profile)
            conn.commit()
    finally:
        invalidate()
    updated = get(user_id)
    assert updated is not None
    return updated


def _admin_count() -> int:
    return sum(1 for u in list_users() if u.is_admin)


def remove(user_id: str) -> None:
    """Delete a user and, through the database's cascades, everything they
    own (sessions, addons, tasks, calls, … ; ``bin/users.py remove``
    says what first). Usage rows are kept (server cost history).

    Raises:
        LookupError: no such user.
        ValueError: they are the last admin.
    """
    user = get(user_id)
    if user is None:
        raise LookupError(f"No user {user_id!r}")
    if user.is_admin and _admin_count() <= 1:
        raise ValueError(f"{user.name} is the last admin and cannot be removed.")
    try:
        with get_db_connection() as conn:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
    finally:
        invalidate()


def add_telegram_id(user_id: str, telegram_user_id: int) -> None:
    """Let a Telegram user id resolve to ``user_id``.

    Raises:
        LookupError: no such user.
        ValueError: the id already belongs to a user.
    """
    if get(user_id) is None:
        raise LookupError(f"No user {user_id!r}")
    try:
        with get_db_connection() as conn:
            conn.execute("INSERT INTO user_telegram (telegram_user_id, user_id, created_at) VALUES (?, ?, ?)",
                         (int(telegram_user_id), user_id, _now_iso()))
            conn.commit()
    except sqlite3.IntegrityError as e:
        raise ValueError(f"Telegram id {telegram_user_id} already belongs to a user.") from e
    finally:
        invalidate()


def move_telegram_id(telegram_user_id: int, user_id: str) -> None:
    """Let a Telegram user id that belongs to someone resolve to ``user_id`` instead.

    Raises:
        LookupError: no such user, or nobody has the id.
    """
    if get(user_id) is None:
        raise LookupError(f"No user {user_id!r}")
    try:
        with get_db_connection() as conn:
            cur = conn.execute("UPDATE user_telegram SET user_id = ? WHERE telegram_user_id = ?",
                               (user_id, int(telegram_user_id)))
            conn.commit()
    finally:
        invalidate()
    if not cur.rowcount:
        raise LookupError(f"No user has Telegram id {telegram_user_id}.")


def remove_telegram_id(telegram_user_id: int) -> bool:
    """Forget a Telegram user id (it no longer resolves to anyone). Returns
    False when nobody had it. Removing the last one opens the bot to
    everyone (see :func:`telegram_open`)."""
    try:
        with get_db_connection() as conn:
            cur = conn.execute("DELETE FROM user_telegram WHERE telegram_user_id = ?", (int(telegram_user_id),))
            conn.commit()
    finally:
        invalidate()
    return cur.rowcount > 0


def issue_token(user_id: str, label: str = "") -> Tuple[str, Token]:
    """A new API token for ``user_id`` (one per device; ``label`` names it).
    Returns ``(token, stored)``: the raw token is shown once and never stored.

    Raises:
        LookupError: no such user.
    """
    if get(user_id) is None:
        raise LookupError(f"No user {user_id!r}")
    raw = secrets.token_urlsafe(32)
    token = Token(id=uuid.uuid4().hex[:12], user_id=user_id, label=(label or "").strip(), created_at=_now_iso(),
                  last_used_at=None)
    with get_db_connection() as conn:
        conn.execute("INSERT INTO user_tokens (id, user_id, label, token_hash, session_hash, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     (token.id, user_id, token.label, _hash(raw), _hash(session_cookie_value(raw)), token.created_at))
        conn.commit()
    invalidate()
    return raw, token


def revoke_token(token_id: str) -> bool:
    """Delete a stored token (its Bearer header and web logins stop working).
    Returns False when there was no such token."""
    with get_db_connection() as conn:
        cur = conn.execute("DELETE FROM user_tokens WHERE id = ?", (token_id,))
        conn.commit()
    invalidate()
    _touched.pop(token_id, None)
    return cur.rowcount > 0


# --- Startup ----------------------------------------------------------------------

def bootstrap() -> Optional[User]:
    """Create the first user when there is none: named ``OWNER_NAME``, access
    ``admin``, Telegram ids from ``ALLOWED_TELEGRAM_USER_IDS``, proactive chat
    ``TELEGRAM_CHAT_ID``, context ``OWNER_CONTEXT`` (with today's default and
    wording: :func:`core.user_rows.first_user_profile`). Those env values are
    read only then (migrations ``m0003_user_scope`` / ``m0016_user_profile``
    create the same user when the database already had data to give it).
    Returns the user when it was created, None when users already existed.
    Logs who may use the Telegram bot."""
    created: Optional[User] = None
    with get_db_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if not count:
        spec = user_rows.first_user_from_env()
        created = create(spec.name, access.ADMIN, telegram_ids=spec.telegram_ids,
                         telegram_chat_id=spec.telegram_chat_id, profile=user_rows.first_user_profile())
        logger.info(f"Created the first user {created.name!r} (admin, id {created.id})")
    snap = _current()
    unknown = [t for t in user_rows.env_telegram_ids() if t not in snap.by_telegram]
    if unknown and not created:
        logger.warning(f"ALLOWED_TELEGRAM_USER_IDS lists ids no user has ({unknown}); they are not let in. "
                       "The env value is read only when the first user is created.")
    if telegram_open() and os.getenv("TELEGRAM_BOT_TOKEN"):
        logger.warning("No Telegram user ids are known: the Telegram bot is PUBLIC "
                       "(anyone can use it, as the first admin user)!")
    else:
        logger.info(f"Telegram bot restricted to user IDs: {sorted(snap.by_telegram)}")
    return created
