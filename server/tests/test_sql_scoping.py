"""SQL scoping lint: every statement on a table with a ``user_id`` column is scoped to a user, or says why not.

A user's rows are reached only through their ``user_id`` (docs/access.md,
"Security rules"; AGENTS.md "User context"). This test reads every SQL string
literal in ``server/`` (not migrations, not tests: core/db/convert.py is a
migration too) and finds the tables it names (``FROM`` / ``JOIN`` / ``INTO`` /
``UPDATE``). A statement naming a table that has a ``user_id`` column passes when:

- its text mentions ``user_id``; or
- it interpolates a scope clause: an f-string placeholder (or a ``.format``
  argument) that is a call of a scope helper, or a name assigned from one in the
  same function. A scope helper is a function named ``*_sql`` / ``*_where`` in
  ``server/`` whose own source mentions ``user_id`` (``visible_sql``,
  ``owned_sql``, ``calls_sql``…); or
- its line, a line it spans, or the line before it carries ``# scoped: <reason>``
  (by id after an access check, a server-wide retention job, an owner join…).

A failure lists ``file:line`` and the tables: scope the statement, or add the
comment with the real reason.
Run: bin/test test_sql_scoping
"""

import ast
import re
import sqlite3
from pathlib import Path
from typing import Dict, Iterator, List, Set, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
DB_PATH = testenv.isolate("sql_scoping_test")

from core import db  # noqa: E402

SERVER = testenv.SERVER
SKIP_DIRS = {"tests", "migrations", ".venv", "node_modules", "__pycache__", "scratch", "bank-worker", "static"}
SKIP_FILES = {"core/db/convert.py"}
TABLE_REF = re.compile(r'\b(?:FROM|JOIN|INTO|UPDATE)\s+"?([A-Za-z_]\w*)')
COMMENT = re.compile(r"#\s*scoped:\s*\S")
HELPER = re.compile(r"\w*_(?:sql|where)$")

failures = 0


def check(name: str, cond: bool, detail: object = "") -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"\n{detail}" if detail and not cond else ""))
    failures += 0 if cond else 1


def scoped_tables() -> Set[str]:
    """Tables (core's and every addon's) that have a ``user_id`` column."""
    db.migrate()
    conn = sqlite3.connect(DB_PATH)
    try:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        return {t for t in names if any(c[1] == "user_id" for c in conn.execute(f'PRAGMA table_info("{t}")'))}
    finally:
        conn.close()


def sources() -> Iterator[Tuple[str, str]]:
    """(path relative to server/, text) of every Python file the lint reads."""
    for path in sorted(SERVER.rglob("*.py")):
        rel = path.relative_to(SERVER)
        if set(rel.parts[:-1]) & SKIP_DIRS or rel.name.startswith("test_") or rel.as_posix() in SKIP_FILES:
            continue
        yield rel.as_posix(), path.read_text()


def scope_helpers(trees: Dict[str, Tuple[ast.Module, str]]) -> Set[str]:
    """Names of the functions that build a user scope clause."""
    found = set()
    for tree, text in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and HELPER.fullmatch(node.name):
                if "user_id" in (ast.get_source_segment(text, node) or ""):
                    found.add(node.name)
    return found


def _called(node: ast.AST) -> str:
    """The function name a call node calls ("" for anything else)."""
    if isinstance(node, ast.Call):
        f = node.func
        return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
    return ""


def _scope_names(func: ast.AST, helpers: Set[str]) -> Set[str]:
    """Names a function assigns from a scope helper call (``where, args = visible_sql(user)``)."""
    out: Set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and _called(node.value) in helpers:
            for target in node.targets:
                for n in ast.walk(target):
                    if isinstance(n, ast.Name):
                        out.add(n.id)
    return out


def _uses_scope(exprs: List[ast.AST], names: Set[str], helpers: Set[str]) -> bool:
    for expr in exprs:
        for n in ast.walk(expr):
            if (isinstance(n, ast.Name) and n.id in names) or _called(n) in helpers:
                return True
    return False


def violations(rel: str, tree: ast.Module, text: str, tables: Set[str], helpers: Set[str]) -> List[str]:
    lines = text.splitlines()
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    formatted: Dict[int, List[ast.AST]] = {}  # literal node id -> .format() arguments
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "format":
            formatted[id(n.func.value)] = [*n.args, *(k.value for k in n.keywords)]
    inner = {id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in n.values}
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            sql = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
            exprs = [v.value for v in node.values if isinstance(v, ast.FormattedValue)]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in inner:
            sql, exprs = node.value, []
        else:
            continue
        named = sorted({t for t in TABLE_REF.findall(sql) if t in tables})
        if not named or "user_id" in sql:
            continue
        exprs = exprs + formatted.get(id(node), [])
        enclosing = [f for f in funcs if f.lineno <= node.lineno <= (f.end_lineno or f.lineno)]
        names = _scope_names(min(enclosing, key=lambda f: (f.end_lineno or 0) - f.lineno), helpers) if enclosing else set()
        if _uses_scope(exprs, names, helpers):
            continue
        span = lines[max(0, node.lineno - 2):node.end_lineno or node.lineno]
        if any(COMMENT.search(line) for line in span):
            continue
        out.append(f"  {rel}:{node.lineno}  {', '.join(named)}: {' '.join(sql.split())[:90]}")
    return out


tables = scoped_tables()
check("the schema has user-scoped tables to check", {"tasks", "notifications", "connections"} <= tables, tables)
trees = {rel: (ast.parse(text), text) for rel, text in sources()}
helpers = scope_helpers(trees)
check("scope helpers found (visible_sql, owned_sql…)", {"visible_sql", "owned_sql"} <= helpers, helpers)
found = [v for rel, (tree, text) in trees.items() for v in violations(rel, tree, text, tables, helpers)]
check(f"every SQL statement on a user-scoped table is scoped or says why (# scoped: <reason>): "
      f"{len(found)} not", not found, "\n".join(found))

print(f"\n{'All checks passed' if not failures else f'{failures} check(s) failed'}")
raise SystemExit(1 if failures else 0)
