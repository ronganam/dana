"""A user's integrations: connect, update, disconnect, look up (table ``integrations``).

One row per (user, kind). Credentials are stored only encrypted
(:mod:`integrations.framework.crypto`), settings as plain JSON. Kinds declared
with ``everyone=True`` (``core``) have no row: every user has them.

Lookups happen on every tool list and tool call, so they read an in-memory
copy of the table: changes made here refresh it at once, changes made by
another process are picked up within :data:`CACHE_TTL_SECONDS`. Decrypted
credentials are cached with it.
"""

import contextlib
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Set, Tuple

from core import user_rows, users
from core.db import get_db_connection
from core.users import User
from integrations.framework import addresses, crypto
from integrations.framework.kind import (ADDRESS, NAME, TELEGRAM_CHAT, Integration, InvalidValue,
                                         check_values, get_kind, kinds)

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 10.0

# Who may set any address or any Telegram chat, and give anyone a kind with
# ``change_requires`` (docs/access.md, "Security rules").
ADMIN_CAPABILITY = "manage_users"
# Extra origins a user without ADMIN_CAPABILITY may use, per kind:
# ``homeassistant=https://ha.example.com,whatsapp=http://waha.waha:3000``
# (docs/configuration.md). The origins of every admin's own integration of the
# kind are always allowed.
MEMBER_ORIGINS_ENV = "MEMBER_ALLOWED_ORIGINS"
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class Forbidden(PermissionError):
    """The user making a change lacks a capability (``needs``): the API
    answers 403 ``{"error": "forbidden", "needs": needs}``."""

    def __init__(self, needs: str, message: str) -> None:
        super().__init__(message)
        self.needs = needs


class NotConnected(Exception):
    """The user has not connected the kind (``kind``). Not a LookupError on
    purpose: handlers map those to 404, and this is not "not found". Web
    handlers let it through: the middleware in core/auth.py answers it with
    403 ``{"error": "not_connected", "integration": kind}``."""

    def __init__(self, user: User, kind: str) -> None:
        super().__init__(f"The {kind} integration is not connected for {user.name}.")
        self.kind = kind


@dataclass(frozen=True, eq=False)
class Connection:
    """What a tool gets as ``conn``: the user, the kind, the decrypted
    credentials and the settings (declared defaults filled in)."""
    user: User
    kind: str
    credentials: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    settings: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    account: Optional[str] = None
    # The account the service reported while another user's connection held
    # it (:func:`claim_account`); the connection then has no ``account``.
    account_conflict: Optional[str] = None


@dataclass(frozen=True)
class IntegrationInfo:
    """A connected integration as listed for its user (never the credentials)."""
    kind: str
    settings: Mapping[str, Any]
    account: Optional[str]
    has_credentials: bool
    created_at: Optional[str]
    updated_at: Optional[str]
    account_conflict: Optional[str] = None


@dataclass(frozen=True)
class _Row:
    credentials: Optional[str]  # Fernet token
    settings: str
    account: Optional[str]
    created_at: str
    updated_at: str
    account_conflict: Optional[str] = None


@dataclass
class _Snapshot:
    loaded_at: float
    rows: Dict[str, Dict[str, _Row]]  # user id -> kind -> row
    plain: Dict[Tuple[str, str], Tuple[Mapping[str, str], Mapping[str, Any]]] = field(default_factory=dict)


_snapshot: Optional[_Snapshot] = None


def _load() -> _Snapshot:
    rows: Dict[str, Dict[str, _Row]] = {}
    with get_db_connection() as db:
        for r in db.execute("SELECT * FROM integrations ORDER BY user_id, kind"):
            rows.setdefault(r["user_id"], {})[r["kind"]] = _Row(
                credentials=r["credentials"], settings=r["settings"], account=r["account"],
                created_at=r["created_at"], updated_at=r["updated_at"], account_conflict=r["account_conflict"])
    return _Snapshot(loaded_at=time.monotonic(), rows=rows)


def _current() -> _Snapshot:
    global _snapshot
    snap = _snapshot
    if snap is None or time.monotonic() - snap.loaded_at > CACHE_TTL_SECONDS:
        snap = _snapshot = _load()
    return snap


def invalidate() -> None:
    """Drop the in-memory copy (and the decrypted credentials)."""
    global _snapshot
    _snapshot = None


# --- Lookups ----------------------------------------------------------------------

def connected_kinds(user: User) -> Tuple[str, ...]:
    """The kinds ``user`` has, in the fixed kind order (``everyone`` kinds included)."""
    mine = _current().rows.get(user.id, {})
    return tuple(k.kind for k in kinds() if k.everyone or k.kind in mine)


def is_connected(user: User, kind: str) -> bool:
    return kind in connected_kinds(user)


def get(user: User, kind: str) -> Optional[Connection]:
    """``user``'s connection of ``kind`` (None when not connected).

    Raises:
        KeyError: no such kind.
        CredentialsKeyError: its credentials cannot be decrypted.
    """
    integration = get_kind(kind)
    if integration.everyone:
        return Connection(user=user, kind=kind, settings=MappingProxyType(integration.setting_defaults()))
    snap = _current()
    row = snap.rows.get(user.id, {}).get(kind)
    if row is None:
        return None
    plain = snap.plain.get((user.id, kind))
    if plain is None:
        creds = crypto.decrypt(row.credentials) if row.credentials else {}
        settings = {**integration.setting_defaults(), **json.loads(row.settings or "{}")}
        plain = (MappingProxyType(creds), MappingProxyType(settings))
        snap.plain[(user.id, kind)] = plain
    return Connection(user=user, kind=kind, credentials=plain[0], settings=plain[1], account=row.account,
                      account_conflict=row.account_conflict)


def require(user: User, kind: str) -> Connection:
    """Like :func:`get`, for code that cannot go on without the kind.

    Raises:
        NotConnected: ``user`` has not connected ``kind``.
    """
    conn = get(user, kind)
    if conn is None:
        raise NotConnected(user, kind)
    return conn


def list_integrations(user: User) -> List[IntegrationInfo]:
    """``user``'s integrations in the fixed kind order (``everyone`` kinds included)."""
    mine = _current().rows.get(user.id, {})
    out: List[IntegrationInfo] = []
    for k in kinds():
        row = mine.get(k.kind)
        if k.everyone:
            out.append(IntegrationInfo(k.kind, MappingProxyType(k.setting_defaults()), None, False, None, None))
        elif row is not None:
            settings = {**k.setting_defaults(), **json.loads(row.settings or "{}")}
            out.append(IntegrationInfo(k.kind, MappingProxyType(settings), row.account, bool(row.credentials),
                                       row.created_at, row.updated_at, row.account_conflict))
    return out


def users_with(kind: str) -> List[str]:
    """Ids of the users who have ``kind`` connected (not for ``everyone`` kinds)."""
    return sorted(uid for uid, mine in _current().rows.items() if kind in mine)


# --- Who may set what (docs/access.md, "Security rules") ------------------------------

def admin_change(by: Optional[User]) -> bool:
    """``by`` may set any address / chat: None (the server itself, an operator
    with shell access: bin/users.py, the env import) or a user with
    :data:`ADMIN_CAPABILITY`."""
    return by is None or by.level.allows(ADMIN_CAPABILITY)


def _env_origins(kind: str) -> Set[str]:
    out: Set[str] = set()
    for entry in (os.getenv(MEMBER_ORIGINS_ENV) or "").split(","):
        name, sep, value = entry.strip().partition("=")
        if not sep or name.strip() != kind:
            continue
        try:
            out.add(addresses.origin(value.strip()))
        except ValueError as e:
            logger.warning(f"{MEMBER_ORIGINS_ENV}: {value.strip()!r} for {kind} skipped ({e})")
    return out


def allowed_origins(kind: str, field_name: str) -> Set[str]:
    """Origins a user without :data:`ADMIN_CAPABILITY` may put in address
    field ``field_name`` of ``kind``: those of every admin's own integration
    of the kind, plus :data:`MEMBER_ORIGINS_ENV`."""
    out = _env_origins(kind)
    for admin in users.list_users():
        if not admin.level.allows(ADMIN_CAPABILITY):
            continue
        conn = get(admin, kind)
        if conn is None:
            continue
        value = conn.credentials.get(field_name) or conn.settings.get(field_name)
        try:
            out.add(addresses.origin(str(value or "")))
        except ValueError:
            pass
    return out


# Why a field or a kind cannot be changed by the user looking at it (the web page shows it read-only).
SET_BY_ADMIN = "Set by an admin"


@dataclass(frozen=True)
class ChangeRule:
    """Whether a user may change a kind or one of its fields for a connection,
    known before any value is typed: ``can_change``; when not, ``why_not`` (a
    short phrase); ``rule``: one line on what a value may be, or None.
    :func:`field_rule` / :func:`kind_rule` compute it from the same checks a
    change goes through (:func:`_checked_value`, :func:`_changeable`), so what
    a client is told and what the store refuses cannot drift apart."""
    can_change: bool
    why_not: Optional[str] = None
    rule: Optional[str] = None


def kind_rule(integration: Integration, by: Optional[User]) -> ChangeRule:
    """Whether ``by`` may connect / update / disconnect ``integration`` (its ``change_requires``)."""
    return ChangeRule(True) if integration.changeable_by(by) else ChangeRule(False, SET_BY_ADMIN)


def field_rule(integration: Integration, user: User, name: str, by: Optional[User]) -> ChangeRule:
    """Whether ``by`` may set field ``name`` of ``user``'s connection of
    ``integration`` at all (some values may still be refused: an address on
    an origin nobody allowed, a WAHA session another user has).

    Raises:
        KeyError: the kind has no field ``name``.
    """
    f = integration.field(name)
    if f is None:
        raise KeyError(f"The {integration.kind} integration has no field {name!r}")
    if not integration.changeable_by(by):
        return ChangeRule(False, SET_BY_ADMIN)
    admin = admin_change(by)
    if f.type == ADDRESS:
        if admin:
            return ChangeRule(True, rule="An http:// or https:// address, without a user name, ? or #.")
        if not allowed_origins(integration.kind, name):
            return ChangeRule(False, SET_BY_ADMIN)
        return ChangeRule(True, rule="Only an address an admin has allowed for this service.")
    if f.type == TELEGRAM_CHAT:
        if admin:
            return ChangeRule(True, rule="A Telegram chat id: a person's own id, or a group's (a negative number).")
        if not user.telegram_ids:
            return ChangeRule(False, SET_BY_ADMIN)
        return ChangeRule(True, rule="Only your own Telegram id (your private chat with the bot).")
    if f.type == NAME:
        return ChangeRule(True, rule="Letters, digits, _, - and . only.")
    return ChangeRule(True)


def _telegram_chat(name: str, value: Any) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError
        return int(str(value).strip())
    except ValueError:
        raise InvalidValue(name, f"{name} must be a numeric Telegram chat id.") from None


def _checked_value(integration: Integration, user: User, name: str, value: Any, by: Optional[User]) -> Any:
    """``value`` of field ``name`` as it is stored (normalised), or InvalidValue."""
    f = integration.field(name)
    if f is None or value is None or (isinstance(value, str) and not value.strip()):
        return value
    lock = field_rule(integration, user, name, by)  # what clients are told in advance (the integration payload)
    if not lock.can_change:
        raise InvalidValue(name, f"{name}: {lock.why_not}; ask an admin to change it.")
    if f.type == ADDRESS:
        try:
            normal = addresses.normalize(str(value))
        except ValueError as e:
            raise InvalidValue(name, f"{name}: {e}") from None
        if not admin_change(by) and addresses.origin(normal) not in allowed_origins(integration.kind, name):
            raise InvalidValue(name, f"{name}: {addresses.origin(normal)} is not an address an admin has allowed "
                                     f"for {integration.kind}; ask an admin to set it for you.")
        return normal
    if f.type == TELEGRAM_CHAT:
        chat = _telegram_chat(name, value)
        if not admin_change(by) and chat not in user.telegram_ids:
            raise InvalidValue(name, f"{name} can only be your own Telegram chat with the bot (your Telegram id); "
                                     "an admin sets group chats.")
        return chat
    if f.type == NAME:
        if not _NAME_RE.match(str(value).strip()):
            raise InvalidValue(name, f"{name} may hold only letters, digits, '_', '-' and '.'.")
        return str(value).strip()
    return value


def _checked(integration: Integration, user: User, values: Optional[Mapping[str, Any]],
             by: Optional[User]) -> Dict[str, Any]:
    return {k: _checked_value(integration, user, k, v, by) for k, v in (values or {}).items()}


def stored_value_problem(conn: Connection) -> Optional[Tuple[str, str]]:
    """``(field, reason)`` when an address / name stored on ``conn`` is not
    valid (a value saved before these rules); None when every one is.
    ``test`` and ``options`` run only on a connection without a problem."""
    integration = get_kind(conn.kind)
    for f in (*integration.credentials, *integration.settings):
        value = conn.credentials.get(f.name) if f in integration.credentials else conn.settings.get(f.name)
        if value is None or value == "" or f.type not in (ADDRESS, NAME):
            continue
        try:
            _checked_value(integration, conn.user, f.name, value, None)
        except InvalidValue as e:
            return f.name, str(e)
    return None


# --- Changes ----------------------------------------------------------------------

def _connectable(user: User, kind: str, by: Optional[User]) -> Integration:
    integration = get_kind(kind)
    if integration.everyone:
        raise ValueError(f"Every user has the {kind} integration; it is not connected or disconnected.")
    if not integration.allowed_for(user):
        raise PermissionError(f"The {kind} integration needs the '{integration.requires}' capability, "
                              f"which access level {user.access!r} does not have.")
    _changeable(integration, by)
    return integration


def _changeable(integration: Integration, by: Optional[User]) -> None:
    if not integration.changeable_by(by):
        raise Forbidden(str(integration.change_requires),
                        f"Only a user with '{integration.change_requires}' can connect or change the "
                        f"{integration.kind} integration.")


def _validate(integration: Integration, user: User, changes: Mapping[str, Any], credentials: Mapping[str, Any],
              settings: Mapping[str, Any], by: Optional[User]) -> None:
    if integration.validate is not None and changes:
        integration.validate(user, changes, credentials, {**integration.setting_defaults(), **settings}, by)


@contextlib.contextmanager
def _write() -> Iterator[sqlite3.Connection]:
    """One write transaction taken at once (``BEGIN IMMEDIATE``): what is read
    inside it cannot change before it commits. Commits on success, rolls back
    on any error."""
    db = get_db_connection()
    db.isolation_level = None
    try:
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
        except BaseException:
            db.execute("ROLLBACK")
            raise
        db.execute("COMMIT")
    finally:
        db.close()


def _encrypt(credentials: Mapping[str, Any]) -> Optional[str]:
    values = {k: str(v) for k, v in credentials.items() if v is not None}
    return crypto.encrypt(values) if values else None


def connect(user: User, kind: str, *, credentials: Optional[Mapping[str, Any]] = None,
            settings: Optional[Mapping[str, Any]] = None, account: Optional[str] = None,
            by: Optional[User] = None) -> Connection:
    """Connect ``kind`` for ``user``. ``by`` is the user making the change
    (None: the server itself or an operator with shell access); it decides
    what may be set (docs/access.md, "Security rules").

    Raises:
        KeyError: no such kind.
        PermissionError: the user's access level does not allow the kind;
            :class:`Forbidden` when ``by`` lacks the kind's ``change_requires``.
        ValueError: already connected, an unknown credential / setting name,
            or a required credential missing; :class:`InvalidValue` for a
            value that is refused (an address, a Telegram chat, the kind's rule).
        CredentialsKeyError: there are credentials to store and no usable key.
    """
    integration = _connectable(user, kind, by)
    credentials = dict(credentials or {})
    check_values(integration, credentials, settings)
    credentials = _checked(integration, user, credentials, by)
    settings = _checked(integration, user, settings, by)
    _validate(integration, user, {**credentials, **settings}, credentials,
              {k: v for k, v in settings.items() if v is not None}, by)
    missing = [f.name for f in integration.credentials if f.required and not credentials.get(f.name)]
    if missing:
        raise ValueError(f"Connecting {kind} needs {', '.join(missing)}.")
    token = _encrypt(credentials)
    now = user_rows.now_iso()
    try:
        with _write() as db:
            db.execute("INSERT INTO integrations (user_id, kind, credentials, settings, account, created_at, "
                       "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (user.id, kind, token, json.dumps(dict(settings or {}), ensure_ascii=False), account,
                        now, now))
    except sqlite3.IntegrityError as e:
        raise ValueError(f"{user.name} already has the {kind} integration ({e}).") from e
    finally:
        invalidate()
    logger.info(f"Connected {kind} for user {user.id}")
    if integration.on_connect is not None:
        try:
            integration.on_connect(user)
        except Exception:
            logger.error(f"on_connect of {kind} failed for user {user.id}", exc_info=True)
    _changed(integration, user)
    return require(user, kind)


def _changed(integration: Integration, user: User) -> None:
    """Run the kind's ``on_change`` after a connect / update / disconnect (a failure is logged)."""
    if integration.on_change is None:
        return
    try:
        integration.on_change(user)
    except Exception:
        logger.error(f"on_change of {integration.kind} failed for user {user.id}", exc_info=True)


def update(user: User, kind: str, *, credentials: Optional[Mapping[str, Any]] = None,
           settings: Optional[Mapping[str, Any]] = None, account: Optional[str] = None,
           by: Optional[User] = None) -> Connection:
    """Change a connected integration: ``credentials`` given are merged into
    the stored ones, ``settings`` given are merged into the stored ones (a
    None value goes back to the default), ``account`` replaces the stored one
    when given. When credentials were given, the kind's
    ``on_credentials_change`` runs afterwards.

    ``by``: who makes the change (see :func:`connect`).

    Raises:
        KeyError: no such kind.
        NotConnected: not connected for ``user``.
        Forbidden: ``by`` lacks the kind's ``change_requires``.
        ValueError: an unknown credential / setting name; InvalidValue for a refused value.
        CredentialsKeyError: no usable key for credentials.
    """
    integration = get_kind(kind)
    _changeable(integration, by)
    current = require(user, kind)
    check_values(integration, credentials, settings)
    credentials = _checked(integration, user, credentials, by) if credentials else credentials
    settings = _checked(integration, user, settings, by) if settings else settings
    merged = dict(current.settings)
    for key, value in (settings or {}).items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    merged_creds = {k: v for k, v in {**current.credentials, **(credentials or {})}.items() if v is not None}
    _validate(integration, user, {**(credentials or {}), **(settings or {})}, merged_creds, merged, by)
    with _write() as db:
        row = db.execute("SELECT credentials, settings, account FROM integrations WHERE user_id = ? AND kind = ?",
                         (user.id, kind)).fetchone()
        if row is None:
            invalidate()
            raise NotConnected(user, kind)
        token = row["credentials"]
        if credentials:
            token = _encrypt({**current.credentials, **credentials})
        stored = json.loads(row["settings"] or "{}")
        for key, value in (settings or {}).items():
            if value is None:
                stored.pop(key, None)
            else:
                stored[key] = value
        db.execute("UPDATE integrations SET credentials = ?, settings = ?, account = ?, updated_at = ? "
                   "WHERE user_id = ? AND kind = ?",
                   (token, json.dumps(stored, ensure_ascii=False), row["account"] if account is None else account,
                    user_rows.now_iso(), user.id, kind))
    invalidate()
    if credentials and integration.on_credentials_change is not None:
        try:
            integration.on_credentials_change(user)
        except Exception:
            logger.error(f"on_credentials_change of {kind} failed for user {user.id}", exc_info=True)
    _changed(integration, user)
    return require(user, kind)


def claim_account(user: User, kind: str, account: str, key: Callable[[str], str]) -> Optional[str]:
    """Store ``account`` as ``user``'s connection of ``kind``'s account unless
    another user's connection of the kind already holds it (the same
    ``key(account)``): an account belongs to at most one connection, the one
    that claimed it first. Checked and written in one write transaction, so
    two claims at the same moment cannot both win. A refused claim leaves the
    connection without an account and records it in ``account_conflict``;
    a successful one clears that.

    Returns:
        None when stored; else the id of the user whose connection holds it.

    Raises:
        NotConnected: not connected for ``user``.
    """
    wanted = key(account)
    try:
        with _write() as db:
            if db.execute("SELECT 1 FROM integrations WHERE user_id = ? AND kind = ?",
                          (user.id, kind)).fetchone() is None:
                raise NotConnected(user, kind)
            holder = next((r["user_id"] for r in db.execute(
                "SELECT user_id, account FROM integrations WHERE kind = ? AND user_id != ? "
                "AND COALESCE(account, '') != '' ORDER BY user_id", (kind, user.id))
                if wanted and key(r["account"]) == wanted), None)
            db.execute("UPDATE integrations SET account = ?, account_conflict = ?, updated_at = ? "
                       "WHERE user_id = ? AND kind = ?",
                       (account if holder is None else None, None if holder is None else account,
                        user_rows.now_iso(), user.id, kind))
    finally:
        invalidate()
    return holder


def clear_account(user: User, kind: str) -> None:
    """Forget ``user``'s connection of ``kind``'s account and any account
    conflict (its credentials changed: the account is learned again)."""
    with get_db_connection() as db:
        db.execute("UPDATE integrations SET account = NULL, account_conflict = NULL WHERE user_id = ? AND kind = ?",
                   (user.id, kind))
        db.commit()
    invalidate()


def disconnect(user: User, kind: str, *, by: Optional[User] = None) -> bool:
    """Remove ``user``'s integration of ``kind`` (credentials included).
    Returns False when it was not connected. ``by``: who makes the change
    (see :func:`connect`).

    Raises:
        KeyError: no such kind.
        ValueError: an ``everyone`` kind.
        Forbidden: ``by`` lacks the kind's ``change_requires``.
    """
    integration = get_kind(kind)
    if integration.everyone:
        raise ValueError(f"Every user has the {kind} integration; it is not connected or disconnected.")
    _changeable(integration, by)
    with get_db_connection() as db:
        cur = db.execute("DELETE FROM integrations WHERE user_id = ? AND kind = ?", (user.id, kind))
        db.commit()
    invalidate()
    if cur.rowcount:
        logger.info(f"Disconnected {kind} for user {user.id}")
        _changed(integration, user)
    return cur.rowcount > 0


# --- Startup ----------------------------------------------------------------------

def stored_credentials() -> int:
    """How many integrations hold encrypted credentials."""
    with get_db_connection() as db:
        return db.execute("SELECT COUNT(*) FROM integrations WHERE credentials IS NOT NULL").fetchone()[0]


def verify_key(*, pending: bool) -> None:
    """Startup check of ``CREDENTIALS_KEY``: needed when credentials are
    stored or ``pending`` (about to be stored); then it must be well formed
    and decrypt every stored credential. A key that is set must be well
    formed even when nothing needs it yet.

    Raises:
        CredentialsKeyError: what is wrong and what to do.
    """
    with get_db_connection() as db:
        tokens = [r[0] for r in db.execute("SELECT credentials FROM integrations WHERE credentials IS NOT NULL")]
    if not tokens and not pending:
        if crypto.key_configured():
            crypto.check_key()
        return
    if not crypto.key_configured():
        why = (f"{len(tokens)} integration(s) have stored credentials" if tokens
               else "integration credentials are waiting to be imported from the environment")
        raise crypto.CredentialsKeyError(f"{crypto.KEY_ENV} is not set but {why}. " + crypto.GENERATE_HINT)
    for token in tokens:
        crypto.decrypt(token)


def imported_kinds() -> Tuple[str, ...]:
    """Kinds the startup env import has already handled (``integration_imports``)."""
    with get_db_connection() as db:
        return tuple(r[0] for r in db.execute("SELECT kind FROM integration_imports ORDER BY kind"))


def import_settings(kind: str) -> Dict[str, Any]:
    """Settings waiting for the env import of ``kind`` (``integration_import_settings``:
    values a migration moved out of older storage before the kind existed for
    the first admin)."""
    with get_db_connection() as db:
        return {r["name"]: json.loads(r["value"])
                for r in db.execute("SELECT name, value FROM integration_import_settings WHERE kind = ? ORDER BY name",
                                    (kind,))}


def mark_imported(user: User, kind: str) -> None:
    """Record ``kind`` as imported for ``user`` (its waiting settings are consumed)."""
    with get_db_connection() as db:
        db.execute("INSERT OR IGNORE INTO integration_imports (kind, user_id, imported_at) VALUES (?, ?, ?)",
                   (kind, user.id, user_rows.now_iso()))
        db.execute("DELETE FROM integration_import_settings WHERE kind = ?", (kind,))
        db.commit()
