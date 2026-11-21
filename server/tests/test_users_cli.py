"""bin/users.py, the server's user management tool (docs/access.md, "Managing users").
Run: bin/test test_users_cli

Calls its ``main(argv)`` in process against a temporary database (plus one
real subprocess run): users (list, add, update, remove with and without
``--delete-data``, the last admin protected), tokens (issued once, listed
without the token, revoked), Telegram ids (add, move, remove, the last one
protected), addons (kinds, show without credential values, connect /
update with credentials only from a file or stdin, settings ``key=value``,
``ops`` refused for a member, disconnect), sharing, the refusal on a database
that is not migrated. Stored secrets never appear in any output.
"""

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
DB_PATH = testenv.isolate("users_cli_test")
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake credentials
ROOT = testenv.SERVER
sys.path.insert(0, str(ROOT))

from core import db, users  # noqa: E402

db.migrate()
users.bootstrap()

_spec = importlib.util.spec_from_file_location("users_cli", ROOT / "bin" / "users.py")
assert _spec is not None and _spec.loader is not None
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

from core.addons import sharing  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from core.addons import store  # noqa: E402
from addons.tasks import groups  # noqa: E402

results: List[Tuple[str, bool]] = []
SECRET = "very-secret-token-123"
outputs: List[str] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    ok = bool(condition)
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {detail}" if detail and not ok else ""))


def run(*argv: str, stdin: str = "") -> Tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):  # argparse writes to sys.*
            rc = cli.main(list(argv), stdout=out, stdin=io.StringIO(stdin), stderr=err)
    except SystemExit as e:  # argparse: bad usage / --help
        rc = int(e.code or 0)
    outputs.append(out.getvalue() + err.getvalue())
    return rc, out.getvalue(), err.getvalue()


ADMIN = users.first_admin()
assert ADMIN is not None


def test_users() -> None:
    rc, out, _ = run("--help")
    check("help: lists the commands", rc == 0 and all(c in out for c in ("list", "add", "update", "remove", "tokens",
                                                                          "telegram", "addons", "sharing")))
    rc, out, _ = run("addons", "connect", "--help")
    check("help: per command", rc == 0 and "--credentials-file" in out and "SETTING=VALUE" in out)

    rc, out, _ = run("add", "Bob Builder", "--pronoun", "he", "--context", "Bob is a builder. Wendy is his partner.")
    bob = next((u for u in users.list_users() if u.name == "Bob Builder"), None)
    check("add: a member with their profile", rc == 0 and bob is not None and bob.access == "member"
          and bob.pronoun == "he" and bob.context.startswith("Bob is a builder") and bob.id in out, out)
    rc, _, _ = run("add", "Carl", "--access", "member", "--telegram-chat", "4242")
    carl = next(u for u in users.list_users() if u.name == "Carl")
    check("add: proactive chat, default pronoun they", rc == 0 and carl.telegram_chat_id == 4242 and carl.pronoun == "they")
    rc, _, err = run("add", "Dora", "--pronoun", "it")
    check("add: an unknown pronoun is bad usage", rc == 2 and not any(u.name == "Dora" for u in users.list_users()))

    rc, out, _ = run("list")
    check("list: every user with access, pronoun, addons", rc == 0 and "Bob Builder  [member]" in out
          and f"{ADMIN.name}  [admin] (first admin)" in out and "pronoun: he" in out and "proactive chat: 4242" in out,
          out)

    rc, out, _ = run("update", "bob", "--name", "Bob B", "--pronoun", "they", "--other-names", "בוב",
                     "--telegram-chat", "77")
    bob = users.get(bob.id)
    check("update: name, pronoun, other names, chat (a user found by the start of the name)",
          rc == 0 and bob.name == "Bob B" and bob.pronoun == "they" and bob.other_names == "בוב"
          and bob.telegram_chat_id == 77, out)
    rc, _, _ = run("update", "Bob B", "--telegram-chat", "none")
    check("update: 'none' removes the proactive chat", rc == 0 and users.get(bob.id).telegram_chat_id is None)
    rc, _, err = run("update", ADMIN.id, "--access", "member")
    check("update: the last admin keeps admin", rc == 1 and "last admin" in err and users.get(ADMIN.id).is_admin, err)
    rc, _, err = run("update", "nobody-here", "--name", "x")
    check("update: an unknown user is refused, users listed", rc == 1 and "No user" in err and "Bob B" in err, err)
    rc, _, err = run("update", "Bob B")
    check("update: nothing to change is refused", rc == 1 and "Nothing to change" in err)
    rc, _, _ = run("update", "carl", "--access", "admin")
    check("update: access", rc == 0 and users.get(carl.id).is_admin)
    rc, _, _ = run("update", "carl", "--access", "member")
    check("update: access back (another admin exists)", rc == 0 and not users.get(carl.id).is_admin)


def test_tokens() -> None:
    bob = next(u for u in users.list_users() if u.name == "Bob B")
    rc, out, _ = run("tokens", "issue", bob.id, "--label", "phone")
    raw = out.splitlines()[1].strip() if rc == 0 else ""
    check("tokens issue: printed once, it works", rc == 0 and users.by_token(raw) is not None
          and users.by_token(raw).id == bob.id, out)
    token = users.list_tokens(bob.id)[0]
    rc, out, _ = run("tokens", "list")
    check("tokens list: label and id, never the token", rc == 0 and token.id in out and "phone" in out and raw not in out, out)
    rc, out, _ = run("list")
    check("list: token labels", f"phone ({token.id})" in out and raw not in out)
    rc, _, _ = run("tokens", "revoke", token.id)
    check("tokens revoke: the token stops working", rc == 0 and users.by_token(raw) is None)
    rc, _, err = run("tokens", "revoke", token.id)
    check("tokens revoke: an unknown id is refused", rc == 1 and "No token" in err)


def test_telegram() -> None:
    bob = next(u for u in users.list_users() if u.name == "Bob B")
    carl = next(u for u in users.list_users() if u.name == "Carl")
    check("telegram: open before any id", users.telegram_open())
    rc, _, _ = run("telegram", "add", bob.id, "1001")
    check("telegram add", rc == 0 and users.by_telegram_id(1001).id == bob.id and not users.telegram_open())
    rc, _, err = run("telegram", "add", carl.id, "1001")
    check("telegram add: an id someone has is refused, move suggested", rc == 1 and "telegram move" in err, err)
    rc, _, _ = run("telegram", "move", "1001", "carl")
    check("telegram move", rc == 0 and users.by_telegram_id(1001).id == carl.id)
    rc, _, err = run("telegram", "remove", "1001")
    check("telegram remove: the last id is refused (the bot would open)",
          rc == 1 and "--allow-open-bot" in err and users.by_telegram_id(1001).id == carl.id)
    run("telegram", "add", ADMIN.id, "1002")
    rc, _, _ = run("telegram", "remove", "1001")
    check("telegram remove", rc == 0 and users.by_telegram_id(1001) is None and users.by_telegram_id(1002).id == ADMIN.id)
    rc, _, err = run("telegram", "move", "999", "carl")
    check("telegram move: an id nobody has is refused", rc == 1 and "No user has" in err)


def test_addons(tmp: Path) -> None:
    bob = next(u for u in users.list_users() if u.name == "Bob B")
    rc, out, _ = run("addons", "list")
    check("addons list: fields and the admin-only addon", rc == 0 and "credential token" in out
          and "setting person" in out and "ops:" in out and "admins only" in out and "essentials:" in out, out)
    creds = tmp / "ha.env"
    creds.write_text(f"# Bob's Home Assistant\nhost=http://ha-bob.invalid\ntoken=\"{SECRET}\"\n", encoding="utf-8")
    rc, out, err = run("addons", "connect", "bob", "homeassistant", "--credentials-file", str(creds),
                       "person=person.bob", "car_device=Bob Car")
    conn = store.get(users.get(bob.id), "homeassistant")
    check("connect: credentials from a file (quotes stripped), settings from key=value",
          rc == 0 and conn is not None and conn.credentials["token"] == SECRET and conn.settings["person"] == "person.bob"
          and conn.settings["car_device"] == "Bob Car", out + err)
    check("connect: the output names credentials set, never their values",
          "token set" in out and SECRET not in out, out)
    rc, _, err = run("addons", "update", "bob", "homeassistant", f"token={SECRET}")
    check("credentials never from argv: a credential as a setting is refused", rc == 1 and "no setting token" in err
          and SECRET not in err, err)
    rc, out, _ = run("addons", "update", "bob", "homeassistant", "--credentials-stdin", "car_device=",
                     "weather_entity=weather.bob", stdin="token=new-secret-456\n")
    conn = store.get(users.get(bob.id), "homeassistant")
    check("update: credentials from stdin merged, an empty setting back to its default",
          rc == 0 and conn.credentials == {"host": "http://ha-bob.invalid", "token": "new-secret-456"}
          and conn.settings["car_device"] is None and conn.settings["weather_entity"] == "weather.bob"
          and "new-secret-456" not in out, out)
    rc, out, _ = run("addons", "connect", "bob", "shopping")
    check("connect: an addon without credentials", rc == 0 and store.is_connected(users.get(bob.id), "shopping"), out)
    rc, _, _ = run("addons", "connect", "bob", "tasks")
    check("connect: on_connect runs (default task lists)", rc == 0
          and len(groups.list_groups(users.get(bob.id))) >= 1)
    rc, _, err = run("addons", "connect", "bob", "ops")
    check("connect: ops refused for a member", rc == 1 and "ops" in err and not store.is_connected(users.get(bob.id), "ops"))
    rc, _, err = run("addons", "connect", "bob", "homeassistant", "--credentials-stdin",
                     stdin="host=http://ha.invalid\ntoken=y\n")
    check("connect: twice is refused", rc == 1 and "already" in err)
    rc, _, err = run("addons", "connect", "bob", "whatsapp", "--credentials-stdin", stdin="api_key=k\n")
    check("connect: a missing required credential is refused", rc == 1 and "needs" in err
          and not store.is_connected(users.get(bob.id), "whatsapp"), err)
    rc, out, _ = run("addons", "connect", "bob", "whatsapp", "--credentials-stdin", "voice_telegram_chat_id=-100123",
                     stdin="url=http://waha-bob.invalid\nsession=bob\n")
    check("connect: a JSON value is stored as JSON (a chat id as a number)",
          rc == 0 and store.get(users.get(bob.id), "whatsapp").settings["voice_telegram_chat_id"] == -100123, out)
    rc, _, _ = run("addons", "disconnect", "bob", "whatsapp")
    rc, _, err = run("addons", "update", "bob", "calendar", "calendar_id=x")
    check("update: not connected → told to connect", rc == 1 and "addons connect" in err)
    rc, _, err = run("addons", "connect", "bob", "nope")
    check("connect: an unknown addon", rc == 1 and "No addon" in err)
    rc, _, err = run("addons", "connect", "bob", "homeassistant", "--credentials-file", str(tmp / "missing.env"))
    check("connect: an unreadable credentials file", rc == 1 and "Cannot read" in err)
    bad = tmp / "bad.env"
    bad.write_text("just-a-value\n", encoding="utf-8")
    rc, _, err = run("addons", "connect", "bob", "calendar", "--credentials-file", str(bad))
    check("connect: a credentials line without = is refused (the value not echoed)",
          rc == 1 and "line 1" in err and "just-a-value" not in err)
    rc, out, _ = run("addons", "show", "bob")
    check("show: kinds, settings, credentials set / not set, no values",
          rc == 0 and "homeassistant" in out and "token set" in out and '"person.bob"' in out and "new-secret" not in out
          and "shopping" in out and "tasks" in out, out)
    rc, out, _ = run("list")
    check("list: connected addons", "addons: tasks, shopping, homeassistant" in out, out)
    rc, _, _ = run("addons", "disconnect", "bob", "shopping")
    check("disconnect", rc == 0 and not store.is_connected(users.get(bob.id), "shopping"))
    rc, _, err = run("addons", "disconnect", "bob", "shopping")
    check("disconnect: not connected is refused", rc == 1 and "does not have" in err)
    rc, _, err = run("addons", "disconnect", "bob", "essentials")
    check("disconnect: core cannot be", rc == 1)

    run("add", "Eve Admin", "--access", "admin")
    rc, _, _ = run("addons", "connect", "Eve Admin", "ops")
    check("connect: ops for an admin", rc == 0 and store.is_connected(next(u for u in users.list_users()
                                                                          if u.name == "Eve Admin"), "ops"))


def test_sharing_and_remove() -> None:
    bob = users.get(next(u for u in users.list_users() if u.name == "Bob B").id)
    carl = users.get(next(u for u in users.list_users() if u.name == "Carl").id)
    store.connect(carl, "tasks")
    store.connect(carl, "knowledge")
    store.connect(bob, "knowledge")
    rc, out, _ = run("sharing")
    check("sharing: nothing yet", rc == 0 and "Nothing is shared" in out)
    bob_list = groups.list_groups(bob)[0]
    sharing.set_level(bob, "task_list", str(bob_list["id"]), carl.id, "edit")
    rc, out, _ = run("sharing")
    check("sharing: who has what", rc == 0 and f"task list '{bob_list['name']}'" in out and "of Bob B → Carl: edit" in out,
          out)
    rc, out, _ = run("sharing", "Eve Admin")
    check("sharing: for one user", rc == 0 and "Nothing is shared by or with Eve Admin" in out)

    from addons.knowledge import projects
    proj = projects.create_project(bob, "Bob's build")
    pid = proj["id"] if isinstance(proj, dict) else proj
    with get_db_connection() as conn:
        carl_group = conn.execute("SELECT id FROM task_groups WHERE user_id = ? LIMIT 1", (carl.id,)).fetchone()[0]
        conn.execute("INSERT INTO tasks (id, title, status, group_id, user_id, created_by, project_id) "
                     "VALUES ('t-carl', 'Carl task', 'needs_action', ?, ?, ?, ?)", (carl_group, carl.id, carl.id, pid))
        conn.commit()

    rc, out, err = run("remove", "Bob B")
    check("remove: without --delete-data says what would go and removes nothing",
          rc == 1 and "connections: " in out and "task_groups: " in out and "--delete-data" in err
          and users.get(bob.id) is not None, out + err)
    rc, out, _ = run("remove", "Bob B", "--delete-data")
    with get_db_connection() as conn:
        left = {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id = ?", (bob.id,)).fetchone()[0]
                for t in ("connections", "task_groups", "projects", "user_tokens", "user_telegram")}
        shares = conn.execute("SELECT COUNT(*) FROM item_access").fetchone()[0]
        carl_task = conn.execute("SELECT project_id FROM tasks WHERE id = 't-carl'").fetchone()
    check("remove --delete-data: the user and everything they owned are gone",
          rc == 0 and users.get(bob.id) is None and not any(left.values()), (out, left))
    check("remove: the shares of their items are gone", shares == 0)
    check("remove: another user's task that pointed at their project stays, unlinked",
          carl_task is not None and carl_task[0] is None)
    store.invalidate()
    check("remove: no addons left for them", store.users_with("homeassistant") == [])

    rc, _, _ = run("update", "Eve Admin", "--access", "member")
    rc, _, err = run("remove", ADMIN.id, "--delete-data")
    check("remove: the last admin is refused", rc == 1 and "last admin" in err and users.get(ADMIN.id) is not None)


def test_not_migrated() -> None:
    real = cli.db.applied_migrations
    cli.db.applied_migrations = lambda *a, **k: [1, 2]
    try:
        rc, _, err = run("list")
    finally:
        cli.db.applied_migrations = real
    check("a database that is not up to date is refused", rc == 1 and "start the server once first" in err)


def test_subprocess() -> None:
    proc = subprocess.run([sys.executable, str(ROOT / "bin" / "users.py"), "list"], capture_output=True, text=True,
                          env={**os.environ, "DATABASE_PATH": DB_PATH}, timeout=120)
    check("runs as a script with the server's env", proc.returncode == 0 and "Carl  [member]" in proc.stdout,
          proc.stderr[-500:])
    outputs.append(proc.stdout + proc.stderr)


def main() -> None:
    tmp = Path(testenv.scratch_dir())
    test_users()
    test_tokens()
    test_telegram()
    test_addons(tmp)
    test_sharing_and_remove()
    test_not_migrated()
    test_subprocess()
    check("no stored secret in any output", not any(s in o for o in outputs for s in (SECRET, "new-secret-456")))
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
