"""A user's addons: connect, update, disconnect, look up (table ``connections``).

One row per (user, addon). Credentials are stored only encrypted
(:mod:`core.addons.crypto`), settings as plain JSON. Addons declared
with ``everyone=True`` (``essentials``) have no row: every user has them.

Lookups happen on every tool list and tool call, so they read an in-memory
copy of the table, read again whenever the database changed since the last
lookup (:class:`core.db.ChangeWatch`): a change made here or by another
process (``bin/dana addons connect``) applies to the next lookup. Decrypted
credentials are cached with it, and kept across a reload for rows that did
not change.
"""

import contextlib
import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Set, Tuple

from core import user_rows, users
from core.db import ChangeWatch, get_db_connection
from core.users import User
from core.addons import addresses, crypto, state
from core.addons.addon import (ADDRESS, NAME, TELEGRAM_CHAT, Addon, InvalidValue,
                                         check_values, get_addon, all_addons)

logger = logging.getLogger(__name__)

# Who may set any address or any Telegram chat, and give anyone an addon with
# ``change_requires`` (docs/access.md, "Security rules").
ADMIN_CAPABILITY = "manage_users"
# Extra origins a user without ADMIN_CAPABILITY may use, per addon:
# ``homeassistant=https://ha.example.com,whatsapp=http://waha.waha:3000``
# (docs/configuration.md). The origins of every admin's own connection of the
# addon are always allowed.
MEMBER_ORIGINS_ENV = "MEMBER_ALLOWED_ORIGINS"
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class Forbidden(PermissionError):
    """The user making a change lacks a capability (``needs``): the API
    answers 403 ``{"error": "forbidden", "needs": needs}``."""

    def __init__(self, needs: str, message: str) -> None:
        super().__init__(message)
        self.needs = needs


class NotConnected(Exception):
    """The user has not connected the addon (``name``). Not a LookupError on
    purpose: handlers map those to 404, and this is not "not found". Web
    handlers let it through: the middleware in core/auth.py answers it with
    403 ``{"error": "not_connected", "addon": name}``."""

    def __init__(self, user: User, name: str) -> None:
        super().__init__(f"The {name} addon is not connected for {user.name}.")
        self.addon = name


@dataclass(frozen=True, eq=False)
class Connection:
    """What a tool gets as ``conn``: the user, the addon, the decrypted
    credentials and the settings (declared defaults filled in)."""
    user: User
    addon: str
    credentials: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    settings: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    account: Optional[str] = None
    # The account the service reported while another user's connection held
    # it (:func:`claim_account`); the connection then has no ``account``.
    account_conflict: Optional[str] = None


@dataclass(frozen=True)
class ConnectionInfo:
    """A connected addon as listed for its user (never the credentials)."""
    addon: str
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
    rows: Dict[str, Dict[str, _Row]]  # user id -> addon -> row
    plain: Dict[Tuple[str, str], Tuple[Mapping[str, str], Mapping[str, Any]]] = field(default_factory=dict)


_snapshot: Optional[_Snapshot] = None
_watch = ChangeWatch()


def _load(previous: Optional[_Snapshot]) -> _Snapshot:
    rows: Dict[str, Dict[str, _Row]] = {}
    with get_db_connection() as db:
        for r in db.execute("SELECT * FROM connections ORDER BY user_id, addon"):
            rows.setdefault(r["user_id"], {})[r["addon"]] = _Row(
                credentials=r["credentials"], settings=r["settings"], account=r["account"],
                created_at=r["created_at"], updated_at=r["updated_at"], account_conflict=r["account_conflict"])
    snap = _Snapshot(rows=rows)
    if previous is not None:  # decrypted values of the rows that did not change
        snap.plain.update({key: plain for key, plain in previous.plain.items()
                           if previous.rows.get(key[0], {}).get(key[1]) == rows.get(key[0], {}).get(key[1])})
    return snap


def _current() -> _Snapshot:
    global _snapshot
    snap = _snapshot
    if _watch.changed() or snap is None:
        snap = _snapshot = _load(snap)
    return snap


def invalidate() -> None:
    """Drop the in-memory copy (and the decrypted credentials)."""
    global _snapshot
    _snapshot = None


# --- Lookups ----------------------------------------------------------------------

def connected_addons(user: User) -> Tuple[str, ...]:
    """The addons ``user`` has, in the fixed addon order (``everyone`` addons
    included; addons disabled on the server left out: core/addons/state.py)."""
    mine = _current().rows.get(user.id, {})
    off = state.disabled()
    return tuple(k.name for k in all_addons() if (k.everyone or k.name in mine) and k.name not in off)


def is_connected(user: User, name: str) -> bool:
    return name in connected_addons(user)


def get(user: User, name: str, *, disabled_too: bool = False) -> Optional[Connection]:
    """``user``'s connection of ``name`` (None when not connected, or when the
    addon is disabled on the server unless ``disabled_too``: the Addons page
    still shows a disabled addon's stored settings).

    Raises:
        KeyError: no such addon.
        CredentialsKeyError: its credentials cannot be decrypted.
    """
    addon = get_addon(name)
    if not disabled_too and not state.is_enabled(name):
        return None
    if addon.everyone:
        return Connection(user=user, addon=name, settings=MappingProxyType(addon.setting_defaults()))
    snap = _current()
    row = snap.rows.get(user.id, {}).get(name)
    if row is None:
        return None
    plain = snap.plain.get((user.id, name))
    if plain is None:
        creds = crypto.decrypt(row.credentials) if row.credentials else {}
        settings = {**addon.setting_defaults(), **json.loads(row.settings or "{}")}
        plain = (MappingProxyType(creds), MappingProxyType(settings))
        snap.plain[(user.id, name)] = plain
    return Connection(user=user, addon=name, credentials=plain[0], settings=plain[1], account=row.account,
                      account_conflict=row.account_conflict)


def require(user: User, name: str) -> Connection:
    """Like :func:`get`, for code that cannot go on without the addon.

    Raises:
        NotConnected: ``user`` has not connected ``name``.
    """
    conn = get(user, name)
    if conn is None:
        raise NotConnected(user, name)
    return conn


def list_connections(user: User) -> List[ConnectionInfo]:
    """``user``'s addons in the fixed addon order (``everyone`` addons included)."""
    mine = _current().rows.get(user.id, {})
    out: List[ConnectionInfo] = []
    for k in all_addons():
        row = mine.get(k.name)
        if k.everyone:
            out.append(ConnectionInfo(k.name, MappingProxyType(k.setting_defaults()), None, False, None, None))
        elif row is not None:
            settings = {**k.setting_defaults(), **json.loads(row.settings or "{}")}
            out.append(ConnectionInfo(k.name, MappingProxyType(settings), row.account, bool(row.credentials),
                                       row.created_at, row.updated_at, row.account_conflict))
    return out


def users_with(name: str) -> List[str]:
    """Ids of the users who have ``name`` connected (not for ``everyone``
    addons; none while it is disabled on the server)."""
    if not state.is_enabled(name):
        return []
    return sorted(uid for uid, mine in _current().rows.items() if name in mine)


# --- Who may set what (docs/access.md, "Security rules") ------------------------------

def admin_change(by: Optional[User]) -> bool:
    """``by`` may set any address / chat: None (the server itself, an operator
    with shell access: bin/dana) or a user with
    :data:`ADMIN_CAPABILITY`."""
    return by is None or by.level.allows(ADMIN_CAPABILITY)


def _env_origins(name: str) -> Set[str]:
    out: Set[str] = set()
    for entry in (os.getenv(MEMBER_ORIGINS_ENV) or "").split(","):
        listed, sep, value = entry.strip().partition("=")
        if not sep or listed.strip() != name:
            continue
        try:
            out.add(addresses.origin(value.strip()))
        except ValueError as e:
            logger.warning(f"{MEMBER_ORIGINS_ENV}: {value.strip()!r} for {name} skipped ({e})")
    return out


def allowed_origins(name: str, field_name: str) -> Set[str]:
    """Origins a user without :data:`ADMIN_CAPABILITY` may put in address
    field ``field_name`` of addon ``name``: those of every admin's own
    connection of the addon, plus :data:`MEMBER_ORIGINS_ENV`."""
    out = _env_origins(name)
    for admin in users.list_users():
        if not admin.level.allows(ADMIN_CAPABILITY):
            continue
        conn = get(admin, name)
        if conn is None:
            continue
        value = conn.credentials.get(field_name) or conn.settings.get(field_name)
        try:
            out.add(addresses.origin(str(value or "")))
        except ValueError:
            pass
    return out


# Why a field or an addon cannot be changed by the user looking at it (the web page shows it read-only).
SET_BY_ADMIN = "Set by an admin"


@dataclass(frozen=True)
class ChangeRule:
    """Whether a user may change an addon or one of its fields for a connection,
    known before any value is typed: ``can_change``; when not, ``why_not`` (a
    short phrase); ``rule``: one line on what a value may be, or None.
    :func:`field_rule` / :func:`addon_rule` compute it from the same checks a
    change goes through (:func:`_checked_value`, :func:`_changeable`), so what
    a client is told and what the store refuses cannot drift apart."""
    can_change: bool
    why_not: Optional[str] = None
    rule: Optional[str] = None


def addon_rule(addon: Addon, by: Optional[User]) -> ChangeRule:
    """Whether ``by`` may connect / update / disconnect ``addon`` (its ``change_requires``)."""
    return ChangeRule(True) if addon.changeable_by(by) else ChangeRule(False, SET_BY_ADMIN)


def field_rule(addon: Addon, user: User, name: str, by: Optional[User]) -> ChangeRule:
    """Whether ``by`` may set field ``name`` of ``user``'s connection of
    ``addon`` at all (some values may still be refused: an address on
    an origin nobody allowed, a WAHA session another user has).

    Raises:
        KeyError: the addon has no field ``name``.
    """
    f = addon.field(name)
    if f is None:
        raise KeyError(f"The {addon.name} addon has no field {name!r}")
    if not addon.changeable_by(by):
        return ChangeRule(False, SET_BY_ADMIN)
    admin = admin_change(by)
    if f.type == ADDRESS:
        if admin:
            return ChangeRule(True, rule="An http:// or https:// address, without a user name, ? or #.")
        if not allowed_origins(addon.name, name):
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


def _checked_value(addon: Addon, user: User, name: str, value: Any, by: Optional[User]) -> Any:
    """``value`` of field ``name`` as it is stored (normalised), or InvalidValue."""
    f = addon.field(name)
    if f is None or value is None or (isinstance(value, str) and not value.strip()):
        return value
    lock = field_rule(addon, user, name, by)  # what clients are told in advance (the addon payload)
    if not lock.can_change:
        raise InvalidValue(name, f"{name}: {lock.why_not}; ask an admin to change it.")
    if f.type == ADDRESS:
        try:
            normal = addresses.normalize(str(value))
        except ValueError as e:
            raise InvalidValue(name, f"{name}: {e}") from None
        if not admin_change(by) and addresses.origin(normal) not in allowed_origins(addon.name, name):
            raise InvalidValue(name, f"{name}: {addresses.origin(normal)} is not an address an admin has allowed "
                                     f"for {addon.name}; ask an admin to set it for you.")
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


def _checked(addon: Addon, user: User, values: Optional[Mapping[str, Any]],
             by: Optional[User]) -> Dict[str, Any]:
    return {k: _checked_value(addon, user, k, v, by) for k, v in (values or {}).items()}


def stored_value_problem(conn: Connection) -> Optional[Tuple[str, str]]:
    """``(field, reason)`` when an address / name stored on ``conn`` is not
    valid (a value saved before these rules); None when every one is.
    ``test`` and ``options`` run only on a connection without a problem."""
    addon = get_addon(conn.addon)
    for f in (*addon.credentials, *addon.settings):
        value = conn.credentials.get(f.name) if f in addon.credentials else conn.settings.get(f.name)
        if value is None or value == "" or f.type not in (ADDRESS, NAME):
            continue
        try:
            _checked_value(addon, conn.user, f.name, value, None)
        except InvalidValue as e:
            return f.name, str(e)
    return None


# --- Changes ----------------------------------------------------------------------

def _connectable(user: User, name: str, by: Optional[User]) -> Addon:
    addon = get_addon(name)
    if addon.everyone:
        raise ValueError(f"Every user has the {name} addon; it is not connected or disconnected.")
    if not addon.allowed_for(user):
        raise PermissionError(f"The {name} addon needs the '{addon.requires}' capability, "
                              f"which access level {user.access!r} does not have.")
    if not state.is_enabled(name):
        raise state.AddonDisabled(name)
    _changeable(addon, by)
    return addon


def _changeable(addon: Addon, by: Optional[User]) -> None:
    if not addon.changeable_by(by):
        raise Forbidden(str(addon.change_requires),
                        f"Only a user with '{addon.change_requires}' can connect or change the "
                        f"{addon.name} addon.")


def _validate(addon: Addon, user: User, changes: Mapping[str, Any], credentials: Mapping[str, Any],
              settings: Mapping[str, Any], by: Optional[User]) -> None:
    if addon.validate is not None and changes:
        addon.validate(user, changes, credentials, {**addon.setting_defaults(), **settings}, by)


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


def connect_defaults(user: User) -> List[str]:
    """Turn on, for a new ``user``, every ``default_on`` addon they may have
    and lack (no credentials, its default settings; not one disabled on the
    server). Returns the names turned on."""
    done = []
    for addon in all_addons():
        if not addon.default_on or addon.everyone or not addon.allowed_for(user) or is_connected(user, addon.name) \
                or not state.is_enabled(addon.name):
            continue
        connect(user, addon.name)
        done.append(addon.name)
    return done


FIRST_ADMIN_DEFAULTS_MARK = "addons:first_admin_defaults"


def connect_first_admin_defaults() -> List[str]:
    """Give the first admin their default addons, once
    (``system_marks['addons:first_admin_defaults']``), so one they turn off
    later stays off. Run at startup (addons/bootstrap.py) and when setup
    creates the first admin (core/setup.py). Returns the names turned on now
    ([] with no admin yet, or when given before)."""
    admin = users.first_admin()
    if admin is None:
        return []
    with get_db_connection() as db:
        if db.execute("SELECT 1 FROM system_marks WHERE key = ?", (FIRST_ADMIN_DEFAULTS_MARK,)).fetchone():
            return []
    turned_on = connect_defaults(admin)
    now = user_rows.now_iso()
    with get_db_connection() as db:
        db.execute("INSERT OR IGNORE INTO system_marks (key, last_at, ok, last_ok_at, detail) VALUES (?, ?, 1, ?, ?)",
                   (FIRST_ADMIN_DEFAULTS_MARK, now, now, (", ".join(turned_on) or "none")[:300]))
        db.commit()
    if turned_on:
        logger.info(f"Default addons turned on for {admin.name}: {', '.join(turned_on)}")
    return turned_on


def connect(user: User, name: str, *, credentials: Optional[Mapping[str, Any]] = None,
            settings: Optional[Mapping[str, Any]] = None, account: Optional[str] = None,
            by: Optional[User] = None) -> Connection:
    """Connect ``name`` for ``user``. ``by`` is the user making the change
    (None: the server itself or an operator with shell access); it decides
    what may be set (docs/access.md, "Security rules").

    Raises:
        KeyError: no such addon.
        PermissionError: the user's access level does not allow the addon;
            :class:`Forbidden` when ``by`` lacks the addon's ``change_requires``.
        ValueError: already connected, an unknown credential / setting name,
            or a required credential missing; :class:`InvalidValue` for a
            value that is refused (an address, a Telegram chat, the addon's rule).
        AddonDisabled: the addon is disabled on the server.
        CredentialsKeyError: there are credentials to store and no usable key.
    """
    addon = _connectable(user, name, by)
    credentials = dict(credentials or {})
    check_values(addon, credentials, settings)
    credentials = _checked(addon, user, credentials, by)
    settings = _checked(addon, user, settings, by)
    _validate(addon, user, {**credentials, **settings}, credentials,
              {k: v for k, v in settings.items() if v is not None}, by)
    missing = [f.name for f in addon.credentials if f.required and not credentials.get(f.name)]
    if missing:
        raise ValueError(f"Connecting {name} needs {', '.join(missing)}.")
    token = _encrypt(credentials)
    now = user_rows.now_iso()
    try:
        with _write() as db:
            db.execute("INSERT INTO connections (user_id, addon, credentials, settings, account, created_at, "
                       "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (user.id, name, token, json.dumps(dict(settings or {}), ensure_ascii=False), account,
                        now, now))
    except sqlite3.IntegrityError as e:
        raise ValueError(f"{user.name} already has the {name} addon ({e}).") from e
    finally:
        invalidate()
    logger.info(f"Connected {name} for user {user.id}")
    if addon.on_connect is not None:
        try:
            addon.on_connect(user)
        except Exception:
            logger.error(f"on_connect of {name} failed for user {user.id}", exc_info=True)
    _changed(addon, user)
    return require(user, name)


def _changed(addon: Addon, user: User) -> None:
    """Run the addon's ``on_change`` after a connect / update / disconnect (a failure is logged)."""
    if addon.on_change is None:
        return
    try:
        addon.on_change(user)
    except Exception:
        logger.error(f"on_change of {addon.name} failed for user {user.id}", exc_info=True)


def update(user: User, name: str, *, credentials: Optional[Mapping[str, Any]] = None,
           settings: Optional[Mapping[str, Any]] = None, account: Optional[str] = None,
           by: Optional[User] = None) -> Connection:
    """Change a connected addon: ``credentials`` given are merged into
    the stored ones, ``settings`` given are merged into the stored ones (a
    None value goes back to the default), ``account`` replaces the stored one
    when given. When credentials were given, the addon's
    ``on_credentials_change`` runs afterwards.

    ``by``: who makes the change (see :func:`connect`).

    Raises:
        KeyError: no such addon.
        NotConnected: not connected for ``user``.
        AddonDisabled: the addon is disabled on the server.
        Forbidden: ``by`` lacks the addon's ``change_requires``.
        ValueError: an unknown credential / setting name; InvalidValue for a refused value.
        CredentialsKeyError: no usable key for credentials.
    """
    addon = get_addon(name)
    if not state.is_enabled(name):
        raise state.AddonDisabled(name)
    _changeable(addon, by)
    current = require(user, name)
    check_values(addon, credentials, settings)
    credentials = _checked(addon, user, credentials, by) if credentials else credentials
    settings = _checked(addon, user, settings, by) if settings else settings
    merged = dict(current.settings)
    for key, value in (settings or {}).items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    merged_creds = {k: v for k, v in {**current.credentials, **(credentials or {})}.items() if v is not None}
    _validate(addon, user, {**(credentials or {}), **(settings or {})}, merged_creds, merged, by)
    with _write() as db:
        row = db.execute("SELECT credentials, settings, account FROM connections WHERE user_id = ? AND addon = ?",
                         (user.id, name)).fetchone()
        if row is None:
            invalidate()
            raise NotConnected(user, name)
        token = row["credentials"]
        if credentials:
            token = _encrypt({**current.credentials, **credentials})
        stored = json.loads(row["settings"] or "{}")
        for key, value in (settings or {}).items():
            if value is None:
                stored.pop(key, None)
            else:
                stored[key] = value
        db.execute("UPDATE connections SET credentials = ?, settings = ?, account = ?, updated_at = ? "
                   "WHERE user_id = ? AND addon = ?",
                   (token, json.dumps(stored, ensure_ascii=False), row["account"] if account is None else account,
                    user_rows.now_iso(), user.id, name))
    invalidate()
    if credentials and addon.on_credentials_change is not None:
        try:
            addon.on_credentials_change(user)
        except Exception:
            logger.error(f"on_credentials_change of {name} failed for user {user.id}", exc_info=True)
    _changed(addon, user)
    return require(user, name)


def claim_account(user: User, name: str, account: str, key: Callable[[str], str]) -> Optional[str]:
    """Store ``account`` as ``user``'s connection of ``name``'s account unless
    another user's connection of the addon already holds it (the same
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
            if db.execute("SELECT 1 FROM connections WHERE user_id = ? AND addon = ?",
                          (user.id, name)).fetchone() is None:
                raise NotConnected(user, name)
            holder = next((r["user_id"] for r in db.execute(
                "SELECT user_id, account FROM connections WHERE addon = ? AND user_id != ? "
                "AND COALESCE(account, '') != '' ORDER BY user_id", (name, user.id))
                if wanted and key(r["account"]) == wanted), None)
            db.execute("UPDATE connections SET account = ?, account_conflict = ?, updated_at = ? "
                       "WHERE user_id = ? AND addon = ?",
                       (account if holder is None else None, None if holder is None else account,
                        user_rows.now_iso(), user.id, name))
    finally:
        invalidate()
    return holder


def clear_account(user: User, name: str) -> None:
    """Forget ``user``'s connection of ``name``'s account and any account
    conflict (its credentials changed: the account is learned again)."""
    with get_db_connection() as db:
        db.execute("UPDATE connections SET account = NULL, account_conflict = NULL WHERE user_id = ? AND addon = ?",
                   (user.id, name))
        db.commit()
    invalidate()


def disconnect(user: User, name: str, *, by: Optional[User] = None) -> bool:
    """Remove ``user``'s addon of ``name`` (credentials included).
    Returns False when it was not connected. ``by``: who makes the change
    (see :func:`connect`).

    Raises:
        KeyError: no such addon.
        ValueError: an ``everyone`` addon.
        Forbidden: ``by`` lacks the addon's ``change_requires``.
    """
    addon = get_addon(name)
    if addon.everyone:
        raise ValueError(f"Every user has the {name} addon; it is not connected or disconnected.")
    _changeable(addon, by)
    with get_db_connection() as db:
        cur = db.execute("DELETE FROM connections WHERE user_id = ? AND addon = ?", (user.id, name))
        db.commit()
    invalidate()
    if cur.rowcount:
        logger.info(f"Disconnected {name} for user {user.id}")
        _changed(addon, user)
    return cur.rowcount > 0


# --- Startup ----------------------------------------------------------------------

def stored_credentials() -> int:
    """How many connections hold encrypted credentials."""
    with get_db_connection() as db:
        # scoped: server-wide startup check of the credentials key over every connection
        return db.execute("SELECT COUNT(*) FROM connections WHERE credentials IS NOT NULL").fetchone()[0]


def verify_key() -> None:
    """Startup check of the credentials key (``CREDENTIALS_KEY``, else the key
    file: core/addons/crypto.py): when credentials are stored it must be well
    formed and decrypt every one of them; a key that is set must be well
    formed even when nothing needs it yet.

    Raises:
        CredentialsKeyError: what is wrong and what to do.
    """
    with get_db_connection() as db:
        # scoped: server-wide startup check of the credentials key over every connection
        tokens = [r[0] for r in db.execute("SELECT credentials FROM connections WHERE credentials IS NOT NULL")]
    if not tokens:
        if crypto.key_configured():
            crypto.check_key()
        return
    if not crypto.key_configured():
        raise crypto.CredentialsKeyError(f"{crypto.KEY_ENV} is not set (and there is no key file) but {len(tokens)} "
                                         "connection(s) have stored credentials. " + crypto.GENERATE_HINT)
    for token in tokens:
        crypto.decrypt(token)
