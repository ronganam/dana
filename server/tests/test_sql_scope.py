"""SQL user-scoping lint (docs/addons.md, "Data") and core.addons.data.UserTable tests.

Scans every SQL statement in server/core/ and server/addons/ (excluding tests,
migrations, and convert.py) that touches a table with a ``user_id`` column.
Any statement without ``user_id`` in the SQL text must carry a ``# scoped:``
comment explaining why it is safe (for example, a server-wide retention pass or
a primary-key lookup already authorised for the user on the line above).

Run: bin/test test_sql_scope
"""

import ast
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("sql_scope_test")

from core import db, users
from core.addons.data import UserTable

SERVER = testenv.SERVER
failures = 0


def check(name: str, cond: bool, detail: object = "") -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    failures += 0 if cond else 1


# --- 1. UserTable unit tests ---------------------------------------------------------------

def test_user_table() -> None:
    db.migrate()
    users.bootstrap()
    u1 = users.first_admin()
    u2 = users.create("Maya")
    assert u1 is not None

    with db.get_db_connection() as conn:
        conn.execute("CREATE TABLE _ut_demo (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL, done INTEGER DEFAULT 0)")
        conn.commit()

    tbl = UserTable("_ut_demo")
    check("UserTable: insert returns id", tbl.insert(u1, {"id": "a1", "title": "First"}) == "a1")
    tbl.insert(u1, {"id": "a2", "title": "Second", "done": 1})
    tbl.insert(u2, {"id": "b1", "title": "Maya item"})

    check("UserTable: get returns own row and hides other user's row",
          (tbl.get(u1, "a1") or {}).get("title") == "First"
          and tbl.get(u1, "b1") is None
          and tbl.get(u2, "a1") is None)
    check("UserTable: exists scoped per user",
          tbl.exists(u1, "a1") and not tbl.exists(u1, "b1") and tbl.exists(u2, "b1"))
    check("UserTable: list with where/order/limit scoped per user",
          [r["id"] for r in tbl.list(u1, order="id")] == ["a1", "a2"]
          and [r["id"] for r in tbl.list(u1, where="done = ?", params=(0,))] == ["a1"]
          and len(tbl.list(u1, limit=1)) == 1
          and [r["id"] for r in tbl.list(u2)] == ["b1"])
    check("UserTable: count scoped per user",
          tbl.count(u1) == 2 and tbl.count(u1, where="done = 1") == 1 and tbl.count(u2) == 1)
    check("UserTable: update modifies own row only",
          tbl.update(u1, "a1", {"done": 1}) is True
          and tbl.update(u1, "b1", {"done": 1}) is False
          and (tbl.get(u2, "b1") or {}).get("done") == 0)
    check("UserTable: delete removes own row only",
          tbl.delete(u1, "b1") is False
          and tbl.exists(u2, "b1") is True
          and tbl.delete(u1, "a2") is True
          and tbl.exists(u1, "a2") is False)
    check("UserTable: delete_where removes only user's matching rows",
          tbl.delete_where(u1) == 1 and tbl.count(u1) == 0 and tbl.count(u2) == 1)

    bad_ident = False
    try:
        UserTable("bad; DROP TABLE users")
    except ValueError:
        bad_ident = True
    check("UserTable: rejects non-identifier table name", bad_ident)

    bad_user_update = False
    try:
        tbl.update(u2, "b1", {"user_id": u1.id})
    except ValueError:
        bad_user_update = True
    check("UserTable: refuses changing user_id in update", bad_user_update)

    with db.get_db_connection() as conn:
        conn.execute("DROP TABLE _ut_demo")
        conn.commit()


# --- 2. SQL user-scoping static lint -------------------------------------------------------

_SQL_START = re.compile(r"^\s*(?:WITH\b.*?\b)?(SELECT|INSERT|UPDATE|DELETE)\b", re.I | re.S)
_TABLE_REF = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE(?:\s+OR\s+(?:ABORT|FAIL|IGNORE|REPLACE|ROLLBACK))?)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.I,
)
_USER_ID = re.compile(r"\buser_id\b", re.I)
_FTS_SHADOW = re.compile(r"_fts_(?:data|idx|docsize|config|content)$")
_SKIP_PARTS = {"tests", "migrations", "__pycache__"}
_SKIP_FILES = {"convert.py", "data.py"}


def user_scoped_tables() -> Set[str]:
    """Every real table in the migrated schema that has a ``user_id`` column."""
    tables: Set[str] = set()
    with db.get_db_connection() as conn:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        for name in names:
            if _FTS_SHADOW.search(name):
                continue
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({name})")}
            if "user_id" in cols:
                tables.add(name)
    return tables


_SCOPE_HELPER = re.compile(r"(?:^|_)(?:visible|owned|calls|notifications|scope|tasks|entries|projects|suggestions)(?:_sql|_where)$")


def _is_scope_helper_call(node: ast.AST) -> bool:
    """True for calls like ``visible_sql(user, ...)`` or ``store.owned_sql(user)``."""
    if not isinstance(node, ast.Call):
        return False
    name = node.func.id if isinstance(node.func, ast.Name) else (
        node.func.attr if isinstance(node.func, ast.Attribute) else "")
    return bool(_SCOPE_HELPER.search(name))


def _extract_str(node: ast.AST, env: Dict[str, str], scoped_vars: Set[str]) -> Optional[str]:
    """Best-effort static string extraction from a constant, f-string, + concat, or local name."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: List[str] = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                if isinstance(v.value, ast.Name) and v.value.id in scoped_vars:
                    parts.append(" user_id ")
                else:
                    inner = _extract_str(v.value, env, scoped_vars)
                    parts.append(inner if inner is not None else " {expr} ")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _extract_str(node.left, env, scoped_vars)
        right = _extract_str(node.right, env, scoped_vars)
        if left is not None or right is not None:
            return (left or "") + (right or "")
    if isinstance(node, ast.Name):
        if node.id in scoped_vars:
            return env.get(node.id, "") + " /* user_id */"
        if node.id in env:
            return env[node.id]
    return None


def _has_scoped_comment(lines: List[str], start: int, end: int) -> bool:
    lo = max(0, start - 3)
    hi = min(len(lines), end + 1)
    return any("# scoped:" in lines[i] for i in range(lo, hi))


def _scan_file(path: Path, scoped_tables: Set[str]) -> List[Tuple[int, str, str]]:
    """Return [(lineno, tables, sql_preview)] for unscoped SQL on per-user tables."""
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src, filename=str(path))
    findings: List[Tuple[int, str, str]] = []
    seen: Set[Tuple[int, str]] = set()

    def walk_scope(stmts: List[ast.stmt], env: Dict[str, str], scoped_vars: Set[str]) -> None:
        local = dict(env)
        scoped = set(scoped_vars)
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk_scope(stmt.body, local, scoped)
                continue
            stmt_end = getattr(stmt, "end_lineno", stmt.lineno) or stmt.lineno
            stmt_has_comment = _has_scoped_comment(lines, stmt.lineno, stmt_end)
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                tgt = stmt.targets[0]
                if isinstance(tgt, ast.Name):
                    val = _extract_str(stmt.value, local, scoped)
                    if val is not None:
                        local[tgt.id] = val
                    if stmt_has_comment or _is_scope_helper_call(stmt.value):
                        scoped.add(tgt.id)
                elif isinstance(tgt, ast.Tuple) and tgt.elts and isinstance(tgt.elts[0], ast.Name):
                    # e.g. `where, args = visible_sql(user, "t")`
                    if stmt_has_comment or _is_scope_helper_call(stmt.value):
                        scoped.add(tgt.elts[0].id)
            elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name) and isinstance(stmt.op, ast.Add):
                val = _extract_str(stmt.value, local, scoped)
                if val is not None:
                    local[stmt.target.id] = local.get(stmt.target.id, "") + val
                if stmt_has_comment:
                    scoped.add(stmt.target.id)
            _check_exprs(stmt, local, scoped)
            for attr in ("body", "orelse", "finalbody"):
                sub = getattr(stmt, attr, None)
                if isinstance(sub, list) and sub and isinstance(sub[0], ast.stmt):
                    walk_scope(sub, local, scoped)
            if isinstance(stmt, ast.Try):
                for h in stmt.handlers:
                    walk_scope(h.body, local, scoped)

    def _check_exprs(node: ast.AST, local: Dict[str, str], scoped: Set[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.stmt)):
                continue
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr in (
                    "execute", "executemany", "executescript") and child.args:
                sql = _extract_str(child.args[0], local, scoped)
                if sql and _SQL_START.match(sql):
                    refs = {m.group(1) for m in _TABLE_REF.finditer(sql)} & scoped_tables
                    if refs and not _USER_ID.search(sql):
                        end = getattr(child, "end_lineno", child.lineno) or child.lineno
                        if not _has_scoped_comment(lines, child.lineno, end):
                            key = (child.lineno, ",".join(sorted(refs)))
                            if key not in seen:
                                seen.add(key)
                                findings.append((child.lineno, key[1], " ".join(sql.split())[:110]))
            _check_exprs(child, local, scoped)

    walk_scope(tree.body, {}, set())
    return findings


def test_sql_scoping_lint() -> None:
    scoped_tables = user_scoped_tables()
    check("lint: discovered per-user tables from migrated schema", len(scoped_tables) >= 20, len(scoped_tables))

    # Self-test: verify the scanner flags an unscoped query and accepts user_id or '# scoped:'.
    probe = Path(testenv.scratch_path("probe_scope.py"))
    probe.write_text(
        "def bad(conn):\n"
        "    conn.execute('SELECT * FROM tasks WHERE id = ?', ('1',))\n"
        "def ok_uid(conn):\n"
        "    conn.execute('SELECT * FROM tasks WHERE id = ? AND user_id = ?', ('1', 'u1'))\n"
        "def ok_comment(conn):\n"
        "    # scoped: verified above\n"
        "    conn.execute('DELETE FROM tasks WHERE id = ?', ('1',))\n",
        encoding="utf-8",
    )
    hits = _scan_file(probe, scoped_tables)
    check("lint: catches unscoped query and allows user_id / '# scoped:'",
          len(hits) == 1 and hits[0][0] == 2 and hits[0][1] == "tasks", hits)

    all_findings: List[str] = []
    for root in (SERVER / "core", SERVER / "addons"):
        for path in sorted(root.rglob("*.py")):
            if _SKIP_PARTS & set(path.parts) or path.name in _SKIP_FILES:
                continue
            for lineno, tables, preview in _scan_file(path, scoped_tables):
                rel = path.relative_to(SERVER)
                all_findings.append(f"{rel}:{lineno} [{tables}] {preview}")

    check("lint: every SQL query on a per-user table filters by user_id or has '# scoped:'",
          not all_findings, "\n  " + "\n  ".join(all_findings))


test_user_table()
test_sql_scoping_lint()

if failures:
    raise SystemExit(f"{failures} check(s) failed")
print("\nAll SQL scoping and UserTable checks passed")
