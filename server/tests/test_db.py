"""Checks for core/db: connection, ordered migrations, the 8.1.0 baseline. Run: bin/test test_db

Every database here is a fresh temp file; ``tests/fixtures/db_8_1_0.sql`` stands
for the production database before migrations existed.
"""

import ast
import json
import os
import sqlite3
import sys

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("db_test")

from core import db
from core.db.migrations import MIGRATIONS, m0001_baseline, m0002_users, m0019_whatsapp_account_claims
from unittest.mock import patch

FIXTURE = os.path.join(testenv.FIXTURES, "db_8_1_0.sql")
ALL = [f"{m.VERSION:04d}_{m.NAME}" for m in MIGRATIONS]
# The migrations before m0034 (integrations → connections): the tests of older migrations stop there.
BEFORE_CONNECTIONS = tuple(m for m in MIGRATIONS if m.VERSION < 34)
NEW_TABLES = {"schema_migrations", "users", "user_tokens", "user_telegram", "connections", "connection_imports",
              "task_trigger_marks", "item_access", "shopping_lists", "whatsapp_contacts", "uploaded_images",
              "connection_import_settings", "notification_digest_state", "shopping_history", "shopping_aisle_choices",
              "shopping_aisles", "shopping_invite_answers", "agent_sessions", "google_contacts",
              "google_contact_phones", "google_contacts_sync", "money_sources", "money_accounts", "money_transactions",
              "money_transactions_fts", "money_transactions_fts_data", "money_transactions_fts_idx",
              "money_transactions_fts_content", "money_transactions_fts_docsize", "money_transactions_fts_config",
              "money_rules", "money_aliases", "money_syncs", "money_code_requests", "money_signals", "money_holdings",
              "money_account_values", "money_trades", "money_savings", "money_market_checks", "money_meals",
              "project_sections", "entry_files"}
# 8.1.0 tables m0020 drops (the operations log, the product catalog and its FTS index) and the one it rebuilds
# with other columns (checked on their own in test_shopping_remake).
DROPPED = {"shopping_operations", "catalog_sources", "catalog_stores", "catalog_imports", "catalog_products",
           "catalog_offers", "catalog_fts", "catalog_fts_data", "catalog_fts_idx", "catalog_fts_content",
           "catalog_fts_docsize", "catalog_fts_config"}
REMADE = {"shopping_items"}
# Tables m0003 / m0005 / m0007 / m0008 / m0009 / m0010 / m0013 / m0014 / m0015 give an owner (rebuilt where a
# constraint changes):
# user_id, tasks.created_by / entries.created_by, and the shopping list of items / operations (list_id).
USER_SCOPED = {"sessions", "thread_messages", "shortcuts", "usage_events", "usage_daily", "task_groups", "tasks",
               "shopping_items", "shopping_operations", "projects", "entries", "about_facts", "about_state", "people",
               "person_phones", "phone_devices", "phone_events", "notifications", "notification_rules",
               "notification_summaries", "notification_rule_declines", "notification_action_requests", "phone_calls",
               "call_suggestions", "now_hidden", "briefings", "weekly_reviews", "wake_log"}
# Self-check marks m0013 / m0015 give to the first admin (``<mark>`` → ``<mark>:<user id>``, values kept).
OWNED_MARKS = ("push", "notification_sync", "wake_signal")
# The one row m0009 adds to a user-scoped table: the first admin owns the old memories.
ADDED_ROWS = {"about_state": {("'legacy_memories'", "'true'")}}
OWNER_COLUMNS = ("user_id", "created_by", "list_id", "url_set_by")
# Columns later migrations add to a user-scoped table with a default (m0023: what people take from Google contacts;
# m0033: the shared thread's "new topic" mark).
ADDED_COLUMNS = {"people": ("emails", "contact_resource"), "projects": ("target_date", "budget"),
                 "entries": ("section_id", "decision"), "sessions": ("cleared_seq",)}
USER_COLUMNS = ("user_id", "created_by", "url_set_by")  # owner columns that name a user

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


def tables(path: str) -> set:
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
    finally:
        conn.close()


def dump_data(path: str, only: set) -> dict:
    """Every row of every table in ``only`` (FTS contents included), as sorted tuples."""
    conn = sqlite3.connect(path)
    try:
        out = {}
        for t in sorted(only):
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info('{t}')")]
            out[t] = sorted(tuple(map(repr, row)) for row in conn.execute(f"SELECT {', '.join(cols)} FROM '{t}'"))
        out["sqlite_sequence"] = sorted(conn.execute("SELECT name, seq FROM sqlite_sequence").fetchall())
        return out
    finally:
        conn.close()


def without_user(path: str, only: set) -> tuple:
    """Rows of ``only`` with the owner columns (``user_id``, ``created_by``,
    ``list_id``) and the columns added since (:data:`ADDED_COLUMNS`) left out
    (same shape as :func:`dump_data`), and the set of users seen in
    ``user_id`` / ``created_by``."""
    conn = sqlite3.connect(path)
    try:
        out, owners = {}, set()
        for t in sorted(only):
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info('{t}')")]
            if not cols:
                continue  # a table a later migration dropped
            kept = [c for c in cols if c not in OWNER_COLUMNS + ADDED_COLUMNS.get(t, ())]
            out[t] = sorted(tuple(map(repr, row)) for row in conn.execute(f"SELECT {', '.join(kept)} FROM '{t}'"))
            for c in USER_COLUMNS:
                if c in cols:
                    owners |= {r[0] for r in conn.execute(f"SELECT DISTINCT {c} FROM '{t}'")}
        return out, owners
    finally:
        conn.close()


def admins(path: str) -> list:
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute("SELECT id FROM users WHERE access = 'admin' ORDER BY created_at")]
    finally:
        conn.close()


def schema_objects(path: str) -> set:
    conn = sqlite3.connect(path)
    try:
        return set(conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master"))
    finally:
        conn.close()


def shape(path: str, skip: set = frozenset()) -> dict:
    """Comparable schema: per table its columns in order, indexes, foreign keys;
    plus the trigger set."""
    conn = sqlite3.connect(path)
    try:
        out = {}
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for t in names:
            if t in skip:
                continue
            cols = [tuple(r[1:]) for r in conn.execute(f"PRAGMA table_info('{t}')")]
            idx = set()
            for r in conn.execute(f"PRAGMA index_list('{t}')"):
                icols = tuple(c[2] for c in conn.execute(f"PRAGMA index_info('{r[1]}')"))
                name = r[1] if not r[1].startswith("sqlite_autoindex_") else "auto"
                idx.add((name, r[2], r[3], r[4], icols))
            fks = sorted(tuple(r[2:5]) + (r[6],) for r in conn.execute(f"PRAGMA foreign_key_list('{t}')"))
            out[t] = (cols, idx, fks)
        out["__triggers__"] = sorted(conn.execute("SELECT name, tbl_name FROM sqlite_master WHERE type = 'trigger'"))
        out["__partial__"] = sorted(conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND sql LIKE '%WHERE%'"))
        return out
    finally:
        conn.close()


def snapshots(path: str) -> list:
    d = os.path.join(os.path.dirname(path), "snapshots")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


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
    check("connection: migrations numbered 1..n in order",
          [m.VERSION for m in MIGRATIONS] == list(range(1, len(MIGRATIONS) + 1)))


# --- Fresh database -----------------------------------------------------------------

def test_fresh():
    path = new_path("fresh.sqlite3")
    applied = db.migrate(path)
    check("fresh: every migration applied in order", applied == ALL, str(applied))
    check("fresh: recorded in schema_migrations", db.applied_migrations(path) == [m.VERSION for m in MIGRATIONS])
    fixture = new_path("ref.sqlite3")
    load_fixture(fixture)
    missing = tables(fixture) - tables(path) - DROPPED
    check("fresh: every 8.1.0 table exists (FTS included) but the ones m0020 drops", not missing, str(missing))
    check("fresh: the dropped tables are gone", not (DROPPED & tables(path)), str(DROPPED & tables(path)))
    check("fresh: the users tables exist", NEW_TABLES <= tables(path))
    check("fresh: no snapshot of an empty database", snapshots(path) == [], str(snapshots(path)))
    conn = sqlite3.connect(path)
    try:
        conn.execute("INSERT INTO phone_calls (id, filename, call_timestamp, title, summary) "
                     "VALUES ('x', 'f.m4a', '2026-01-01', 'Call with Dana', 'about the boiler')")
        conn.execute("UPDATE phone_calls SET summary = 'about the gate' WHERE id = 'x'")
        hits = conn.execute("SELECT id FROM phone_calls_fts WHERE phone_calls_fts MATCH 'gate'").fetchall()
        stale = conn.execute("SELECT id FROM phone_calls_fts WHERE phone_calls_fts MATCH 'boiler'").fetchall()
        conn.execute("INSERT INTO projects (id, title) VALUES ('p', 'P')")
        conn.execute("INSERT INTO entries (id, project_id, title, body) VALUES ('e', 'p', 'Seeds', 'tomato')")
        e_hits = conn.execute("SELECT id FROM entries_fts WHERE entries_fts MATCH 'tomato'").fetchall()
    finally:
        conn.close()
    check("fresh: FTS triggers keep the call index current", hits == [("x",)] and stale == [], f"{hits} {stale}")
    check("fresh: FTS triggers on entries", e_hits == [("e",)])
    check("fresh: running again is a no-op", db.migrate(path) == [])


# --- 8.1.0 database -----------------------------------------------------------------

def test_upgrade_8_1_0():
    path = new_path("prod.sqlite3")
    load_fixture(path)
    before_tables = tables(path)
    before_data = dump_data(path, before_tables)
    before_schema = schema_objects(path)

    applied = db.migrate(path)
    check("8.1.0: every migration applied", applied == ALL, str(applied))
    check("8.1.0: recorded in schema_migrations", db.applied_migrations(path) == [m.VERSION for m in MIGRATIONS])
    check("8.1.0: m0020 dropped the operations log and the catalog", not (DROPPED & tables(path)))
    after_data = dump_data(path, before_tables - USER_SCOPED - DROPPED)
    admin = (admins(path) or [""])[0]
    after_data["system_marks"] = sorted(
        tuple([repr(next((m for m in OWNED_MARKS if ast.literal_eval(r[0]) == f"{m}:{admin}"), ast.literal_eval(r[0])))] + list(r[1:]))
        for r in after_data["system_marks"])
    changed = [t for t in after_data if before_data[t] != after_data[t]]
    check("8.1.0: no existing row changed, added or removed (FTS included)", not changed, str(changed))
    kept_scoped = USER_SCOPED - DROPPED - REMADE
    before_scoped, _ = without_user(path.replace("prod.sqlite3", "snapshots/prod-before-0001.sqlite3"), kept_scoped)
    after_scoped, owners = without_user(path, kept_scoped)
    after_scoped = {t: [r for r in rows if r not in ADDED_ROWS.get(t, ())] for t, rows in after_scoped.items()}
    check("8.1.0: user-scoped rows kept with every value (seq included)", before_scoped == after_scoped,
          str([t for t in before_scoped if before_scoped[t] != after_scoped.get(t)]))
    check("8.1.0: the first admin is created for the existing rows", len(admins(path)) == 1, str(admins(path)))
    check("8.1.0: every existing session, thread message, phrase, usage row, task list, task, project, entry, About fact, About state row, person, phone number, phone device, push "
          "event, notification, rule, summary, decline, action request, call, suggestion, hidden Now card, "
          "briefing, weekly review and confirmed wake is the first admin's "
          "(tasks, "
          "entries: owner and creator)",
          owners == set(admins(path)[:1]), str(owners))
    check("8.1.0: sqlite_sequence kept for the rebuilt thread", dict(after_data["sqlite_sequence"]).get(
        "thread_messages") == dict(before_data["sqlite_sequence"]).get("thread_messages"))
    lost = before_schema - schema_objects(path)
    check("8.1.0: every existing table, index and trigger kept as it was (user-scoped tables rebuilt, m0020's "
          "dropped)", all(tbl in USER_SCOPED | DROPPED for _, _, tbl, _ in lost), str(sorted(lost)[:3]))
    check("8.1.0: only the new tables were added", tables(path) - before_tables == NEW_TABLES,
          str(tables(path) - before_tables))

    snaps = snapshots(path)
    check("8.1.0: a snapshot was taken before migrating", snaps == ["prod-before-0001.sqlite3"], str(snaps))
    if snaps:
        snap = os.path.join(os.path.dirname(path), "snapshots", snaps[0])
        check("8.1.0: the snapshot is the database as it was",
              dump_data(snap, before_tables) == before_data and "schema_migrations" not in tables(snap))

    mtime = os.path.getmtime(os.path.join(os.path.dirname(path), "snapshots", "prod-before-0001.sqlite3"))
    check("8.1.0: running again is a no-op", db.migrate(path) == [])
    check("8.1.0: no new snapshot when nothing is pending",
          snapshots(path) == snaps and os.path.getmtime(
              os.path.join(os.path.dirname(path), "snapshots", "prod-before-0001.sqlite3")) == mtime)

    fresh = new_path("fresh2.sqlite3")
    db.migrate(fresh)
    a, b = shape(fresh), shape(path)
    diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    check("schema: fresh and migrated 8.1.0 are equivalent (tables, ordered columns, indexes, FKs, triggers)",
          not diff, str(diff))


def test_upgrade_8_1_0_partial_use():
    """An 8.1.0 database that never used a lazily created feature, whose
    sessions table has no created_at, with a leftover table from an older
    version."""
    path = new_path("partial.sqlite3")
    load_fixture(path)
    conn = sqlite3.connect(path)
    for t in ("now_hidden", "usage_events", "usage_daily", "shortcuts"):
        conn.execute(f"DROP TABLE {t}")
    conn.execute("ALTER TABLE sessions DROP COLUMN created_at")  # 8.1.0 never managed to add it to old tables
    conn.execute("CREATE TABLE knowledge_items (id TEXT PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO knowledge_items VALUES ('k1', 'old')")
    conn.commit()
    conn.close()
    try:
        applied = db.migrate(path)
    except db.SchemaError as e:
        applied = str(e)
    check("8.1.0 without lazily created tables: migrates", applied == ALL, str(applied))
    check("8.1.0 without lazily created tables: they are created",
          {"now_hidden", "usage_events", "usage_daily", "shortcuts"} <= tables(path))
    conn = sqlite3.connect(path)
    kept = conn.execute("SELECT title FROM knowledge_items").fetchall()
    conn.close()
    check("legacy extra table is left alone", kept == [("old",)])


# --- Refused databases --------------------------------------------------------------

def _refused(path: str) -> str:
    try:
        db.migrate(path)
    except db.SchemaError as e:
        return str(e)
    return ""


def test_older_database():
    path = new_path("old.sqlite3")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE memories (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL)")
    conn.execute("INSERT INTO tasks VALUES ('t', 'keep me')")
    conn.commit()
    conn.close()
    before = schema_objects(path)
    msg = _refused(path)
    check("older DB: refused with a clear message", "older than Dana 8.1.0" in msg and "8.1.0" in msg, msg)
    check("older DB: the message names what is missing", "table notifications" in msg and "more" in msg, msg)
    check("older DB: nothing was changed", schema_objects(path) == before and "schema_migrations" not in tables(path))
    check("older DB: no snapshot taken", snapshots(path) == [])

    path = new_path("old2.sqlite3")
    load_fixture(path)
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE notifications DROP COLUMN actions")
    conn.commit()
    conn.close()
    msg = _refused(path)
    check("8.1.0 minus one column: refused, naming it", "column notifications.actions" in msg, msg)


def test_newer_database():
    path = new_path("newer.sqlite3")
    db.migrate(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO schema_migrations (version, name, applied_at) VALUES (999, 'future', 'x')")
    conn.commit()
    conn.close()
    msg = _refused(path)
    check("newer DB (unknown migration): refused", "newer version" in msg, msg)


def test_failed_migration_rolls_back():
    path = new_path("fail.sqlite3")
    original = m0002_users.up

    def boom(conn):
        original(conn)
        raise RuntimeError("disk on fire")

    m0002_users.up = boom
    try:
        try:
            db.migrate(path)
            raised = False
        except RuntimeError:
            raised = True
    finally:
        m0002_users.up = original
    check("failed migration: the error propagates", raised)
    check("failed migration: its changes are rolled back, earlier ones kept",
          db.applied_migrations(path) == [1] and "users" not in tables(path) and "tasks" in tables(path))
    check("failed migration: the next start applies it", db.migrate(path) == ALL[1:])


def test_user_scope_goes_to_existing_admin():
    """Users already exist (m0002 applied, people added) when m0003 runs: the
    rows go to the first admin, not a member, and no user is created."""
    path = new_path("scoped.sqlite3")
    load_fixture(path)
    full = db.MIGRATIONS
    db.MIGRATIONS = full[:2]
    try:
        db.migrate(path)
    finally:
        db.MIGRATIONS = full
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('m1', 'Member', 'member', '2026-01-01')")
    conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('a1', 'Admin', 'admin', '2026-01-02')")
    conn.commit()
    conn.close()
    check("user scope: only m0003 is pending", db.migrate(path) == ALL[2:])
    _, owners = without_user(path, USER_SCOPED)
    conn = sqlite3.connect(path)
    n_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    check("user scope: rows go to the existing admin", owners == {"a1"}, str(owners))
    check("user scope: no user created when one exists", n_users == 2)

    path = new_path("noadmin.sqlite3")
    load_fixture(path)
    db.MIGRATIONS = full[:2]
    try:
        db.migrate(path)
    finally:
        db.MIGRATIONS = full
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('m1', 'Member', 'member', '2026-01-01')")
    conn.commit()
    conn.close()
    try:
        db.migrate(path)
        refused = False
    except Exception:
        refused = True
    check("user scope: users but no admin is refused (nobody to give the rows to)", refused)
    check("user scope: the refused migration left the tables alone",
          db.applied_migrations(path) == [1, 2] and "user_id" not in
          [r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info('sessions')")])

    fresh = new_path("fresh3.sqlite3")
    db.migrate(fresh)
    conn = sqlite3.connect(fresh)
    n_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    check("user scope: a fresh database gets no user from the migration (bootstrap makes it)", n_users == 0)


def test_integrations_table():
    """m0004 on the 8.1.0 fixture: empty integrations tables, one row per
    (user, kind), rows go with their user."""
    path = new_path("integrations.sqlite3")
    load_fixture(path)
    with patch.object(db, "MIGRATIONS", BEFORE_CONNECTIONS):
        db.migrate(path)
    conn = db.get_db_connection(path)
    try:
        check("integrations: nothing is connected by the migration",
              conn.execute("SELECT COUNT(*) FROM integrations").fetchone()[0] == 0
              and conn.execute("SELECT COUNT(*) FROM integration_imports").fetchone()[0] == 0)
        cols = [r[1] for r in conn.execute("PRAGMA table_info('integrations')")]
        check("integrations: columns", cols == ["user_id", "kind", "credentials", "settings", "account",
                                                "created_at", "updated_at", "account_conflict"], str(cols))
        uid = conn.execute("SELECT id FROM users LIMIT 1").fetchone()[0]
        conn.execute("INSERT INTO integrations (user_id, kind, created_at, updated_at) VALUES (?, 'tasks', 'x', 'x')",
                     (uid,))
        try:
            conn.execute("INSERT INTO integrations (user_id, kind, created_at, updated_at) "
                         "VALUES (?, 'tasks', 'y', 'y')", (uid,))
            unique = False
        except sqlite3.IntegrityError:
            unique = True
        check("integrations: one row per (user, kind)", unique)
        check("integrations: settings default to an empty JSON object",
              conn.execute("SELECT settings FROM integrations WHERE user_id = ?", (uid,)).fetchone()[0] == "{}")
        for t in ("sessions", "thread_messages", "shortcuts", "usage_events", "usage_daily", "tasks", "task_groups"):
            conn.execute(f"DELETE FROM {t} WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))
        check("integrations: removed with their user",
              conn.execute("SELECT COUNT(*) FROM integrations").fetchone()[0] == 0)
    finally:
        conn.close()


def test_tasks_per_user():
    """m0005 on the 8.1.0 fixture: lists and tasks go to the first admin with
    their ids; names are unique per user; trigger marks move out of memories."""
    path = new_path("tasks.sqlite3")
    load_fixture(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO memories (key, value, category) VALUES ('last_trigger_v2_time_t1', '2026-11-01T08:00:00+00:00', 'system')")
    conn.execute("INSERT INTO memories (key, value, category) VALUES ('last_event_v2_location_home', '2026-11-02T08:00:00+00:00', 'system')")
    before_groups = conn.execute("SELECT id, name FROM task_groups ORDER BY id").fetchall()
    before_tasks = conn.execute("SELECT id, group_id FROM tasks ORDER BY id").fetchall()
    conn.commit()
    conn.close()
    db.migrate(path)
    conn = db.get_db_connection(path)
    try:
        admin = conn.execute("SELECT id FROM users WHERE access = 'admin'").fetchone()[0]
        groups = conn.execute("SELECT id, name, user_id FROM task_groups ORDER BY id").fetchall()
        check("tasks: every list kept with its id and name, owned by the first admin",
              [(g[0], g[1]) for g in groups] == [tuple(g) for g in before_groups] and {g[2] for g in groups} == {admin})
        tasks = conn.execute("SELECT id, group_id, user_id, created_by FROM tasks ORDER BY id").fetchall()
        check("tasks: every task kept in its list, owned and created by the first admin",
              [(t[0], t[1]) for t in tasks] == [tuple(t) for t in before_tasks]
              and all(t[2] == admin and t[3] == admin for t in tasks))
        marks = conn.execute("SELECT user_id, key, at FROM task_trigger_marks ORDER BY key").fetchall()
        check("tasks: trigger marks moved to the first admin, with their keys and times",
              [tuple(m) for m in marks] == [(admin, "last_event_v2_location_home", "2026-11-02T08:00:00+00:00"),
                                            (admin, "last_trigger_v2_time_t1", "2026-11-01T08:00:00+00:00")], str(marks))
        check("tasks: no trigger mark left in memories",
              not conn.execute("SELECT 1 FROM memories WHERE key LIKE 'last_%_v2_%'").fetchone()
              and conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 2)
        conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('m1', 'Member', 'member', 'z')")
        conn.execute("INSERT INTO task_groups (id, user_id, name) VALUES ('m1-home', 'm1', 'Home')")
        try:
            conn.execute("INSERT INTO task_groups (id, user_id, name) VALUES ('m1-home2', 'm1', 'Home')")
            unique = False
        except sqlite3.IntegrityError:
            unique = True
        check("tasks: a list name is unique per user, not on the whole server", unique)
        try:
            conn.execute("INSERT INTO tasks (id, title) VALUES ('orphan', 'no owner')")
            owned = False
        except sqlite3.IntegrityError:
            owned = True
        check("tasks: a task without an owner is refused (foreign key)", owned)
        conn.rollback()
    finally:
        conn.close()

    # Lists but no conversations, and no user yet: the migration makes the first user to own them.
    path = new_path("tasks_nouser.sqlite3")
    load_fixture(path)
    conn = sqlite3.connect(path)
    for t in ("sessions", "thread_messages", "shortcuts", "usage_events", "usage_daily"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    conn.close()
    db.migrate(path)
    conn = sqlite3.connect(path)
    try:
        owners = {r[0] for r in conn.execute("SELECT DISTINCT user_id FROM task_groups")}
        check("tasks: with lists but no user, the first admin is created to own them",
              len(admins(path)) == 1 and owners == set(admins(path)), str(owners))
    finally:
        conn.close()


def test_shopping_lists():
    """m0007 + m0020 on the 8.1.0 fixture: the one implicit shopping list
    becomes the first admin's list; items keep their ids, names, checked
    state and order (``seq``), ``added_by`` becomes the owner, a bought item
    gets ``checked_at``; names are unique per user; an item without a list is
    refused; a user's list and its rows go with them."""
    path = new_path("shopping.sqlite3")
    load_fixture(path)
    conn = sqlite3.connect(path)
    before_items = conn.execute("SELECT id, name, checked FROM shopping_items ORDER BY position, created_at").fetchall()
    conn.close()
    db.migrate(path)
    conn = db.get_db_connection(path)
    try:
        admin = conn.execute("SELECT id FROM users WHERE access = 'admin'").fetchone()[0]
        lists = [tuple(r) for r in conn.execute("SELECT id, user_id, name, version FROM shopping_lists")]
        check("shopping: one list, the first admin's, named 'Shopping list', version 0",
              len(lists) == 1 and lists[0][1:] == (admin, "Shopping list", 0), str(lists))
        list_id = lists[0][0] if lists else None
        items = [dict(r) for r in conn.execute("SELECT * FROM shopping_items ORDER BY seq")]
        check("shopping: every item kept with its id, name, checked state and order, on the first admin's list",
              [(i["id"], i["name"], i["checked"]) for i in items] == [tuple(i) for i in before_items]
              and [i["seq"] for i in items] == [1, 2] and {i["list_id"] for i in items} == {list_id},
              str(items))
        check("shopping: the old added_by text becomes the list owner; aisle other; key, quantity, note set",
              all(i["added_by"] == admin and i["category"] == "other" and i["quantity"] == "" and i["note"] == ""
                  for i in items) and [i["name_key"] for i in items] == ["milk", "bread"], str(items))
        check("shopping: a bought item gets checked_at (it clears 12 hours later), one to buy none",
              items[1]["checked_at"] and items[0]["checked_at"] is None and items[1]["checked_by"] is None)
        check("shopping: created_at kept as ISO 8601 with its zone",
              items[0]["created_at"] == "2026-11-01T08:00:00+00:00", items[0]["created_at"])
        cols = [r[1] for r in conn.execute("PRAGMA table_info('shopping_items')")]
        check("shopping: the unused item columns are gone",
              not {"position", "metadata_json", "enrichment_json", "selected_offer", "source", "updated_at"} & set(cols),
              str(cols))
        conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('m1', 'Member', 'member', 'z')")
        conn.execute("INSERT INTO shopping_lists (id, user_id, name) VALUES ('l1', 'm1', 'Shopping list')")
        try:
            conn.execute("INSERT INTO shopping_lists (id, user_id, name) VALUES ('l2', 'm1', 'Shopping list')")
            unique = False
        except sqlite3.IntegrityError:
            unique = True
        check("shopping: a list name is unique per user (two users may both have 'Shopping list')", unique)
        try:
            conn.execute("INSERT INTO shopping_items (id, name, name_key, created_at) VALUES ('o', 'x', 'x', 'z')")
            owned = False
        except sqlite3.IntegrityError:
            owned = True
        check("shopping: an item without a list is refused (foreign key)", owned)
        conn.execute("INSERT INTO shopping_items (id, list_id, name, name_key, created_at) "
                     "VALUES ('mi', 'l1', 'Eggs', 'eggs', 'z')")
        conn.execute("INSERT INTO shopping_history (list_id, name_key, name, last_at) VALUES ('l1', 'eggs', 'Eggs', 'z')")
        conn.execute("INSERT INTO shopping_aisle_choices (list_id, name_key, category) VALUES ('l1', 'eggs', 'dairy')")
        conn.execute("INSERT INTO shopping_invite_answers (list_id, user_id, answer, answered_at) "
                     "VALUES (?, 'm1', 'dismiss', 'z')", (list_id,))
        conn.execute("UPDATE shopping_items SET checked_by = 'm1' WHERE id = 's1'")
        conn.execute("DELETE FROM users WHERE id = 'm1'")
        check("shopping: a user's list, its items, history, aisle choices and their answers go with their user",
              not any(conn.execute(f"SELECT 1 FROM {t} WHERE list_id = 'l1'").fetchone()
                      for t in ("shopping_items", "shopping_history", "shopping_aisle_choices"))
              and not conn.execute("SELECT 1 FROM shopping_lists WHERE id = 'l1'").fetchone()
              and not conn.execute("SELECT 1 FROM shopping_invite_answers").fetchone())
        check("shopping: another list's item they ticked stays (checked_by cleared)",
              tuple(conn.execute("SELECT id, checked_by FROM shopping_items WHERE id = 's1'").fetchone()) == ("s1", None))
        conn.rollback()
    finally:
        conn.close()

    path = new_path("shopping_fresh.sqlite3")
    db.migrate(path)
    conn = sqlite3.connect(path)
    try:
        check("shopping: a fresh database gets no list from the migration (connecting shopping makes it)",
              conn.execute("SELECT COUNT(*) FROM shopping_lists").fetchone()[0] == 0)
    finally:
        conn.close()


def test_shopping_remake_from_m0019():
    """m0020 on a database already at m0019 (two users' lists, operations, a
    stored Telegram group): items kept per list in their order, the
    operations log dropped, ``telegram_chat_id`` gone from the stored
    settings (``active_list`` kept), other addons' settings untouched."""
    path = new_path("m0019.sqlite3")
    with patch.object(db, "MIGRATIONS", MIGRATIONS[:19]):
        db.migrate(path)
    check("m0019 database: made", db.applied_migrations(path) == list(range(1, 20)))
    conn = db.get_db_connection(path)
    conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('a1', 'Admin', 'admin', '2026-01-01')")
    conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('m1', 'Bob', 'member', '2026-02-01')")
    conn.execute("INSERT INTO shopping_lists (id, user_id, name) VALUES ('la', 'a1', 'Shopping list')")
    conn.execute("INSERT INTO shopping_lists (id, user_id, name) VALUES ('lb', 'm1', 'Shopping list')")
    for item_id, list_id, name, checked, position in (("i3", "la", "חָלָב", 0, 5), ("i1", "la", "Eggs", 1, 2),
                                                      ("i2", "lb", "Coca-Cola", 0, 0)):
        conn.execute("INSERT INTO shopping_items (id, list_id, name, checked, position, source, added_by, "
                     "selected_offer) VALUES (?, ?, ?, ?, ?, 'telegram', '12345', '{\"price\": 3}')",
                     (item_id, list_id, name, checked, position))
    conn.execute("INSERT INTO shopping_operations (id, op_type, list_id, user_id) VALUES ('o1', 'add', 'la', 'a1')")
    for uid, kind, settings in (("a1", "shopping", {"telegram_chat_id": -100123, "active_list": "lb"}),
                                ("m1", "shopping", {}), ("a1", "whatsapp", {"voice_telegram_chat_id": 111})):
        conn.execute("INSERT INTO integrations (user_id, kind, settings, created_at, updated_at) "
                     "VALUES (?, ?, ?, 'z', 'z')", (uid, kind, json.dumps(settings)))
    conn.commit()
    conn.close()
    applied = db.migrate(path)
    check("m0019 database: m0020 and later applied", applied == ALL[ALL.index("0020_shopping_remake"):], str(applied))
    conn = db.get_db_connection(path)
    try:
        items = [(r["id"], r["list_id"], r["name"], r["name_key"], r["checked"], r["seq"], r["added_by"])
                 for r in conn.execute("SELECT * FROM shopping_items ORDER BY list_id, seq")]
        check("m0019 database: items kept per list in their order, keys normalised, added_by the owner",
              items == [("i1", "la", "Eggs", "eggs", 1, 1, "a1"), ("i3", "la", "חָלָב", "חלב", 0, 2, "a1"),
                        ("i2", "lb", "Coca-Cola", "coca cola", 0, 1, "m1")], str(items))
        settings = {(r["user_id"], r["addon"]): json.loads(r["settings"]) for r in
                    conn.execute("SELECT user_id, addon, settings FROM connections")}
        check("m0019 database: telegram_chat_id gone from shopping settings, the rest kept",
              settings == {("a1", "shopping"): {"active_list": "lb"}, ("m1", "shopping"): {},
                           ("a1", "whatsapp"): {"voice_telegram_chat_id": 111}}, str(settings))
        check("m0019 database: the operations log and the catalog are gone", not (DROPPED & tables(path)))
    finally:
        conn.close()
    fresh = new_path("fresh_m0020.sqlite3")
    db.migrate(fresh)
    a, b = shape(fresh), shape(path)
    diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    check("schema: fresh and migrated-from-m0019 are equivalent", not diff, str(diff))


def test_knowledge_per_user():
    """m0008 on the 8.1.0 fixture: projects and entries go to the first admin
    with their ids; entries are owned and created by them; the FTS index is
    left exactly as it was (no re-indexing) and still finds the entries; a
    project or entry without an owner is refused."""
    path = new_path("knowledge.sqlite3")
    load_fixture(path)
    fts_tables = {t for t in tables(path) if t.startswith("entries_fts")}
    conn = sqlite3.connect(path)
    before_projects = conn.execute("SELECT id, title FROM projects ORDER BY id").fetchall()
    before_entries = conn.execute("SELECT id, project_id, title FROM entries ORDER BY id").fetchall()
    trigger_sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'entries_au'").fetchone()
    conn.close()
    before_fts = dump_data(path, fts_tables)
    db.migrate(path)
    conn = db.get_db_connection(path)
    try:
        admin = conn.execute("SELECT id FROM users WHERE access = 'admin'").fetchone()[0]
        projects = conn.execute("SELECT id, title, user_id FROM projects ORDER BY id").fetchall()
        check("knowledge: every project kept with its id and title, owned by the first admin",
              [(p[0], p[1]) for p in projects] == [tuple(p) for p in before_projects] and len(projects) == 1
              and {p[2] for p in projects} == {admin}, str([tuple(p) for p in projects]))
        entries = conn.execute("SELECT id, project_id, title, user_id, created_by FROM entries ORDER BY id").fetchall()
        check("knowledge: every entry kept in its project, owned and created by the first admin",
              [tuple(e)[:3] for e in entries] == [tuple(e) for e in before_entries] and len(entries) == 2
              and all(e[3] == admin and e[4] == admin for e in entries), str([tuple(e) for e in entries]))
        check("knowledge: the FTS index is byte-for-byte what it was (shadow tables included)",
              dump_data(path, fts_tables) == before_fts)
        check("knowledge: the FTS update trigger is back with its original SQL",
              conn.execute("SELECT sql FROM sqlite_master WHERE name = 'entries_au'").fetchone()[0] == trigger_sql[0])
        hits = [r[0] for r in conn.execute("SELECT id FROM entries_fts WHERE entries_fts MATCH 'tomato'")]
        check("knowledge: the FTS index still finds the migrated entries", hits == ["e1"], str(hits))
        conn.execute("UPDATE entries SET title = 'Cherry tomatoes' WHERE id = 'e1'")
        hits = [r[0] for r in conn.execute("SELECT id FROM entries_fts WHERE entries_fts MATCH 'cherry'")]
        check("knowledge: the FTS triggers still keep the index current", hits == ["e1"], str(hits))
        for sql, what in (("INSERT INTO projects (id, title) VALUES ('px', 'no owner')", "project"),
                          ("INSERT INTO entries (id, project_id, title) VALUES ('ex', 'p1', 'no owner')", "entry")):
            try:
                conn.execute(sql)
                refused = False
            except sqlite3.IntegrityError:
                refused = True
            check(f"knowledge: a {what} without an owner is refused (foreign key)", refused)
        conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('m1', 'Member', 'member', 'z')")
        conn.execute("INSERT INTO projects (id, title, user_id) VALUES ('mp', 'Mine', 'm1')")
        conn.execute("INSERT INTO entries (id, project_id, title, user_id, created_by) VALUES ('me', 'mp', 'x', 'm1', 'm1')")
        conn.execute("DELETE FROM users WHERE id = 'm1'")
        check("knowledge: a user's projects and entries go with their user (and leave the FTS index)",
              not conn.execute("SELECT 1 FROM projects WHERE id = 'mp'").fetchone()
              and not conn.execute("SELECT 1 FROM entries WHERE id = 'me'").fetchone()
              and not conn.execute("SELECT 1 FROM entries_fts WHERE id = 'me'").fetchone())
        conn.rollback()
    finally:
        conn.close()


def test_about_per_user():
    """m0009 on the 8.1.0 fixture: every About fact (active or not) and every
    About state row goes to the first admin with its id / key; the first admin
    is marked as the owner of the old memories; state keys are per user; a fact
    or state row without an owner is refused. A fresh database marks nobody."""
    path = new_path("about.sqlite3")
    load_fixture(path)
    conn = sqlite3.connect(path)
    before_facts = conn.execute("SELECT id, text, status, legacy_key FROM about_facts ORDER BY id").fetchall()
    before_state = conn.execute("SELECT key, value FROM about_state ORDER BY key").fetchall()
    conn.close()
    db.migrate(path)
    conn = db.get_db_connection(path)
    try:
        admin = conn.execute("SELECT id FROM users WHERE access = 'admin'").fetchone()[0]
        facts = conn.execute("SELECT id, text, status, legacy_key, user_id FROM about_facts ORDER BY id").fetchall()
        check("about: every fact kept with its id, text, status and legacy key, owned by the first admin",
              [tuple(f)[:4] for f in facts] == [tuple(f) for f in before_facts] and len(facts) == 2
              and {f[4] for f in facts} == {admin}, str([tuple(f) for f in facts]))
        state = conn.execute("SELECT user_id, key, value FROM about_state ORDER BY key").fetchall()
        kept = [(k, v) for u, k, v in state if k != "legacy_memories"]
        check("about: every state row (the learner's bookmark) kept, the first admin's",
              kept == [tuple(r) for r in before_state] and len(kept) == 1 and {r[0] for r in state} == {admin},
              str([tuple(r) for r in state]))
        check("about: the first admin owns the old memories (legacy_memories marker)",
              [tuple(r) for r in state if r[1] == "legacy_memories"] == [(admin, "legacy_memories", "true")])
        conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('m1', 'Member', 'member', 'z')")
        conn.execute("INSERT INTO about_state (user_id, key, value) VALUES ('m1', 'learn_bookmark', '{}')")
        check("about: a state key is per user (two users may both have a learner bookmark)",
              conn.execute("SELECT COUNT(*) FROM about_state WHERE key = 'learn_bookmark'").fetchone()[0] == 2)
        for sql, what in (("INSERT INTO about_facts (id, text, created_at, updated_at) VALUES ('fx', 'x', 'a', 'a')",
                           "fact"),
                          ("INSERT INTO about_state (user_id, key, value) VALUES ('nobody', 'k', 'v')", "state row")):
            try:
                conn.execute(sql)
                refused = False
            except sqlite3.IntegrityError:
                refused = True
            check(f"about: a {what} without an owner is refused (foreign key)", refused)
        conn.execute("INSERT INTO about_facts (id, user_id, text, created_at, updated_at) VALUES ('mf', 'm1', 'x', 'a', 'a')")
        conn.execute("DELETE FROM users WHERE id = 'm1'")
        check("about: a user's facts and state go with their user",
              not conn.execute("SELECT 1 FROM about_facts WHERE id = 'mf'").fetchone()
              and not conn.execute("SELECT 1 FROM about_state WHERE user_id = 'm1'").fetchone())
        conn.rollback()
    finally:
        conn.close()

    path = new_path("about_fresh.sqlite3")
    db.migrate(path)
    conn = sqlite3.connect(path)
    try:
        check("about: a fresh database marks nobody as owner of the old memories",
              conn.execute("SELECT COUNT(*) FROM about_state").fetchone()[0] == 0)
    finally:
        conn.close()


def test_people_per_user():
    """m0010 on the 8.1.0 fixture: every person (aliases, flags, brief and its
    signature included) and every phone number goes to the first admin with
    its id; links from calls / notifications are untouched; a number is unique
    per user, not across users; a person or number without an owner is
    refused; a user's people and numbers go with their user."""
    path = new_path("people.sqlite3")
    load_fixture(path)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE people SET brief = '{\"points\": [\"Hiking on Saturday\"], \"open\": []}', "
                 "brief_signature = 'sig1', brief_updated_at = '2026-11-02T08:00:00+00:00'")
    conn.commit()
    before_people = conn.execute("SELECT * FROM people ORDER BY id").fetchall()
    before_phones = conn.execute("SELECT phone, person_id, created_at FROM person_phones ORDER BY phone").fetchall()
    before_links = conn.execute("SELECT id, person_id FROM phone_calls UNION ALL "
                                "SELECT id, person_id FROM notifications ORDER BY 1").fetchall()
    conn.close()
    db.migrate(path)
    conn = db.get_db_connection(path)
    try:
        admin = conn.execute("SELECT id FROM users WHERE access = 'admin'").fetchone()[0]
        cols = [r[1] for r in conn.execute("PRAGMA table_info('people')")
                if r[1] not in ("user_id",) + ADDED_COLUMNS["people"]]
        people = conn.execute(f"SELECT {', '.join(cols)}, user_id FROM people ORDER BY id").fetchall()
        added = conn.execute("SELECT emails, contact_resource FROM people").fetchall()
        check("people: m0023's columns start empty (no emails, no Google contact)",
              [tuple(a) for a in added] == [("[]", "")], str([tuple(a) for a in added]))
        check("people: every person kept with its id, aliases, flags and cached brief + signature, owned by "
              "the first admin",
              [tuple(p)[:-1] for p in people] == [tuple(p) for p in before_people] and len(people) == 1
              and {p[-1] for p in people} == {admin} and people[0]["brief_signature"] == "sig1",
              str([tuple(p) for p in people]))
        phones = conn.execute("SELECT phone, person_id, created_at, user_id FROM person_phones ORDER BY phone").fetchall()
        check("people: every phone number kept with its person, the first admin's",
              [tuple(p)[:3] for p in phones] == [tuple(p) for p in before_phones] and len(phones) == 1
              and {p[3] for p in phones} == {admin}, str([tuple(p) for p in phones]))
        links = conn.execute("SELECT id, person_id FROM phone_calls UNION ALL "
                             "SELECT id, person_id FROM notifications ORDER BY 1").fetchall()
        check("people: calls and notifications keep their person links",
              [tuple(r) for r in links] == [tuple(r) for r in before_links])
        conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('m1', 'Member', 'member', 'z')")
        conn.execute("INSERT INTO people (id, user_id, name, created_at, updated_at) VALUES ('mp', 'm1', 'Sam', 'a', 'a')")
        conn.execute("INSERT INTO person_phones (user_id, phone, person_id, created_at) "
                     "VALUES ('m1', '15550100001', 'mp', 'a')")
        check("people: the same number may belong to a person of each user",
              conn.execute("SELECT COUNT(*) FROM person_phones WHERE phone = '15550100001'").fetchone()[0] == 2)
        try:
            conn.execute("INSERT INTO person_phones (user_id, phone, person_id, created_at) "
                         "VALUES ('m1', '15550100001', 'other', 'a')")
            unique = False
        except sqlite3.IntegrityError:
            unique = True
        check("people: a number is unique within one user", unique)
        for sql, what in (("INSERT INTO people (id, name, created_at, updated_at) VALUES ('px', 'x', 'a', 'a')", "person"),
                          ("INSERT INTO person_phones (user_id, phone, person_id, created_at) "
                           "VALUES ('nobody', '1234567', 'px', 'a')", "phone number")):
            try:
                conn.execute(sql)
                refused = False
            except sqlite3.IntegrityError:
                refused = True
            check(f"people: a {what} without an owner is refused (foreign key)", refused)
        conn.execute("DELETE FROM users WHERE id = 'm1'")
        check("people: a user's people and numbers go with their user",
              not conn.execute("SELECT 1 FROM people WHERE id = 'mp'").fetchone()
              and not conn.execute("SELECT 1 FROM person_phones WHERE user_id = 'm1'").fetchone())
        conn.rollback()
    finally:
        conn.close()


def test_whatsapp_per_user():
    """m0011 on the 8.1.0 fixture: the contact rows of the old memories (a
    WhatsApp chat id value, or a contact category / key) are copied to the
    first admin in their old order (no preferred contacts in the public code); other
    memories are not, and memories itself is untouched; image files already in
    uploads/images become the first admin's; rows go with their user."""
    path = new_path("whatsapp.sqlite3")
    load_fixture(path)
    conn = sqlite3.connect(path)
    conn.executemany("INSERT INTO memories (key, value, category, updated_at) VALUES (?, ?, ?, '2026-11-01 09:00:00')",
                     [("girlfriend_chat_id", "972500000301@c.us", "contact"),
                      ("family_group", "120363000000@g.us", "general"),
                      ("mom_phone", "0521112233", "family"),
                      ("plumber_contact", "Yossi 050", "general"),
                      ("wifi_password", "hunter2", "general")])
    conn.commit()
    before_memories = conn.execute("SELECT * FROM memories ORDER BY key").fetchall()
    conn.close()
    folder = os.path.join(os.path.dirname(path), "uploads", "images")
    os.makedirs(folder)
    for name in ("a1.jpg", "b2.png", ".hidden"):
        with open(os.path.join(folder, name), "wb") as f:
            f.write(b"x")
    db.migrate(path)
    conn = db.get_db_connection(path)
    try:
        admin = conn.execute("SELECT id FROM users WHERE access = 'admin'").fetchone()[0]
        rows = [tuple(r) for r in conn.execute(
            "SELECT user_id, key, value, category, updated_at FROM whatsapp_contacts ORDER BY id")]
        copied = [r for r in rows if r[3] != "preferred"]
        check("whatsapp: contact rows copied to the first admin in their old order, values kept",
              copied == [(admin, "girlfriend_chat_id", "972500000301@c.us", "contact", "2026-11-01 09:00:00"),
                         (admin, "family_group", "120363000000@g.us", "general", "2026-11-01 09:00:00"),
                         (admin, "mom_phone", "0521112233", "family", "2026-11-01 09:00:00"),
                         (admin, "plumber_contact", "Yossi 050", "general", "2026-11-01 09:00:00")], str(rows))
        check("whatsapp: no preferred contacts", not [r for r in rows if r[3] == "preferred"], str(rows))
        check("whatsapp: the old memories are left as they were",
              [tuple(r) for r in conn.execute("SELECT * FROM memories ORDER BY key")] == before_memories)
        check("whatsapp: existing image files are the first admin's (hidden files skipped)",
              [tuple(r) for r in conn.execute("SELECT name, user_id FROM uploaded_images ORDER BY name")]
              == [("a1.jpg", admin), ("b2.png", admin)])
        conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('m1', 'Member', 'member', 'z')")
        conn.execute("INSERT INTO whatsapp_contacts (user_id, key, value) VALUES ('m1', 'mom_phone', '1')")
        conn.execute("INSERT INTO uploaded_images (name, user_id, created_at) VALUES ('m.jpg', 'm1', 'z')")
        check("whatsapp: a key is unique per user, not across users",
              conn.execute("SELECT COUNT(*) FROM whatsapp_contacts WHERE key = 'mom_phone'").fetchone()[0] == 2)
        for sql, what in (("INSERT INTO whatsapp_contacts (user_id, key, value) VALUES ('nobody', 'k', 'v')", "contact"),
                          ("INSERT INTO uploaded_images (name, user_id, created_at) VALUES ('n.jpg', 'nobody', 'z')",
                           "image")):
            try:
                conn.execute(sql)
                refused = False
            except sqlite3.IntegrityError:
                refused = True
            check(f"whatsapp: a {what} without an owner is refused (foreign key)", refused)
        conn.execute("DELETE FROM users WHERE id = 'm1'")
        check("whatsapp: a user's contacts and images go with their user",
              not conn.execute("SELECT 1 FROM whatsapp_contacts WHERE user_id = 'm1'").fetchone()
              and not conn.execute("SELECT 1 FROM uploaded_images WHERE user_id = 'm1'").fetchone())
        conn.rollback()
    finally:
        conn.close()
    fresh = new_path("whatsapp_fresh.sqlite3")
    db.migrate(fresh)
    conn = sqlite3.connect(fresh)
    try:
        check("whatsapp: a fresh database has no contacts, no preferred contacts, no images",
              conn.execute("SELECT COUNT(*) FROM whatsapp_contacts").fetchone()[0] == 0
              and conn.execute("SELECT COUNT(*) FROM uploaded_images").fetchone()[0] == 0)
    finally:
        conn.close()


def _undo_m0012(conn) -> None:
    """Put a migrated database back to just before m0012 (to run it again on new data)."""
    conn.execute("DELETE FROM schema_migrations WHERE version = 12")
    conn.execute("DROP TABLE integration_import_settings")


def test_calendar_per_user():
    """m0012: the selected_calendars memory leaves memories and becomes the
    first admin's calendar setting: written into their integration when it
    exists, else waiting for the env import (integration_import_settings),
    dropped when the addon was imported and since disconnected or the value is
    not a list of ids; the calendar self-check mark becomes the admin's."""
    path = new_path("calendar.sqlite3")
    load_fixture(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO memories (key, value, category, updated_at) VALUES "
                 "('selected_calendars', '[\"primary\", \"work@group.calendar.google.com\"]', 'preferences', 'x')")
    conn.execute("INSERT INTO system_marks (key, last_at, ok, last_ok_at, last_fail_at, detail) "
                 "VALUES ('calendar', 't', 0, 'ok-at', 'fail-at', 'invalid_grant')")
    conn.commit()
    others = conn.execute("SELECT * FROM memories WHERE key != 'selected_calendars' ORDER BY key").fetchall()
    conn.close()
    with patch.object(db, "MIGRATIONS", BEFORE_CONNECTIONS):
        db.migrate(path)
    conn = db.get_db_connection(path)
    try:
        admin = conn.execute("SELECT id FROM users WHERE access = 'admin'").fetchone()[0]
        check("calendar: the selection leaves memories, the other memories stay",
              not conn.execute("SELECT 1 FROM memories WHERE key = 'selected_calendars'").fetchone()
              and [tuple(r) for r in conn.execute("SELECT * FROM memories ORDER BY key")] == [tuple(r) for r in others])
        check("calendar: no integration yet → the value waits for the env import",
              [tuple(r) for r in conn.execute("SELECT * FROM integration_import_settings")]
              == [("calendar", "selected_calendars", '["primary", "work@group.calendar.google.com"]')])
        check("calendar: the self-check mark becomes the first admin's, values kept",
              [tuple(r) for r in conn.execute("SELECT key, ok, last_ok_at, last_fail_at, detail FROM system_marks "
                                              "WHERE key LIKE 'calendar%'")]
              == [(f"calendar:{admin}", 0, "ok-at", "fail-at", "invalid_grant")])

        # The other order: the admin's calendar integration exists when m0012 runs.
        _undo_m0012(conn)
        conn.execute("INSERT INTO integrations (user_id, kind, credentials, settings, created_at, updated_at) "
                     "VALUES (?, 'calendar', 'token', '{\"calendar_id\": \"me@example.com\"}', 'x', 'x')", (admin,))
        conn.execute("INSERT INTO memories (key, value, category) VALUES ('selected_calendars', '[\"a\", \"b\"]', "
                     "'preferences')")
        conn.commit()
    finally:
        conn.close()
    with patch.object(db, "MIGRATIONS", BEFORE_CONNECTIONS):
        check("calendar: m0012 runs again on its own", db.migrate(path) == ["0012_calendar_per_user"])
    conn = db.get_db_connection(path)
    try:
        settings = json.loads(conn.execute("SELECT settings FROM integrations WHERE kind = 'calendar'").fetchone()[0])
        check("calendar: an existing integration gets the selection in its settings (others kept)",
              settings == {"calendar_id": "me@example.com", "selected_calendars": ["a", "b"]}, str(settings))
        check("calendar: nothing waits for the import then, the row is gone",
              conn.execute("SELECT COUNT(*) FROM integration_import_settings").fetchone()[0] == 0
              and not conn.execute("SELECT 1 FROM memories WHERE key = 'selected_calendars'").fetchone())

        # Imported once and disconnected since; then a malformed value with no integration.
        for setup, what in ((["DELETE FROM integrations WHERE kind = 'calendar'",
                              f"INSERT INTO integration_imports (kind, user_id, imported_at) VALUES ('calendar', '{admin}', 'x')",
                              "INSERT INTO memories (key, value, category) VALUES ('selected_calendars', '[\"a\"]', "
                              "'preferences')"], "the addon was imported and disconnected since"),
                            (["DELETE FROM integration_imports",
                              "INSERT INTO memories (key, value, category) VALUES ('selected_calendars', 'primary', "
                              "'preferences')"], "a value that is not a list of ids")):
            _undo_m0012(conn)
            for sql in setup:
                conn.execute(sql)
            conn.commit()
            conn.close()
            with patch.object(db, "MIGRATIONS", BEFORE_CONNECTIONS):
                db.migrate(path)
            conn = db.get_db_connection(path)
            check(f"calendar: {what} → dropped (nothing waits, the row is gone)",
                  conn.execute("SELECT COUNT(*) FROM integration_import_settings").fetchone()[0] == 0
                  and not conn.execute("SELECT 1 FROM memories WHERE key = 'selected_calendars'").fetchone())
    finally:
        conn.close()
    fresh = new_path("calendar_fresh.sqlite3")
    db.migrate(fresh)
    conn = sqlite3.connect(fresh)
    try:
        check("calendar: a fresh database waits for nothing",
              conn.execute("SELECT COUNT(*) FROM connection_import_settings").fetchone()[0] == 0)
    finally:
        conn.close()


def test_phone_per_user():
    """m0013 on the 8.1.0 fixture: devices, push events, notifications, rules,
    summaries, declines and action requests become the first admin's with
    every value kept (FTS untouched and still matching, push seq continuing);
    the digest's rows leave memories for the admin's digest state; the push /
    notification-sync marks become the admin's; the same notification can be
    stored once per user."""
    path = new_path("phone.sqlite3")
    load_fixture(path)
    conn = sqlite3.connect(path)
    conn.executemany("INSERT INTO memories (key, value, category, updated_at) VALUES (?, ?, 'system', 'x')",
                     [("notif_digest_0830", "2026-11-01"), ("notif_digest_last", "2026-11-01T08:30:00+02:00")])
    conn.execute("INSERT INTO system_marks (key, last_at, ok, last_ok_at, last_fail_at, detail) "
                 "VALUES ('notification_sync', 't', 1, 'ok-at', NULL, '3 new')")
    conn.commit()
    others = conn.execute("SELECT * FROM memories WHERE key NOT LIKE 'notif_digest_%' ORDER BY key").fetchall()
    push_mark = conn.execute("SELECT last_at, ok, last_ok_at, last_fail_at, detail FROM system_marks "
                             "WHERE key = 'push'").fetchone()
    fts_before = conn.execute("SELECT * FROM notifications_fts ORDER BY id").fetchall()
    conn.close()
    db.migrate(path)
    conn = db.get_db_connection(path)
    try:
        admin = conn.execute("SELECT id FROM users WHERE access = 'admin'").fetchone()[0]
        owners = {t: {r[0] for r in conn.execute(f"SELECT DISTINCT user_id FROM {t}")}
                  for t in ("phone_devices", "phone_events", "notifications", "notification_rules",
                            "notification_summaries", "notification_rule_declines", "notification_action_requests")}
        check("phone: every existing row is the first admin's",
              all(v == {admin} for v in owners.values()), str(owners))
        check("phone: notification ids and the FTS index kept (search still matches)",
              [r[0] for r in conn.execute("SELECT id FROM notifications ORDER BY id")] == ["n1", "n2"]
              and [tuple(r) for r in conn.execute("SELECT * FROM notifications_fts ORDER BY id")]
              == [tuple(r) for r in fts_before]
              and [r[0] for r in conn.execute("SELECT id FROM notifications_fts WHERE notifications_fts MATCH "
                                              "'\"Friday\"*'")] == ["n1"])
        check("phone: the digest state moves to the admin and leaves memories (other memories kept)",
              sorted(tuple(r) for r in conn.execute("SELECT user_id, slot, value FROM notification_digest_state"))
              == [(admin, "0830", "2026-11-01"), (admin, "last", "2026-11-01T08:30:00+02:00")]
              and [tuple(r) for r in conn.execute("SELECT * FROM memories ORDER BY key")] == [tuple(r) for r in others])
        check("phone: the push / notification-sync marks become the admin's, values kept",
              [tuple(r) for r in conn.execute("SELECT key, last_at, ok, last_ok_at, last_fail_at, detail "
                                              "FROM system_marks WHERE key LIKE 'push%' OR key LIKE 'notification_sync%' "
                                              "ORDER BY key")]
              == [(f"notification_sync:{admin}", "t", 1, "ok-at", None, "3 new"), (f"push:{admin}", *tuple(push_mark))])
        conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('u2', 'Bob', 'member', 'x')")
        h = conn.execute("SELECT content_hash FROM notifications WHERE id = 'n1'").fetchone()[0]
        conn.execute("INSERT INTO notifications (id, user_id, package, posted_at, content_hash) "
                     "VALUES ('b1', 'u2', 'com.example.chat', 'x', ?)", (h,))
        try:
            conn.execute("INSERT INTO notifications (id, user_id, package, posted_at, content_hash) "
                         "VALUES ('b2', 'u2', 'com.example.chat', 'x', ?)", (h,))
            dup = False
        except sqlite3.IntegrityError:
            dup = True
        check("phone: the same notification once per user (unique per user, not across users)", dup)
        conn.execute("INSERT INTO phone_events (type, data, created_at, expires_at, user_id) "
                     "VALUES ('reminder', '{}', 'x', 'y', 'u2')")
        check("phone: push seq continues after the existing events (nothing renumbered)",
              [tuple(r) for r in conn.execute("SELECT seq, user_id FROM phone_events ORDER BY seq")]
              == [(1, admin), (2, "u2")])
        try:
            conn.execute("INSERT INTO phone_devices (device_id, token) VALUES ('nobody', 't')")
            orphan = False
        except sqlite3.IntegrityError:
            orphan = True
        check("phone: a device without an owner is refused (foreign key)", orphan)
        conn.rollback()
    finally:
        conn.close()


def test_calls_per_user():
    """m0014 on the 8.1.0 fixture: every call and call suggestion becomes the
    first admin's with every value kept (the FTS index untouched and still
    matching); a notification's suggestion goes to the notification's user, a
    suggestion whose notification is gone to the first admin; a call or a
    suggestion without an owner is refused; they go with their user."""
    path = new_path("calls.sqlite3")
    load_fixture(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO call_suggestions (id, call_id, kind, payload, status, source_type) "
                 "VALUES ('cs-orphan', 'gone', 'task', '{}', 'pending', 'notification')")
    conn.commit()
    fts_before = conn.execute("SELECT * FROM phone_calls_fts ORDER BY id").fetchall()
    calls_before = conn.execute("SELECT * FROM phone_calls ORDER BY id").fetchall()
    conn.close()
    # Up to m0013, then a second user's notification with a suggestion, then m0014.
    db.MIGRATIONS = MIGRATIONS[:13]
    try:
        db.migrate(path)
    finally:
        db.MIGRATIONS = MIGRATIONS
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('u2', 'Bob', 'member', 'z')")
    conn.execute("INSERT INTO notifications (id, user_id, package, posted_at, content_hash) "
                 "VALUES ('bn1', 'u2', 'com.example.chat', 'x', 'h-bob')")
    conn.execute("INSERT INTO call_suggestions (id, call_id, kind, payload, status, source_type) "
                 "VALUES ('cs-bob', 'bn1', 'task', '{}', 'pending', 'notification')")
    conn.commit()
    conn.close()
    check("calls: m0014 applied after the others", db.migrate(path)[:1] == ["0014_calls_per_user"])
    conn = db.get_db_connection(path)
    try:
        admin = conn.execute("SELECT id FROM users WHERE access = 'admin'").fetchone()[0]
        cols = [r[1] for r in conn.execute("PRAGMA table_info('phone_calls')") if r[1] != "user_id"]
        calls = conn.execute(f"SELECT {', '.join(cols)}, user_id FROM phone_calls ORDER BY id").fetchall()
        check("calls: every call kept with every value, the first admin's",
              [tuple(c)[:-1] for c in calls] == [tuple(c) for c in calls_before] and len(calls) == 1
              and {c[-1] for c in calls} == {admin}, str([tuple(c) for c in calls]))
        check("calls: the FTS index kept as it was and still matching",
              [tuple(r) for r in conn.execute("SELECT * FROM phone_calls_fts ORDER BY id")] == [tuple(r) for r in fts_before]
              and [r[0] for r in conn.execute("SELECT id FROM phone_calls_fts WHERE phone_calls_fts MATCH 'ladder'")]
              == ["c1"])
        owners = dict(conn.execute("SELECT id, user_id FROM call_suggestions").fetchall())
        check("calls: a call's suggestion and one whose notification is gone are the first admin's, a "
              "notification's suggestion its user's",
              owners == {"cs1": admin, "cs2": admin, "cs-orphan": admin, "cs-bob": "u2"}, str(owners))
        for sql, what in (("INSERT INTO phone_calls (id, filename, call_timestamp) VALUES ('px', 'x', 'y')", "call"),
                          ("INSERT INTO call_suggestions (id, call_id) VALUES ('sx', 'c1')", "suggestion")):
            try:
                conn.execute(sql)
                refused = False
            except sqlite3.IntegrityError:
                refused = True
            check(f"calls: a {what} without an owner is refused (foreign key)", refused)
        conn.execute("INSERT INTO phone_calls (id, user_id, filename, call_timestamp) VALUES ('bc', 'u2', 'x', 'y')")
        conn.execute("DELETE FROM users WHERE id = 'u2'")
        check("calls: a user's calls and suggestions go with their user",
              not conn.execute("SELECT 1 FROM phone_calls WHERE id = 'bc'").fetchone()
              and not conn.execute("SELECT 1 FROM call_suggestions WHERE id = 'cs-bob'").fetchone())
        conn.rollback()
    finally:
        conn.close()


def test_composers_per_user():
    """m0015 on the 8.1.0 fixture: hidden Now cards, briefings, weekly reviews
    and confirmed wakes become the first admin's with every value kept; the
    old wake date leaves memories for the admin's wake log (as a dateless
    row, or not at all when that date is already logged); the wake-signal
    mark becomes the admin's; the same card / date / week can exist once per
    user; rows without an owner are refused; they go with their user."""
    path = new_path("composers.sqlite3")
    load_fixture(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO memories (key, value, category, updated_at) "
                 "VALUES ('last_wake_trigger_date', '2026-11-03', 'system', '2026-11-03 05:10:00')")
    conn.execute("INSERT INTO system_marks (key, last_at, ok, last_ok_at, last_fail_at, detail) "
                 "VALUES ('wake_signal', 'sig-at', 1, 'sig-at', NULL, 'unlock')")
    conn.commit()
    before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
              for t in ("now_hidden", "briefings", "weekly_reviews", "wake_log")}
    others = conn.execute("SELECT * FROM memories WHERE key != 'last_wake_trigger_date' ORDER BY key").fetchall()
    conn.close()
    applied = db.migrate(path)
    check("composers: m0015 applied", "0015_composers_per_user" in applied, str(applied))
    conn = db.get_db_connection(path)
    try:
        admin = conn.execute("SELECT id FROM users WHERE access = 'admin'").fetchone()[0]
        kept = {}
        for t in before:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info('{t}')") if r[1] != "user_id"]
            rows = conn.execute(f"SELECT {', '.join(cols)}, user_id FROM {t} ORDER BY 1").fetchall()
            kept[t] = ([tuple(r)[:-1] for r in rows], {r[-1] for r in rows})
        same = {t: kept[t][0] == [tuple(r) for r in before[t]] for t in ("now_hidden", "briefings", "weekly_reviews")}
        check("composers: hidden cards, briefings and weekly reviews kept with every value, the first admin's",
              all(same.values()) and all(kept[t][1] == {admin} for t in same), str(same))
        wake_rows = [tuple(r) for r in conn.execute("SELECT user_id, date, at, why FROM wake_log ORDER BY date")]
        check("composers: the wake log kept, plus the old wake date as a dateless row (the admin's)",
              wake_rows == [(admin, "2026-11-01", "2026-11-01T08:00:00+00:00", "phone alarm"),
                            (admin, "2026-11-03", "", "")], str(wake_rows))
        check("composers: the old wake date left memories (other memories kept)",
              [tuple(r) for r in conn.execute("SELECT * FROM memories ORDER BY key")] == [tuple(r) for r in others])
        check("composers: the wake-signal mark becomes the admin's, values kept",
              [tuple(r) for r in conn.execute("SELECT key, last_at, ok, detail FROM system_marks "
                                              "WHERE key LIKE 'wake_signal%'")]
              == [(f"wake_signal:{admin}", "sig-at", 1, "unlock")])
        conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('u2', 'Bob', 'member', 'z')")
        conn.execute("INSERT INTO now_hidden (user_id, item_id, until, reason, created_at) "
                     "VALUES ('u2', 'check:calls', 'x', 'snooze', 'y')")
        conn.execute("INSERT INTO briefings (user_id, date, headline, created_at) VALUES ('u2', '2026-11-01', 'B', 'x')")
        conn.execute("INSERT INTO wake_log (user_id, date, at) VALUES ('u2', '2026-11-01', 'x')")
        conn.execute("INSERT INTO weekly_reviews (id, user_id, week, created_at) VALUES ('wr-bob', 'u2', '2026-W44', 'x')")
        check("composers: the same card, date and week once per user",
              conn.execute("SELECT COUNT(*) FROM now_hidden WHERE item_id = 'check:calls'").fetchone()[0] == 2
              and conn.execute("SELECT COUNT(*) FROM briefings WHERE date = '2026-11-01'").fetchone()[0] == 2
              and conn.execute("SELECT COUNT(*) FROM wake_log WHERE date = '2026-11-01'").fetchone()[0] == 2)
        try:
            conn.execute("INSERT INTO wake_log (user_id, date, at) VALUES ('u2', '2026-11-01', 'again')")
            dup = False
        except sqlite3.IntegrityError:
            dup = True
        check("composers: one confirmed wake per user and date", dup)
        refused = []
        for sql in ("INSERT INTO now_hidden (item_id, until, created_at) VALUES ('x', 'y', 'z')",
                    "INSERT INTO briefings (date, headline, created_at) VALUES ('2026-12-01', 'h', 'x')",
                    "INSERT INTO wake_log (date, at) VALUES ('2026-12-01', 'x')",
                    "INSERT INTO weekly_reviews (id, week, created_at) VALUES ('wx', 'w', 'x')"):
            try:
                conn.execute(sql)
                refused.append(False)
            except sqlite3.IntegrityError:
                refused.append(True)
        check("composers: rows without an owner are refused", all(refused), str(refused))
        conn.execute("DELETE FROM users WHERE id = 'u2'")
        check("composers: a user's rows go with their user",
              not any(conn.execute(f"SELECT 1 FROM {t} WHERE user_id = 'u2'").fetchone()
                      for t in ("now_hidden", "briefings", "wake_log", "weekly_reviews")))
        conn.rollback()
    finally:
        conn.close()
    # An old wake date already in the log is not added twice.
    path = new_path("composers2.sqlite3")
    load_fixture(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO memories (key, value, category, updated_at) "
                 "VALUES ('last_wake_trigger_date', '2026-11-01', 'system', 'x')")
    conn.commit()
    conn.close()
    db.migrate(path)
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT date, at FROM wake_log").fetchall()
        gone = conn.execute("SELECT 1 FROM memories WHERE key = 'last_wake_trigger_date'").fetchone()
    finally:
        conn.close()
    check("composers: a wake date already logged keeps its time (no second row)",
          rows == [("2026-11-01", "2026-11-01T08:00:00+00:00")] and gone is None, str(rows))


def _profiles(path: str) -> list:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT access, name, context, pronoun, other_names, prompt_name FROM users "
                            "ORDER BY created_at, rowid").fetchall()
    finally:
        conn.close()


def test_user_profile():
    """m0016: the profile columns on users. On the 8.1.0 fixture the first
    admin gets what the prompts said about them (OWNER_CONTEXT or today's
    default, "he", "רובין", "the owner"); later users get '' / "they"; the
    pronoun is checked; a fresh database has the columns and no user."""
    from core import user_rows

    path = new_path("profile.sqlite3")
    load_fixture(path)
    applied = db.migrate(path)
    check("profile: m0016 applied", "0016_user_profile" in applied, str(applied))
    rows = _profiles(path)
    check("profile: the first admin gets today's prompt words (default context)",
          rows == [("admin", testenv.OWNER_NAME, testenv.OWNER_CONTEXT, "he", "רובין",
                    "the owner")], str(rows))
    conn = sqlite3.connect(path)
    try:
        conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('u2', 'Maya', 'member', '2030-01-01')")
        second = conn.execute("SELECT context, pronoun, other_names, prompt_name FROM users WHERE id = 'u2'").fetchone()
        try:
            conn.execute("UPDATE users SET pronoun = 'it' WHERE id = 'u2'")
            refused = False
        except sqlite3.IntegrityError:
            refused = True
        conn.commit()
    finally:
        conn.close()
    check("profile: a later user gets no context and 'they'", second == ("", "they", "", ""), str(second))
    check("profile: only he / she / they", refused)

    path = new_path("profile_env.sqlite3")
    load_fixture(path)
    os.environ["OWNER_NAME"] = "Ronnie Test"
    os.environ["OWNER_CONTEXT"] = "Robin is a test user."
    try:
        db.migrate(path)
    finally:
        os.environ.pop("OWNER_NAME", None)
        os.environ.pop("OWNER_CONTEXT", None)
    rows = _profiles(path)
    check("profile: OWNER_NAME / OWNER_CONTEXT honoured once, at the migration",
          rows == [("admin", "Ronnie Test", "Robin is a test user.", "he", "רובין", "the owner")], str(rows))

    path = new_path("profile_fresh.sqlite3")
    db.migrate(path)
    conn = sqlite3.connect(path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info('users')")}
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()
    check("profile: a fresh database has the columns and no user yet",
          {"context", "pronoun", "other_names", "prompt_name"} <= cols and count == 0)


def test_phone_devices_per_user():
    """m0017 on the 8.1.0 fixture: each device row keeps every value (owner,
    device id, token, timestamps) and the key becomes (user, device id), so a
    second user can register the same device id without touching the first
    user's row; a row still needs an owner."""
    path = new_path("devices.sqlite3")
    load_fixture(path)
    conn = sqlite3.connect(path)
    before = conn.execute("SELECT device_id, token, name, app_version, created_at, updated_at, "
                          "last_fetch_at, last_seq FROM phone_devices ORDER BY device_id").fetchall()
    conn.close()
    applied = db.migrate(path)
    check("devices: m0017 applied", "0017_phone_devices_per_user" in applied, str(applied))
    conn = db.get_db_connection(path)
    try:
        admin = conn.execute("SELECT id FROM users WHERE access = 'admin'").fetchone()[0]
        before = [(admin, *r) for r in before]
        after = conn.execute("SELECT user_id, device_id, token, name, app_version, created_at, updated_at, "
                             "last_fetch_at, last_seq FROM phone_devices ORDER BY device_id").fetchall()
        check("devices: every row kept with every value", bool(before)
              and [tuple(r) for r in after] == [tuple(r) for r in before], f"{before} -> {after}")
        pk = [r[1] for r in sorted((r for r in conn.execute("PRAGMA table_info(phone_devices)") if r[5]),
                                   key=lambda r: r[5])]
        check("devices: the key is (user_id, device_id)", pk == ["user_id", "device_id"], str(pk))
        conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('u2', 'Bob', 'member', 'x')")
        device = after[0][1]
        conn.execute("INSERT INTO phone_devices (user_id, device_id, token, created_at, updated_at) "
                     "VALUES ('u2', ?, 'bob-token', 'x', 'x')", (device,))
        rows = sorted(tuple(r) for r in conn.execute(
            "SELECT user_id, token FROM phone_devices WHERE device_id = ?", (device,)))
        check("devices: the same device id for a second user is a second row (the first untouched)",
              rows == sorted([(after[0][0], after[0][2]), ("u2", "bob-token")]), str(rows))
        try:
            conn.execute("INSERT INTO phone_devices (user_id, device_id, token, created_at, updated_at) "
                         "VALUES ('nobody', 'd', 't', 'x', 'x')")
            orphan = False
        except sqlite3.IntegrityError:
            orphan = True
        check("devices: a device of an unknown user is refused (foreign key)", orphan)
        conn.rollback()
    finally:
        conn.close()


def test_entry_url_set_by():
    """m0018 on the 8.1.0 fixture: every entry records who set its link — its
    creator (the first admin) — and nothing else changes; the FTS index is
    left as it was (checked byte for byte in test_knowledge_per_user, after
    every migration). A fresh database has the column."""
    path = new_path("url_set_by.sqlite3")
    load_fixture(path)
    conn = sqlite3.connect(path)
    before = conn.execute("SELECT * FROM entries ORDER BY id").fetchall()
    conn.close()
    applied = db.migrate(path)
    check("url_set_by: m0018 applied", "0018_entry_url_set_by" in applied, str(applied))
    conn = db.get_db_connection(path)
    try:
        admin = conn.execute("SELECT id FROM users WHERE access = 'admin'").fetchone()[0]
        rows = [dict(r) for r in conn.execute("SELECT * FROM entries ORDER BY id")]
        check("url_set_by: every existing entry's link set by its creator (the first admin)",
              bool(rows) and all(r["url_set_by"] == r["created_by"] == admin for r in rows), str(rows)[:300])
        old_cols = len(before[0]) if before else 0
        check("url_set_by: every other value kept",
              [tuple(r)[:old_cols] for r in before] == [tuple(r.values())[:old_cols] for r in rows])
    finally:
        conn.close()
    fresh = new_path("url_set_by_fresh.sqlite3")
    db.migrate(fresh)
    conn = sqlite3.connect(fresh)
    try:
        check("url_set_by: a fresh database has the column",
              "url_set_by" in {r[1] for r in conn.execute("PRAGMA table_info(entries)")})
    finally:
        conn.close()


def test_whatsapp_account_claims():
    """m0019: ``addons.account_conflict``; whatsapp connections holding
    the same account (number before '@' / ':') keep it for the first admin,
    the others move to the conflict column; every other row unchanged."""
    path = new_path("claims.sqlite3")
    # The state m0019 meets (a DB migrated up to before m0034, without its column, rows of a branch before it), then its body.
    with patch.object(db, "MIGRATIONS", BEFORE_CONNECTIONS):
        db.migrate(path)
    conn = db.get_db_connection(path)
    conn.execute("ALTER TABLE integrations DROP COLUMN account_conflict")
    conn.commit()
    try:
        conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('a1', 'Admin', 'admin', '2026-01-01')")
        conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('m1', 'Bob', 'member', '2025-01-01')")
        conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('m2', 'Carl', 'member', '2026-02-01')")
        for uid, account in (("m1", "972501111111:4@s.whatsapp.net"), ("a1", "972501111111@c.us"),
                             ("m2", "972502222222@c.us")):
            conn.execute("INSERT INTO integrations (user_id, kind, settings, account, created_at, updated_at) "
                         "VALUES (?, 'whatsapp', '{}', ?, '2026-03-01', '2026-03-01')", (uid, account))
        conn.execute("INSERT INTO integrations (user_id, kind, settings, account, created_at, updated_at) "
                     "VALUES ('m1', 'tasks', '{}', '972501111111@c.us', '2026-03-01', '2026-03-01')")
        before = sorted(tuple(r) for r in conn.execute("SELECT * FROM integrations"))
        m0019_whatsapp_account_claims.up(conn)
        rows = {(r["user_id"], r["kind"]): dict(r) for r in conn.execute("SELECT * FROM integrations")}
        check("claims: the admin keeps the account another connection also had",
              rows[("a1", "whatsapp")]["account"] == "972501111111@c.us"
              and rows[("a1", "whatsapp")]["account_conflict"] is None)
        check("claims: the other connection (device-suffixed id) moves to account_conflict",
              rows[("m1", "whatsapp")]["account"] is None
              and rows[("m1", "whatsapp")]["account_conflict"] == "972501111111:4@s.whatsapp.net")
        check("claims: a connection with its own account and other addons are unchanged",
              rows[("m2", "whatsapp")]["account"] == "972502222222@c.us"
              and rows[("m2", "whatsapp")]["account_conflict"] is None
              and rows[("m1", "tasks")]["account"] == "972501111111@c.us")
        unchanged = [b for b in before if (b[0], b[1]) != ("m1", "whatsapp")]
        check("claims: every other value kept", sorted(tuple(r.values())[:len(before[0])] for k, r in rows.items()
                                                    if k != ("m1", "whatsapp")) == unchanged)
        conn.rollback()
    finally:
        conn.close()


def test_item_access():
    """m0006: the sharing table; one row per (item type, item, user); level
    read / edit only; rows go with their user."""
    path = new_path("sharing.sqlite3")
    load_fixture(path)
    db.migrate(path)
    conn = db.get_db_connection(path)
    try:
        check("sharing: nothing is shared by the migration",
              conn.execute("SELECT COUNT(*) FROM item_access").fetchone()[0] == 0)
        conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('m1', 'Member', 'member', 'z')")
        conn.execute("INSERT INTO item_access (item_type, item_id, user_id, level, granted_by, granted_at) "
                     "VALUES ('task_list', 'home', 'm1', 'read', 'a', 'x')")
        try:
            conn.execute("INSERT INTO item_access (item_type, item_id, user_id, level, granted_by, granted_at) "
                         "VALUES ('task_list', 'home', 'm1', 'edit', 'a', 'y')")
            unique = False
        except sqlite3.IntegrityError:
            unique = True
        check("sharing: one row per (item type, item, user)", unique)
        try:
            conn.execute("INSERT INTO item_access (item_type, item_id, user_id, level, granted_by, granted_at) "
                         "VALUES ('task_list', 'work', 'm1', 'owner', 'a', 'y')")
            checked = False
        except sqlite3.IntegrityError:
            checked = True
        check("sharing: level is read or edit", checked)
        conn.execute("DELETE FROM users WHERE id = 'm1'")
        check("sharing: removed with their user", conn.execute("SELECT COUNT(*) FROM item_access").fetchone()[0] == 0)
    finally:
        conn.close()


def test_connections():
    """m0034: ``integrations`` → ``connections`` (``kind`` → ``addon``), the
    import tables likewise, the stored ``core`` addon → ``essentials``, and the
    triage answers ``ron_wrote_it`` / ``addressed_to_ron`` → ``owner_wrote_it`` /
    ``addressed_to_owner`` in stored notifications; other values untouched."""
    path = new_path("connections.sqlite3")
    with patch.object(db, "MIGRATIONS", BEFORE_CONNECTIONS):
        db.migrate(path)
    conn = db.get_db_connection(path)
    conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('a1', 'Admin', 'admin', '2026-01-01')")
    conn.execute("INSERT INTO integrations (user_id, kind, credentials, settings, account, created_at, updated_at) "
                 "VALUES ('a1', 'whatsapp', 'token', '{\"session\": \"x\"}', '9725@c.us', 'c', 'u')")
    conn.execute("INSERT INTO integrations (user_id, kind, created_at, updated_at) VALUES ('a1', 'core', 'c', 'u')")
    conn.execute("INSERT INTO integration_imports (kind, user_id, imported_at) VALUES ('whatsapp', 'a1', 'i')")
    conn.execute("INSERT INTO integration_import_settings (kind, name, value) VALUES ('calendar', 'selected', '[]')")
    triage = json.dumps({"bucket": "normal", "signals": {"ron_wrote_it": 0.1, "addressed_to_ron": 0.9,
                                                         "needs_action": 0.5}})
    for nid, tri in (("n1", triage), ("n2", None), ("n3", '{"bucket": "dismiss"}')):
        conn.execute("INSERT INTO notifications (id, user_id, package, posted_at, content_hash, triage) "
                     "VALUES (?, 'a1', 'pkg', 't', ?, ?)", (nid, nid, tri))
    conn.commit()
    conn.close()
    check("connections: only m0034 is pending", db.migrate(path) == ["0034_connections"])
    conn = db.get_db_connection(path)
    try:
        names = tables(path)
        check("connections: the tables are renamed",
              {"connections", "connection_imports", "connection_import_settings"} <= names
              and not names & {"integrations", "integration_imports", "integration_import_settings"})
        cols = [r[1] for r in conn.execute("PRAGMA table_info('connections')")]
        check("connections: columns (kind → addon)", cols == ["user_id", "addon", "credentials", "settings", "account",
                                                              "created_at", "updated_at", "account_conflict"], str(cols))
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' "
                                              "AND tbl_name = 'connections' AND sql IS NOT NULL")}
        check("connections: the (addon, account) index", indexes == {"idx_connections_addon_account"}, str(indexes))
        rows = sorted(tuple(r) for r in conn.execute("SELECT user_id, addon, credentials, settings, account FROM connections"))
        check("connections: rows kept, core → essentials",
              rows == [("a1", "essentials", None, "{}", None), ("a1", "whatsapp", "token", '{"session": "x"}', "9725@c.us")],
              str(rows))
        check("connections: the import tables keep their rows",
              [tuple(r) for r in conn.execute("SELECT addon, user_id FROM connection_imports")] == [("whatsapp", "a1")]
              and [tuple(r) for r in conn.execute("SELECT addon, name FROM connection_import_settings")]
              == [("calendar", "selected")])
        stored = {r[0]: r[1] for r in conn.execute("SELECT id, triage FROM notifications")}
        check("connections: triage answers renamed, nothing else changed",
              json.loads(stored["n1"]) == {"bucket": "normal", "signals": {"owner_wrote_it": 0.1, "addressed_to_owner": 0.9,
                                                                          "needs_action": 0.5}}
              and stored["n2"] is None and stored["n3"] == '{"bucket": "dismiss"}', str(stored))
        uid = "a1"
        for t in ("notifications",):
            conn.execute(f"DELETE FROM {t} WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))
        check("connections: still removed with their user",
              conn.execute("SELECT COUNT(*) FROM connections").fetchone()[0] == 0)
    finally:
        conn.close()


def test_baseline_check_is_exact_for_8_1_0():
    path = new_path("exact.sqlite3")
    load_fixture(path)
    conn = sqlite3.connect(path)
    try:
        check("baseline: an 8.1.0 database lacks nothing", m0001_baseline.missing_from(conn) == [],
              str(m0001_baseline.missing_from(conn)))
    finally:
        conn.close()


test_connection()
test_fresh()
test_upgrade_8_1_0()
test_upgrade_8_1_0_partial_use()
test_older_database()
test_newer_database()
test_failed_migration_rolls_back()
test_user_scope_goes_to_existing_admin()
test_integrations_table()
test_tasks_per_user()
test_item_access()
test_shopping_lists()
test_shopping_remake_from_m0019()
test_knowledge_per_user()
test_about_per_user()
test_people_per_user()
test_whatsapp_per_user()
test_calendar_per_user()
test_phone_per_user()
test_calls_per_user()
test_composers_per_user()
test_user_profile()
test_phone_devices_per_user()
test_entry_url_set_by()
test_whatsapp_account_claims()
test_connections()
test_baseline_check_is_exact_for_8_1_0()

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
if passed != len(results):
    sys.exit(1)
