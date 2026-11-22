"""Checks for core.db.ChangeWatch: commits by any connection or process seen at once, reads not counted. Run: bin/test test_db_watch

The watch the in-memory copies of users, devices, connections and disabled
addons check before each lookup: the first look counts as a change; a commit
by another connection (in this process or another one, as ``bin/dana`` does)
is seen by the next look, a read or a rolled-back write is not; the database
file replaced counts as a change; WAL mode works too; a look stays cheap.
Temp DB, no network.
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import time

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("db_watch_test")

from core import db
from core.paths import DATABASE_PATH

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


def write(sql: str, *args) -> None:
    conn = sqlite3.connect(str(DATABASE_PATH))
    conn.execute(sql, args)
    conn.commit()
    conn.close()


def test_rollback_journal() -> None:
    watch = db.ChangeWatch()
    check("no database yet: every look counts as a change", watch.changed() and watch.changed())
    write("CREATE TABLE t (v TEXT)")
    check("first look at the database: a change", watch.changed())
    check("nothing committed since: no change", not watch.changed())
    write("INSERT INTO t VALUES ('a')")
    check("a commit by another connection: seen by the next look", watch.changed())
    check("... once", not watch.changed())
    conn = sqlite3.connect(str(DATABASE_PATH))
    conn.execute("SELECT * FROM t").fetchall()
    conn.execute("INSERT INTO t VALUES ('b')")
    conn.rollback()
    conn.close()
    check("a read and a rolled-back write: no change", not watch.changed())
    subprocess.run([sys.executable, "-c", "import sqlite3, sys; c = sqlite3.connect(sys.argv[1]); "
                    "c.execute(\"INSERT INTO t VALUES ('c')\"); c.commit()", str(DATABASE_PATH)], check=True)
    check("a commit by another process: seen by the next look", watch.changed())
    other = db.ChangeWatch()
    other.changed()
    write("INSERT INTO t VALUES ('d')")
    check("every watch sees the commit (each keeps its own mark)", watch.changed() and other.changed())

    copy = str(DATABASE_PATH) + ".copy"
    shutil.copyfile(str(DATABASE_PATH), copy)
    os.replace(copy, str(DATABASE_PATH))
    check("the file replaced (a restore): a change", watch.changed())
    check("... then quiet again", not watch.changed())

    started = time.process_time()
    for _ in range(20000):
        watch.changed()
    elapsed = time.process_time() - started
    check("a look is cheap (20k in well under a second of CPU)", elapsed < 0.5, f"{elapsed:.2f}s")
    watch.close()
    check("after close the next look counts as a change", watch.changed())
    watch.close()
    other.close()


def test_wal() -> None:
    conn = sqlite3.connect(str(DATABASE_PATH))
    mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
    conn.close()
    watch = db.ChangeWatch()
    check("WAL mode: first look a change", mode == "wal" and watch.changed(), mode)
    check("WAL mode: nothing committed since: no change", not watch.changed())
    write("INSERT INTO t VALUES ('e')")
    check("WAL mode: a commit by another connection is seen", watch.changed())
    check("WAL mode: ... once", not watch.changed())
    watch.close()


test_rollback_journal()
test_wal()

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
if passed != len(results):
    sys.exit(1)
