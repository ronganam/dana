"""Plugin kinds (docs/plugins.md): from the site's plugins/ or DANA_PLUGIN_DIRS, after the built-in kinds, tools only for users who connected them, name clashes refused; the shipped example plugin works end to end.

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


def run(script: str, plugin_dirs: str | None, site: str | None = None) -> subprocess.CompletedProcess:
    """``script`` in a fresh interpreter; ``plugin_dirs`` None leaves DANA_PLUGIN_DIRS unset (the site default)."""
    env = dict(os.environ, DATABASE_PATH=testenv.scratch_path(f"p{len(plugin_dirs or '')}{len(site or '')}.sqlite3"))
    env.pop("DANA_PLUGIN_DIRS", None)
    if plugin_dirs is not None:
        env["DANA_PLUGIN_DIRS"] = plugin_dirs
    if site is not None:
        env["DANA_SITE"] = site
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

# --- the site folder: its plugins/ is the default ----------------------------------------------
site = base / "site"
write_kind(site / "plugins", "sitekind")
out = run("import integrations; print(integrations.PLUGIN_KINDS)", None, str(site))
check("site: DANA_SITE's plugins/ load when DANA_PLUGIN_DIRS is unset", out.stdout.strip() == "('sitekind',)",
      out.stdout + out.stderr[-500:])
out = run("import integrations; print(integrations.PLUGIN_KINDS)", "", str(site))
check("site: DANA_PLUGIN_DIRS='' turns plugins off (what testenv does)", out.stdout.strip() == "()", out.stdout + out.stderr[-500:])
out = run("import integrations; print(integrations.site_dir())", None)
check("site: without DANA_SITE it is site/ at the repository root",
      out.stdout.strip() == str(ROOT.parent / "site"), out.stdout + out.stderr[-500:])

# --- the shipped example (examples/plugins/hello): tool, setting, prompt line, route -------------
out = run("""
    import asyncio, json, os
    os.environ["API_TOKEN"] = "env-token"  # Bearer tokens decide the user (else every request is the first admin)
    import integrations
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from core import db, users
    from core.auth import setup_auth
    from core.server import plugin_route_modules
    from integrations.framework import registry, store
    db.migrate(); users.bootstrap()
    admin = users.first_admin()
    other = users.create("Maya")
    store.connect(admin, "hello")
    print("DEFAULT", asyncio.run(registry.execute(admin, "hello_greet", {"name": "Noa"})))
    store.update(admin, "hello", settings={"greeting": "Shalom"})
    print("SETTING", asyncio.run(registry.execute(admin, "hello_greet", {"name": "Noa"})))
    print("PROMPT", any("hello_greet" in p for p in registry.prompt_parts(admin, "guides")),
          any("hello_greet" in p for p in registry.prompt_parts(other, "guides")))

    async def http():
        app = web.Application()
        setup_auth(app)
        for module in plugin_route_modules():
            module.setup(app)
        async with TestClient(TestServer(app)) as c:
            mine = await c.get("/api/hello", headers={"Authorization": "Bearer " + users.issue_token(admin.id, "t")[0]})
            theirs = await c.get("/api/hello", headers={"Authorization": "Bearer " + users.issue_token(other.id, "t")[0]})
            print("ROUTE", mine.status, json.dumps(await mine.json()), theirs.status)
    asyncio.run(http())
""", str(ROOT / "examples" / "plugins"))
lines = dict(line.split(" ", 1) for line in out.stdout.splitlines() if " " in line)
check("example: the tool uses the setting's default", lines.get("DEFAULT") == "Hello, Noa!", out.stdout + out.stderr[-2000:])
check("example: … and the user's own setting", lines.get("SETTING") == "Shalom, Noa!", lines.get("SETTING"))
check("example: its instructions only for a user who connected it", lines.get("PROMPT") == "True False", lines.get("PROMPT"))
check("example: its route answers its user and refuses another (403)",
      lines.get("ROUTE") == '200 {"greeting": "Shalom"} 403', lines.get("ROUTE"))

if failures:
    raise SystemExit(f"{failures} check(s) failed")
print("\nAll plugin checks passed")
