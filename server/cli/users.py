"""``bin/dana users …`` and the per-user part of ``bin/dana addons …``:
Dana's users, their tokens, Telegram ids, connections and shares
(docs/access.md, "Managing users").

Works on the database directly, with the server's own env (``DATABASE_PATH``,
``CREDENTIALS_KEY``); the running server sees a change on its next lookup
(``core.db.ChangeWatch``). In the pod::

    kubectl exec -n <namespace> deploy/<deployment> -- bin/dana users list

Commands (``bin/dana users <command> --help`` for each)::

    users list                              users: access, Telegram ids, tokens, addons
    users add NAME [--access] [--context] [--pronoun] [--languages] [--currency] [--telegram-chat]
    users update USER [--name] [--access] [--context] [--pronoun] [--languages] [--currency] [--other-names]
                      [--prompt-name] [--telegram-chat]
    users remove USER [--delete-data]       without the flag: says what would be deleted, removes nothing
    users tokens issue USER [--label]       prints the token once
    users tokens list [USER]
    users tokens revoke TOKEN_ID
    users telegram add USER TELEGRAM_ID
    users telegram move TELEGRAM_ID USER
    users telegram remove TELEGRAM_ID [--allow-no-ids]
    users sharing [USER]                    who has access to what
    addons show USER                        a user's connections (credential values never shown)
    addons connect USER ADDON [--credentials-file PATH | --credentials-stdin] [SETTING=VALUE ...]
    addons update USER ADDON [--credentials-file PATH | --credentials-stdin] [SETTING=VALUE ...]
    addons disconnect USER ADDON

``USER`` is a user id or name (full name, or a unique start of one, any case).
Credentials are read only from a file or stdin, as ``name=value`` lines
(``#`` comments and blank lines ignored, surrounding quotes stripped), never
from the command line. ``SETTING=VALUE``: the value is read as JSON when it
parses (numbers, ``null``, lists), else as text; ``SETTING=`` (empty) puts the
setting back to its default. Stored secrets are never printed.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, TextIO

import addons  # noqa: F401 — first: registers every addon (and the sharing item types)
from core import access, db, user_locale, user_rows, users
from core.addons import sharing, state
from core.db import get_db_connection
from core.paths import APP_ICONS_DIR, IMAGES_DIR, RECORDINGS_DIR
from core.users import User
from core.addons import crypto, store
from core.addons.addon import get_addon, all_addons

# Rows a removed user keeps: usage is the server's cost history.
KEPT_TABLES = ("usage_events", "usage_daily")


class CliError(Exception):
    """A refusal or a bad argument: printed as ``error: …``, exit status 1."""


# --- Helpers ------------------------------------------------------------------------------------

def find_user(ref: str) -> User:
    """The user ``ref`` names: an id, a full name (any case) or a unique start of a name.

    Raises:
        CliError: no such user, or several match.
    """
    ref = (ref or "").strip()
    everyone = users.list_users()
    hit = users.get(ref)
    if hit is not None:
        return hit
    exact = [u for u in everyone if u.name.casefold() == ref.casefold()]
    if len(exact) == 1:
        return exact[0]
    starts = [u for u in everyone if ref and u.name.casefold().startswith(ref.casefold())]
    if len(starts) == 1:
        return starts[0]
    names = ", ".join(f"{u.name} ({u.id})" for u in (exact or starts or everyone))
    if exact or starts:
        raise CliError(f"{ref!r} matches several users: {names}. Use the id.")
    raise CliError(f"No user {ref!r}. Users: {names or 'none'}.")


def parse_credentials(text: str) -> Dict[str, str]:
    """``name=value`` lines (see the module docstring).

    Raises:
        CliError: a line without ``=``.
    """
    out: Dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise CliError(f"Credentials line {number} is not name=value.")
        name, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[name.strip()] = value
    return out


def parse_settings(pairs: Sequence[str]) -> Dict[str, Any]:
    """``SETTING=VALUE`` arguments (see the module docstring); an empty value is None (the default).

    Raises:
        CliError: an argument without ``=``.
    """
    out: Dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise CliError(f"Setting {pair!r} is not SETTING=VALUE.")
        name, raw = pair.split("=", 1)
        raw = raw.strip()
        if not raw:
            out[name.strip()] = None
            continue
        try:
            out[name.strip()] = json.loads(raw)
        except ValueError:
            out[name.strip()] = raw
    return out


def _read_credentials(args: argparse.Namespace, stdin: TextIO) -> Dict[str, str]:
    if args.credentials_file and args.credentials_stdin:
        raise CliError("Give --credentials-file or --credentials-stdin, not both.")
    if args.credentials_file:
        try:
            return parse_credentials(Path(args.credentials_file).read_text(encoding="utf-8"))
        except OSError as e:
            raise CliError(f"Cannot read {args.credentials_file}: {e.strerror}") from e
    if args.credentials_stdin:
        return parse_credentials(stdin.read())
    return {}


def _chat_id(raw: Optional[str]) -> object:
    """``--telegram-chat``: a chat id, ``none`` to remove it."""
    if raw is None:
        return None
    if raw.strip().lower() in ("", "none", "null"):
        return None
    try:
        return int(raw)
    except ValueError as e:
        raise CliError(f"--telegram-chat must be a numeric chat id or 'none' (not {raw!r}).") from e


def _short(text: str, limit: int = 70) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _user_tables(conn: sqlite3.Connection) -> List[str]:
    """Tables with a ``user_id`` column (each row owned by that user)."""
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return [n for n in names if any(c[1] == "user_id" for c in conn.execute(f"PRAGMA table_info('{n}')"))]


def owned_data(user: User) -> Dict[str, int]:
    """Rows ``user`` owns, per table (tables with none left out; see :data:`KEPT_TABLES`)."""
    out: Dict[str, int] = {}
    with get_db_connection() as conn:
        for table in _user_tables(conn):
            if table in KEPT_TABLES:
                continue
            n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (user.id,)).fetchone()[0]
            if n:
                out[table] = n
    return out


def _left_behind(user: User) -> List[str]:
    """What stays after removing ``user`` (said before and after)."""
    notes = []
    with get_db_connection() as conn:
        usage = conn.execute("SELECT COUNT(*) FROM usage_events WHERE user_id = ?", (user.id,)).fetchone()[0]
        daily = conn.execute("SELECT COUNT(*) FROM usage_daily WHERE user_id = ?", (user.id,)).fetchone()[0]
        added = sum(conn.execute(f"SELECT COUNT(*) FROM {t} WHERE created_by = ? AND user_id != ?",
                                 (user.id, user.id)).fetchone()[0] for t in ("tasks", "entries"))
    if usage or daily:
        notes.append(f"{usage + daily} usage rows are kept (the server's cost history), under the id {user.id}.")
    if added:
        notes.append(f"{added} tasks / entries they added to other people's shared lists and projects stay "
                     "with their owners (their reminders no longer fire for anyone).")
    return notes


def _files_of(user: User) -> List[Path]:
    """Call recordings, saved images and app icons of ``user`` (deleted with them)."""
    with get_db_connection() as conn:
        calls = [r[0] for r in conn.execute("SELECT filename FROM phone_calls WHERE user_id = ?", (user.id,))]
        images = [r[0] for r in conn.execute("SELECT name FROM uploaded_images WHERE user_id = ?", (user.id,))]
    return ([RECORDINGS_DIR / f for f in calls if f and "/" not in f]
            + [IMAGES_DIR / f for f in images if f and "/" not in f]
            + sorted((APP_ICONS_DIR / user.id).glob("*.png")))


def remove_user(user: User) -> List[str]:
    """Delete ``user``, everything they own (database cascades), their call
    recordings, saved images and app icons, and the shares of their items. Returns notes
    on what stays.

    Raises:
        CliError: they are the last admin.
    """
    notes = _left_behind(user)
    files = [f for f in _files_of(user) if f.is_file()]
    with get_db_connection() as conn:
        # Other people's tasks may point at one of their projects: keep the task, drop the link.
        conn.execute("UPDATE tasks SET project_id = NULL WHERE user_id != ? AND project_id IN "
                     "(SELECT id FROM projects WHERE user_id = ?)", (user.id, user.id))
        conn.commit()
    try:
        users.remove(user.id)
    except ValueError as e:
        raise CliError(str(e)) from e
    with get_db_connection() as conn:
        # scoped: admin CLI: every shared item, to drop the shares of the removed user's items
        rows = conn.execute("SELECT DISTINCT item_type, item_id FROM item_access").fetchall()
    for row in rows:
        try:
            t = sharing.item_type(row["item_type"])
        except KeyError:
            continue
        if t.owner_of(row["item_id"]) is None:
            sharing.forget(t.name, row["item_id"])
    for f in files:
        f.unlink(missing_ok=True)
    store.invalidate()
    return notes


def _connection_lines(user: User) -> List[str]:
    lines = []
    for info in store.list_connections(user):
        k = get_addon(info.addon)
        if k.everyone:
            continue
        conn = store.get(user, info.addon, disabled_too=True)
        creds = conn.credentials if conn is not None else {}
        cred_text = ", ".join(f"{f.name} {'set' if creds.get(f.name) else 'not set'}" for f in k.credentials)
        settings = ", ".join(f"{name}={json.dumps(value, ensure_ascii=False)}" for name, value in info.settings.items())
        line = f"  {info.addon}"
        if cred_text:
            line += f"  credentials: {cred_text}"
        if settings:
            line += f"  settings: {settings}"
        if info.account:
            line += f"  account: {info.account}"
        if info.account_conflict:
            line += f"  account conflict: {info.account_conflict} is already connected by another user (not used)"
        if not state.is_enabled(info.addon):
            line += "  (disabled on this server: not used)"
        lines.append(line)
    return lines or ["  (none besides essentials)"]


# --- Commands -----------------------------------------------------------------------------------

def cmd_list(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    everyone = users.list_users()
    if not everyone:
        print("No users yet (the server creates the first one at its first start).", file=out)
    first = users.first_admin()
    for u in everyone:
        mark = " (first admin)" if first is not None and u.id == first.id else ""
        print(f"{u.id}  {u.name}  [{u.access}]{mark}", file=out)
        print(f"  telegram ids: {', '.join(map(str, u.telegram_ids)) or 'none'}"
              f"   proactive chat: {u.telegram_chat_id if u.telegram_chat_id is not None else 'none'}", file=out)
        print(f"  pronoun: {u.pronoun}   context: {_short(u.context) if u.context else 'none'}", file=out)
        print(f"  languages: {','.join(user_locale.languages(u))}{'' if u.languages else ' (server default)'}"
              f"   currency: {user_locale.currency(u)}{'' if u.currency else ' (server default)'}", file=out)
        tokens = ", ".join(f"{t.label or '(no label)'} ({t.id})" for t in users.list_tokens(u.id))
        print(f"  tokens: {tokens or 'none'}", file=out)
        names = [k for k in store.connected_addons(u) if not get_addon(k).everyone]
        print(f"  addons: {', '.join(names) or 'none'}", file=out)
    if users.telegram_unlinked():
        print("Note: no user has a Telegram id, so the Telegram bot refuses everyone "
              "(link one: telegram add USER ID).", file=out)


def _profile_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--context", help="who they are, family, work, the people they talk about (for prompts)")
    p.add_argument("--pronoun", choices=user_rows.PRONOUNS, help="how prompts refer to them")
    p.add_argument("--languages", help="their languages, a comma list of codes, the first the default "
                                       "(e.g. 'he,en'; '' = the server's DEFAULT_LANGUAGES)")
    p.add_argument("--currency", help="their currency, ISO 4217 (e.g. EUR; '' = the server's CURRENCY)")


def cmd_add(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    profile = user_rows.Profile(context=args.context or "", pronoun=args.pronoun or "they",
                                languages=args.languages or "", currency=args.currency or "")
    try:
        user = users.create(args.name, args.access, telegram_chat_id=_chat_id(args.telegram_chat),  # type: ignore[arg-type]
                            profile=profile)
    except ValueError as e:
        raise CliError(str(e)) from e
    defaults = store.connect_defaults(user)
    has = ", ".join(["essentials", *defaults])
    print(f"Added {user.name} ({user.id}, {user.access}). They have {has} until "
          f"you connect others: bin/dana addons connect {user.id} <addon>. "
          "Their phone and other devices: pair them on the web Devices page (an admin can pair one for them).",
          file=out)


def cmd_update(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    user = find_user(args.user)
    changes: Dict[str, Any] = {}
    if args.name is not None:
        changes["name"] = args.name
    if args.access is not None:
        changes["access_level"] = args.access
    for field in ("context", "pronoun", "languages", "currency", "other_names", "prompt_name"):
        if getattr(args, field) is not None:
            changes[field] = getattr(args, field)
    if args.telegram_chat is not None:
        changes["telegram_chat_id"] = _chat_id(args.telegram_chat)
    if not changes:
        raise CliError("Nothing to change: give at least one option (see --help).")
    try:
        updated = users.update(user.id, **changes)
    except ValueError as e:
        raise CliError(str(e)) from e
    print(f"Updated {updated.name} ({updated.id}): {', '.join(sorted(changes))}.", file=out)


def cmd_remove(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    user = find_user(args.user)
    if user.is_admin and sum(1 for u in users.list_users() if u.is_admin) <= 1:
        raise CliError(f"{user.name} is the last admin and cannot be removed.")
    owned = owned_data(user)
    files = [f for f in _files_of(user) if f.is_file()]
    print(f"{user.name} ({user.id}) owns:", file=out)
    for table, n in owned.items():
        print(f"  {table}: {n}", file=out)
    if files:
        print(f"  files: {len(files)} (call recordings, saved images, app icons)", file=out)
    if not owned and not files:
        print("  nothing", file=out)
    for note in _left_behind(user):
        print(f"  note: {note}", file=out)
    if not args.delete_data:
        raise CliError("Nothing was removed. Removing a user deletes everything listed above; "
                       "run again with --delete-data to do it.")
    notes = remove_user(user)
    print(f"Removed {user.name} ({user.id}) and their data.", file=out)
    for note in notes:
        print(f"  {note}", file=out)


def cmd_tokens_issue(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    user = find_user(args.user)
    raw, token = users.issue_token(user.id, args.label or "")
    print(f"Token {token.id} for {user.name}{f' ({token.label})' if token.label else ''}:", file=out)
    print(raw, file=out)
    print("It is shown only now; it is stored hashed. Revoke it with: "
          f"bin/dana users tokens revoke {token.id}", file=out)


def cmd_tokens_list(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    targets = [find_user(args.user)] if args.user else users.list_users()
    any_token = False
    for u in targets:
        for t in users.list_tokens(u.id):
            any_token = True
            print(f"{t.id}  {u.name}  {t.label or '(no label)'}  created {t.created_at[:19]}  "
                  f"last used {(t.last_used_at or 'never')[:19]}", file=out)
    if not any_token:
        print("No stored tokens (API_TOKEN, if set, is the first admin's and is not stored).", file=out)


def cmd_tokens_revoke(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    if not users.revoke_token(args.token_id):
        raise CliError(f"No token {args.token_id!r}.")
    print(f"Revoked token {args.token_id} (its devices and web logins stop working).", file=out)


def cmd_telegram_add(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    user = find_user(args.user)
    try:
        users.add_telegram_id(user.id, args.telegram_id)
    except ValueError as e:
        raise CliError(f"{e} Use: bin/dana users telegram move {args.telegram_id} {user.id}") from e
    print(f"Telegram id {args.telegram_id} is now {user.name}.", file=out)


def cmd_telegram_move(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    user = find_user(args.user)
    try:
        users.move_telegram_id(args.telegram_id, user.id)
    except LookupError as e:
        raise CliError(str(e)) from e
    print(f"Telegram id {args.telegram_id} is now {user.name}.", file=out)


def cmd_telegram_remove(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    owners = [u for u in users.list_users() if args.telegram_id in u.telegram_ids]
    if not owners:
        raise CliError(f"No user has Telegram id {args.telegram_id}.")
    if sum(len(u.telegram_ids) for u in users.list_users()) == 1 and not args.allow_no_ids:
        raise CliError("That is the last Telegram id: without any, the Telegram bot refuses everyone. "
                       "Add another first, or pass --allow-no-ids.")
    users.remove_telegram_id(args.telegram_id)
    print(f"Telegram id {args.telegram_id} removed from {owners[0].name}.", file=out)


def cmd_show(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    user = find_user(args.user)
    print(f"{user.name} ({user.id}):", file=out)
    for line in _connection_lines(user):
        print(line, file=out)


def _change(args: argparse.Namespace, out: TextIO, stdin: TextIO, *, connecting: bool) -> None:
    user = find_user(args.user)
    try:
        get_addon(args.addon)
    except KeyError as e:
        raise CliError(f"No addon {args.addon!r} (see: bin/dana addons list).") from e
    credentials = _read_credentials(args, stdin)
    settings = parse_settings(args.settings)
    try:
        if connecting:
            store.connect(user, args.addon, credentials=credentials or None,
                          settings={k: v for k, v in settings.items() if v is not None})
        else:
            store.update(user, args.addon, credentials=credentials or None, settings=settings or None)
    except PermissionError as e:
        raise CliError(str(e)) from e
    except store.NotConnected as e:
        raise CliError(f"{e} Use: bin/dana addons connect {user.id} {args.addon}") from e
    except state.AddonDisabled as e:
        raise CliError(f"{e} (bin/dana addons enable {args.addon})") from e
    except crypto.CredentialsKeyError as e:
        raise CliError(str(e)) from e
    except ValueError as e:
        raise CliError(str(e)) from e
    print(f"{'Connected' if connecting else 'Updated'} {args.addon} for {user.name}:", file=out)
    for line in _connection_lines(user):
        if line.strip().split(" ")[0] == args.addon:
            print(line, file=out)


def cmd_connect(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    _change(args, out, stdin, connecting=True)


def cmd_connection_update(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    _change(args, out, stdin, connecting=False)


def cmd_disconnect(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    user = find_user(args.user)
    try:
        gone = store.disconnect(user, args.addon)
    except KeyError as e:
        raise CliError(f"No addon {args.addon!r}.") from e
    except ValueError as e:
        raise CliError(str(e)) from e
    if not gone:
        raise CliError(f"{user.name} does not have the {args.addon} addon.")
    print(f"Disconnected {args.addon} for {user.name} (their data of that addon is kept; connecting it again "
          "brings it back).", file=out)


def cmd_sharing(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    who = find_user(args.user) if args.user else None
    with get_db_connection() as conn:
        rows = conn.execute("SELECT item_type, item_id, user_id, level, granted_by FROM item_access "
                            "ORDER BY item_type, item_id, granted_at").fetchall()
    shown = 0
    for r in rows:
        try:
            t = sharing.item_type(r["item_type"])
        except KeyError:
            continue
        owner_id = t.owner_of(r["item_id"])
        if who is not None and who.id not in (owner_id, r["user_id"]):
            continue
        owner = users.get(owner_id) if owner_id else None
        grantee = users.get(r["user_id"])
        print(f"{t.label} {t.title_of(r['item_id']) or r['item_id']!r} ({r['item_id']}) of "
              f"{owner.name if owner else '?'} → {grantee.name if grantee else r['user_id']}: {r['level']}", file=out)
        shown += 1
    if not shown:
        print("Nothing is shared." if who is None else f"Nothing is shared by or with {who.name}.", file=out)


# --- Parser -------------------------------------------------------------------------------------

def register(sub: Any) -> None:
    """Add the ``users`` commands to ``sub`` (the ``bin/dana users`` sub-parsers)."""
    p = sub.add_parser("list", help="every user with access, Telegram ids, tokens and addons")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("add", help="add a user")
    p.add_argument("name")
    p.add_argument("--access", choices=list(access.LEVELS), default=access.MEMBER)
    _profile_args(p)
    p.add_argument("--telegram-chat", help="Telegram chat id for proactive messages")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("update", help="change a user's name, access, prompt profile, languages, currency "
                                      "or proactive chat")
    p.add_argument("user")
    p.add_argument("--name")
    p.add_argument("--access", choices=list(access.LEVELS))
    _profile_args(p)
    p.add_argument("--other-names", help="other spellings of the first name, e.g. in Hebrew (call analysis)")
    p.add_argument("--prompt-name", help="what Dana's instructions call them ('' = their first name)")
    p.add_argument("--telegram-chat", help="Telegram chat id for proactive messages, or 'none'")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("remove", help="remove a user (with --delete-data: and everything they own)")
    p.add_argument("user")
    p.add_argument("--delete-data", action="store_true",
                   help="really remove them and delete their data (without it: only say what would go)")
    p.set_defaults(func=cmd_remove)

    tokens = sub.add_parser("tokens", help="API tokens (a paired device has its own; these are for scripts)").add_subparsers(
        dest="tokens_command", required=True, metavar="action")
    p = tokens.add_parser("issue", help="issue a token (printed once)")
    p.add_argument("user")
    p.add_argument("--label", help="what uses it, e.g. a script's name")
    p.set_defaults(func=cmd_tokens_issue)
    p = tokens.add_parser("list", help="stored tokens (never the tokens themselves)")
    p.add_argument("user", nargs="?")
    p.set_defaults(func=cmd_tokens_list)
    p = tokens.add_parser("revoke", help="revoke a token")
    p.add_argument("token_id")
    p.set_defaults(func=cmd_tokens_revoke)

    tg = sub.add_parser("telegram", help="Telegram user ids").add_subparsers(
        dest="telegram_command", required=True, metavar="action")
    p = tg.add_parser("add", help="let a Telegram user id be this user")
    p.add_argument("user")
    p.add_argument("telegram_id", type=int)
    p.set_defaults(func=cmd_telegram_add)
    p = tg.add_parser("move", help="move a Telegram user id to another user")
    p.add_argument("telegram_id", type=int)
    p.add_argument("user")
    p.set_defaults(func=cmd_telegram_move)
    p = tg.add_parser("remove", help="forget a Telegram user id")
    p.add_argument("telegram_id", type=int)
    p.add_argument("--allow-no-ids", action="store_true",
                   help="allow removing the last id (the bot then refuses everyone)")
    p.set_defaults(func=cmd_telegram_remove)

    p = sub.add_parser("sharing", help="who has access to what")
    p.add_argument("user", nargs="?")
    p.set_defaults(func=cmd_sharing)


def register_connections(sub: Any) -> None:
    """Add the per-user addon commands (show, connect, update, disconnect) to the ``bin/dana addons`` sub-parsers."""
    p = sub.add_parser("show", help="a user's connections (credential values are never shown)")
    p.add_argument("user")
    p.set_defaults(func=cmd_show)
    for name, func, text in (("connect", cmd_connect, "turn an addon on for a user"),
                             ("update", cmd_connection_update, "change a user's addon credentials / settings")):
        p = sub.add_parser(name, help=text)
        p.add_argument("user")
        p.add_argument("addon")
        p.add_argument("settings", nargs="*", metavar="SETTING=VALUE")
        p.add_argument("--credentials-file", metavar="PATH", help="name=value lines")
        p.add_argument("--credentials-stdin", action="store_true", help="read name=value lines from stdin")
        p.set_defaults(func=func)
    p = sub.add_parser("disconnect", help="turn an addon off for a user (its data is kept)")
    p.add_argument("user")
    p.add_argument("addon")
    p.set_defaults(func=cmd_disconnect)
