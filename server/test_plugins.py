"""Plugin kinds from DANA_PLUGIN_DIRS (docs/plugins.md): loaded after the built-in kinds, their tools listed and run only for users who connected them, and a name clash refused.

Run: python test_plugins.py

Each case runs in a fresh interpreter: kinds register once, when ``integrations`` is imported.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("plugins_test")

ROOT = Path(__file__).resolve().parent
failures = 0


def check(name: str, cond: bool, detail: object = "") -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    failures += 0 if cond else 1


def write_kind(folder: Path, name: str) -> None:
    pkg = folder / name
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(textwrap.dedent(f"""\
        from integrations.{name}.kind import {name}
        from integrations.{name} import tools  # noqa: F401
        """))
    (pkg / "kind.py").write_text(textwrap.dedent(f"""\
        from integrations.framework import Integration
        {name} = Integration("{name}", description="A test plugin")
        """))
    (pkg / "tools.py").write_text(textwrap.dedent(f"""\
        from integrations.framework import Connection
        from integrations.{name}.kind import {name}

        @{name}.tool()
        def {name}_echo(conn: Connection, text: str) -> str:
            \"\"\"Echo the text back.

            Args:
                text: What to echo.
            \"\"\"
            return f"{name}:{{conn.user.name}}:{{text}}"
        """))


def run(script: str, plugin_dirs: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, DANA_PLUGIN_DIRS=plugin_dirs, DATABASE_PATH=testenv.scratch_path(f"p{len(plugin_dirs)}.sqlite3"))
    return subprocess.run([sys.executable, "-c", textwrap.dedent(script)], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)


base = Path(testenv.scratch_dir())
one, two = base / "one", base / "two"
write_kind(one, "zeta")
write_kind(two, "alpha")
(one / "not_a_kind").mkdir()

out = run("""
    import asyncio
    import integrations
    from core import db, users
    from integrations.framework import registry, store
    from integrations.framework.kind import kinds
    db.migrate(); users.bootstrap()
    admin = users.first_admin()
    other = users.create("Maya")
    order = [k.kind for k in kinds()]
    print("PLUGINS", list(integrations.PLUGIN_KINDS))
    print("TAIL", order[-3:])
    print("BEFORE", registry.has_tool(admin, "zeta_echo"))
    store.connect(admin, "zeta")
    print("AFTER", registry.has_tool(admin, "zeta_echo"), registry.has_tool(other, "zeta_echo"))
    print("RESULT", asyncio.run(registry.execute(admin, "zeta_echo", {"text": "hi"})))
""", f"{one}{os.pathsep}{two}{os.pathsep}{base / 'missing'}")
lines = dict(line.split(" ", 1) for line in out.stdout.splitlines() if " " in line)
check("plugins: loaded in folder order, folders without kind.py skipped, a missing folder ignored",
      lines.get("PLUGINS") == "['zeta', 'alpha']", out.stdout + out.stderr[-2000:])
check("plugins: after the built-in kinds", lines.get("TAIL") == "['ops', 'zeta', 'alpha']", lines.get("TAIL"))
check("plugins: a tool only for a user who connected the kind",
      lines.get("BEFORE") == "False" and lines.get("AFTER") == "True False", (lines.get("BEFORE"), lines.get("AFTER")))
check("plugins: the tool runs with the user's connection", lines.get("RESULT") == "zeta:Robin Levi:hi", lines.get("RESULT"))

clash = base / "clash"
write_kind(clash, "tasks")
out = run("import integrations", str(clash))
check("plugins: a built-in kind's name is refused", out.returncode != 0 and "clashes" in out.stderr, out.stderr[-500:])

again = base / "again"
write_kind(again, "zeta")
out = run("import integrations", f"{one}{os.pathsep}{again}")
check("plugins: two plugins with one name are refused", out.returncode != 0 and "clashes" in out.stderr, out.stderr[-500:])

out = run("import integrations; print(integrations.PLUGIN_KINDS)", "")
check("plugins: none without DANA_PLUGIN_DIRS", out.stdout.strip() == "()", out.stdout + out.stderr[-500:])

if failures:
    raise SystemExit(f"{failures} check(s) failed")
print("\nAll plugin checks passed")
