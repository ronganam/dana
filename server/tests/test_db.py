"""Checks for core/db: connections, per-addon migrations, converting a Dana 9.0 database. Run: bin/test test_db

Every database here is a fresh temp file; ``tests/fixtures/db_9_0.sql`` is a
database of Dana 9.0 (old single-numbered migrations, at 33). Covers: a fresh
database gets core's and every official addon's baseline; the converted 9.0
fixture has the same schema as a fresh one (tables, columns, indexes, foreign
keys, triggers) and every row it had; converting at 33 or 34, idempotent, after
a snapshot; older / newer databases refused untouched; an addon's later
migration (and a new addon's first) applies on the next start; a failing
migration rolls back; migration numbering is checked.
"""

import os
import re
import sqlite3
import sys
import textwrap

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("db_test")

from core import addon_dirs, db
from core.db import convert, migrations

FIXTURE = os.path.join(testenv.FIXTURES, "db_9_0.sql")
OFFICIAL = migrations.official_with_migrations()
BASELINES = ["core:0001_baseline"] + [f"{a}:0001_baseline" for a in sorted(OFFICIAL)]
# Core's migrations after its baseline: applied on a fresh database and after converting 9.0.
CORE_LATER = dict(migrations.all_migrations())[migrations.CORE][1:]
CORE_LABELS = [m.label for m in CORE_LATER]
CORE_ROWS = [("core", m.version, m.name) for m in CORE_LATER]
# Tables a later core migration drops (m0003: the env import is gone).
DROPPED = {"connection_imports", "connection_import_settings"}
# Old migration 34 renames these 9.0 tables (and the column ``kind`` → ``addon``).
RENAMED = {"integrations": "connections", "integration_imports": "connection_imports",
           "integration_import_settings": "connection_import_settings"}

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


def new_path(name: str) -> str:
    folder = testenv.scratch_path(os.path.splitext(name)[0])  # own folder: snapshots go next to the DB
    os.makedirs(folder)
    return os.path.join(folder, name)


def load_fixture(path: str) -> None:
    conn = sqlite3.connect(path)
    with open(FIXTURE, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.close()


def sql(path: str, query: str, args: tuple = ()) -> list:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(query, args).fetchall()
    finally:
        conn.close()


def tables(path: str) -> set:
    return {r[0] for r in sql(path, "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}


def dump_data(path: str) -> dict:
    """Every row of every table but ``schema_migrations`` (FTS contents included), as sorted tuples."""
    conn = sqlite3.connect(path)
    try:
        out = {}
        for t in sorted(tables(path) - {"schema_migrations"}):
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info('{t}')")]
            out[t] = sorted(tuple(map(repr, row)) for row in conn.execute(f"SELECT {', '.join(cols)} FROM '{t}'"))
        out["sqlite_sequence"] = sorted(conn.execute("SELECT name, seq FROM sqlite_sequence").fetchall())
        return out
    finally:
        conn.close()


def shape(path: str) -> dict:
    """Comparable schema: per table its columns in order, indexes, foreign keys;
    plus the triggers and the partial indexes."""
    conn = sqlite3.connect(path)
    try:
        out = {}
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for t in names:
            cols = [tuple(r[1:]) for r in conn.execute(f"PRAGMA table_info('{t}')")]
            idx = set()
            for r in conn.execute(f"PRAGMA index_list('{t}')"):
                icols = tuple(c[2] for c in conn.execute(f"PRAGMA index_info('{r[1]}')"))
                name = r[1] if not r[1].startswith("sqlite_autoindex_") else "auto"
                idx.add((name, r[2], r[3], r[4], icols))
            fks = sorted(tuple(r[2:5]) + (r[6],) for r in conn.execute(f"PRAGMA foreign_key_list('{t}')"))
            out[t] = (cols, idx, fks)
        out["__triggers__"] = sorted((n, t, " ".join(s.split())) for n, t, s in conn.execute(
            "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'trigger'"))
        out["__partial__"] = sorted(conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND sql LIKE '%WHERE%'"))
        return out
    finally:
        conn.close()


def schema_objects(path: str) -> set:
    return set(sql(path, "SELECT type, name, tbl_name, sql FROM sqlite_master"))


def normalized(path: str) -> set:
    """Every schema object with its SQL's whitespace collapsed (the baselines are indented as code)."""
    return {(t, n, tb, " ".join(re.sub(r"\s*([(),])\s*", r"\1", s or "").split())) for t, n, tb, s in schema_objects(path)}


def snapshots(path: str) -> list:
    d = os.path.join(os.path.dirname(path), "snapshots")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def refused(path: str) -> str:
    try:
        db.migrate(path)
    except db.SchemaError as e:
        return str(e)
    return ""


def write_addon(root: str, name: str, files: dict) -> None:
    """An addon folder ``root/name`` with ``addon.py`` and the given ``migrations/`` files."""
    folder = os.path.join(root, name, "migrations")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(root, name, "addon.py"), "w", encoding="utf-8") as f:
        f.write(f"from core.addons import Addon\naddon = Addon(name={name!r}, description='Test addon.')\n")
    for file, body in files.items():
        with open(os.path.join(folder, file), "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(body))


# --- Connection ---------------------------------------------------------------------

def test_connection():
    conn = db.get_db_connection(new_path("c.sqlite3"))
    try:
        check("connection: rows are sqlite3.Row", conn.row_factory is sqlite3.Row)
        check("connection: foreign keys on", conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1)
        check("connection: busy timeout from DB_BUSY_TIMEOUT_MS",
              conn.execute("PRAGMA busy_timeout").fetchone()[0] == db.DB_BUSY_TIMEOUT_MS)
    finally:
        conn.close()
    core = dict(migrations.all_migrations())[migrations.CORE]
    check("migrations: core's baseline first, then its later migrations (m0003 drops the env import tables)",
          [m.label for m in core][0] == "core:0001_baseline" and "core:0003_drop_env_imports" in CORE_LABELS,
          [m.label for m in core])
    check("migrations: the official addons with tables have a baseline",
          {"tasks", "phone", "people", "knowledge", "about", "shopping", "whatsapp", "contacts", "money"} == set(OFFICIAL),
          str(OFFICIAL))


# --- Fresh database -----------------------------------------------------------------

def test_fresh():
    path = new_path("fresh.sqlite3")
    applied = db.migrate(path)
    check("fresh: core first, then every official addon's baseline", applied[0] == "core:0001_baseline"
          and sorted(applied) == sorted(BASELINES + CORE_LABELS), str(applied))
    check("fresh: recorded per addon in schema_migrations",
          sorted(db.applied_migrations(path))
          == sorted([("core", 1)] + [(a, 1) for a in OFFICIAL] + [(a, v) for a, v, _ in CORE_ROWS]))
    check("fresh: schema_migrations has (addon, version, name, applied_at)",
          [r[1] for r in sql(path, "PRAGMA table_info(schema_migrations)")] == ["addon", "version", "name", "applied_at"])
    check("fresh: no snapshot of an empty database", snapshots(path) == [], str(snapshots(path)))
    check("fresh: running again is a no-op", db.migrate(path) == [] and db.pending_migrations(path) == [])

    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('u1', 'U', 'admin', 'x')")
    conn.execute("INSERT INTO phone_calls (id, user_id, filename, call_timestamp, transcript) "
                 "VALUES ('c1', 'u1', 'c1.m4a', 'x', 'hello x')")
    conn.commit()
    hits = conn.execute("SELECT id FROM phone_calls_fts WHERE phone_calls_fts MATCH 'hello'").fetchall()
    conn.close()
    check("fresh: the FTS triggers keep the call index current", hits == [("c1",)], str(hits))


# --- Converting a Dana 9.0 database ------------------------------------------------

def _renamed(data: dict) -> dict:
    """The 9.0 data as old migration 34 leaves it: tables renamed, ``core`` connections → ``essentials``."""
    out = {}
    for t, rows in data.items():
        if t == "sqlite_sequence":
            rows = sorted((RENAMED.get(n, n), s) for n, s in rows)
        elif t in RENAMED:
            rows = sorted(tuple("'essentials'" if v == "'core'" else v for v in r) for r in rows)
        out[RENAMED.get(t, t)] = rows
    return out


def test_convert_9_0():
    fresh = new_path("fresh2.sqlite3")
    db.migrate(fresh)
    path = new_path("prod.sqlite3")
    load_fixture(path)
    before = dump_data(path)
    check("9.0 fixture: at version 33", max(r[0] for r in sql(path, "SELECT version FROM schema_migrations")) == 33)
    check("9.0 fixture: pending = every migration but the baselines it is marked with (core's later ones)",
          [m.label for m in db.pending_migrations(path)] == CORE_LABELS, str(db.pending_migrations(path)))
    applied = db.migrate(path)
    check("9.0: only core's later migrations run after the conversion (the baselines are marked, not run)",
          applied == CORE_LABELS, str(applied))
    check("9.0: schema_migrations marks core's and every official addon's baseline, and core's later ones",
          sorted(sql(path, "SELECT addon, version, name FROM schema_migrations"))
          == sorted([("core", 1, "baseline")] + [(a, 1, "baseline") for a in OFFICIAL] + CORE_ROWS))
    a, b = shape(fresh), shape(path)
    diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    check("9.0: same schema as a fresh database (tables, ordered columns, indexes, FKs, triggers)", not diff, str(diff))
    check("9.0: the same sqlite_master SQL as a fresh database (whitespace aside)",
          normalized(fresh) == normalized(path), str(sorted(normalized(fresh) ^ normalized(path))[:4]))
    after = dump_data(path)
    expected = {t: rows for t, rows in _renamed(before).items() if t not in DROPPED}
    expected["sqlite_sequence"] = [r for r in expected["sqlite_sequence"] if r[0] not in DROPPED]
    expected["addon_state"] = []  # core m0002: the server-wide disabled addons
    # core m0004 adds users.languages / users.currency, empty (the server's defaults apply)
    expected["users"] = sorted(tuple(r) + ("''", "''") for r in expected["users"])
    changed = sorted(t for t in set(expected) | set(after) if expected.get(t) != after.get(t))
    check("9.0: every row kept with every value (FTS and sqlite_sequence included)", not changed, str(changed))
    check("9.0: the fixture's rows are there", after["users"] and after["tasks"] and after["phone_calls"])
    snaps = snapshots(path)
    check("9.0: a snapshot was taken before converting (and one before core's later migrations)",
          snaps[:1] == ["prod-before-addons-from-0033.sqlite3"] and len(snaps) == 2, str(snaps))
    if snaps:
        snap = os.path.join(os.path.dirname(path), "snapshots", snaps[0])
        check("9.0: the snapshot is the database as it was", dump_data(snap) == before
              and max(r[0] for r in sql(snap, "SELECT version FROM schema_migrations")) == 33)
    check("9.0: converting is idempotent (nothing to do, no new snapshot)",
          db.migrate(path) == [] and snapshots(path) == snaps and dump_data(path) == after)

    # At 34 (the last old migration already applied): only the table is rewritten.
    p34 = new_path("at34.sqlite3")
    load_fixture(p34)
    conn = sqlite3.connect(p34)
    for s in convert.M0034:
        conn.execute(s)
    conn.execute("INSERT INTO schema_migrations (version, name, applied_at) VALUES (34, 'connections', 'x')")
    conn.commit()
    conn.close()
    check("9.0 at 34: converts", db.migrate(p34) == CORE_LABELS
          and snapshots(p34)[:1] == ["at34-before-addons-from-0034.sqlite3"],
          str(snapshots(p34)))
    check("9.0 at 34: same schema and rows", shape(p34) == shape(path) and dump_data(p34) == after)


def test_core_connections_renamed():
    path = new_path("conn.sqlite3")
    load_fixture(path)
    uid = sql(path, "SELECT id FROM users")[0][0]
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO integrations (user_id, kind, credentials, settings, account, created_at, updated_at) "
                 "VALUES (?, 'core', '', '{}', '', 'x', 'x')", (uid,))
    conn.execute("INSERT INTO integrations (user_id, kind, credentials, settings, account, created_at, updated_at) "
                 "VALUES (?, 'tasks', '', '{}', '', 'x', 'x')", (uid,))
    conn.execute("INSERT INTO notifications (id, user_id, package, title, text, posted_at, content_hash, triage) "
                 "VALUES ('n-conv', ?, 'p', 't', 'x', 'x', 'x', '{\"ron_wrote_it\": true}')", (uid,))
    conn.commit()
    conn.close()
    db.migrate(path)
    check("9.0 at 33: integrations are connections, core's is essentials",
          sorted(sql(path, "SELECT addon FROM connections")) == [("essentials",), ("tasks",)])
    check("9.0 at 33: the triage names the owner, not a name",
          sql(path, "SELECT triage FROM notifications WHERE id = 'n-conv'") == [('{"owner_wrote_it": true}',)])


def test_older_database():
    path = new_path("old.sqlite3")
    load_fixture(path)
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM schema_migrations WHERE version = 33")
    conn.commit()
    conn.close()
    before = schema_objects(path)
    msg = refused(path)
    check("older than 9.0 (at 32): refused, says to start Dana 9.0.0 first", "Dana 9.0.0 first" in msg, msg)
    check("older than 9.0: nothing changed, no snapshot", schema_objects(path) == before and snapshots(path) == [])

    path = new_path("ancient.sqlite3")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL)")
    conn.commit()
    conn.close()
    msg = refused(path)
    check("before migrations existed: refused the same way", "Dana 9.0.0 first" in msg, msg)
    check("before migrations existed: nothing changed", tables(path) == {"tasks"} and snapshots(path) == [])


def test_newer_database():
    path = new_path("newer-old.sqlite3")
    load_fixture(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO schema_migrations (version, name, applied_at) VALUES (35, 'future', 'x')")
    conn.commit()
    conn.close()
    check("old scheme past 34: refused as newer", "newer version" in refused(path))

    path = new_path("newer.sqlite3")
    db.migrate(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO schema_migrations (addon, version, name, applied_at) VALUES ('tasks', 9, 'future', 'x')")
    conn.commit()
    conn.close()
    msg = refused(path)
    check("an unknown migration of a present addon: refused as newer", "newer version" in msg and "tasks" in msg, msg)

    path = new_path("gone.sqlite3")
    db.migrate(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO schema_migrations (addon, version, name, applied_at) VALUES ('removed', 3, 'x', 'x')")
    conn.commit()
    conn.close()
    check("migrations of an addon no longer there: ignored", db.migrate(path) == [])


# --- Addon migrations added later ----------------------------------------------------

def test_addon_migrations_later():
    root = testenv.scratch_path("addon_dirs")
    os.environ["DANA_ADDON_DIRS"] = root
    try:
        path = new_path("later.sqlite3")
        db.migrate(path)
        write_addon(root, "notes_x", {"m0001_baseline.py": """
            VERSION = 1
            NAME = "baseline"

            def up(conn):
                conn.execute("CREATE TABLE notes_x (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, text TEXT NOT NULL)")
            """})
        check("a new addon: its first migration applies on the next start", db.migrate(path) == ["notes_x:0001_baseline"])
        check("a new addon: a snapshot before it", snapshots(path) == ["later-before-notes_x-0001.sqlite3"],
              str(snapshots(path)))
        write_addon(root, "notes_x", {"m0002_pinned.py": """
            VERSION = 2
            NAME = "pinned"

            def up(conn):
                conn.execute("ALTER TABLE notes_x ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
            """})
        check("an addon's m0002 added later: pending alone", [m.label for m in db.pending_migrations(path)]
              == ["notes_x:0002_pinned"])
        check("an addon's m0002 added later: applies alone", db.migrate(path) == ["notes_x:0002_pinned"])
        check("an addon's m0002: the column exists",
              "pinned" in [r[1] for r in sql(path, "PRAGMA table_info(notes_x)")])
        check("an addon's m0002: recorded", ("notes_x", 2) in db.applied_migrations(path))

        # A failing migration: its changes are rolled back, the next start applies it again.
        write_addon(root, "notes_x", {"m0003_tags.py": """
            VERSION = 3
            NAME = "tags"
            FAIL = True

            def up(conn):
                conn.execute("CREATE TABLE notes_x_tags (note_id INTEGER, tag TEXT)")
                if FAIL:
                    raise RuntimeError("disk on fire")
            """})
        try:
            db.migrate(path)
            raised = False
        except RuntimeError:
            raised = True
        check("failed migration: the error propagates", raised)
        check("failed migration: its changes are rolled back, not recorded",
              "notes_x_tags" not in tables(path) and ("notes_x", 3) not in db.applied_migrations(path))
        m3 = [m for m in db.pending_migrations(path) if m.version == 3][0]
        m3.module.FAIL = False
        check("failed migration: the next start applies it", db.migrate(path) == ["notes_x:0003_tags"])

        # Numbering is checked: a gap stops the start before anything runs.
        write_addon(root, "notes_x", {"m0005_gap.py": "VERSION = 5\nNAME = 'gap'\ndef up(conn):\n    pass\n"})
        try:
            db.migrate(path)
            gap = ""
        except RuntimeError as e:
            gap = str(e)
        check("a gap in an addon's numbers stops the start", "without gaps" in gap, gap)
        os.remove(os.path.join(root, "notes_x", "migrations", "m0005_gap.py"))
        check("folders: the extra addon is found", any(f.name == "notes_x" for f in addon_dirs.addon_folders()))
    finally:
        os.environ["DANA_ADDON_DIRS"] = ""


test_connection()
test_fresh()
test_convert_9_0()
test_core_connections_renamed()
test_older_database()
test_newer_database()
test_addon_migrations_later()

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
if passed != len(results):
    sys.exit(1)
