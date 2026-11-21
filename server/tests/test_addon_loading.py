"""Site addons (docs/writing-an-addon.md): from the site's addons/ or DANA_ADDON_DIRS, after the official addons, tools only for users who connected them, name clashes refused; the shipped example addon works end to end.

Run: bin/test test_addon_loading

Each case runs in a fresh interpreter: addons register once, when ``addons`` is imported.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("addon_loading_test")

ROOT = testenv.SERVER
failures = 0


def check(name: str, cond: bool, detail: object = "") -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    failures += 0 if cond else 1


def write_addon(folder: Path, name: str) -> None:
    pkg = folder / name
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(textwrap.dedent(f"""\
        from addons.{name} import addon, tools  # noqa: F401
        """))
    (pkg / "addon.py").write_text(textwrap.dedent(f"""\
        from core.addons import Addon
        addon = Addon("{name}", description="A test addon")
        """))
    (pkg / "tools.py").write_text(textwrap.dedent(f"""\
        from core.addons import Connection
        from addons.{name}.addon import addon

        @addon.tool()
        def {name}_echo(conn: Connection, text: str) -> str:
            \"\"\"Echo the text back.

            Args:
                text: What to echo.
            \"\"\"
            return f"{name}:{{conn.user.name}}:{{text}}"
        """))


def run(script: str, addon_dirs: str | None, site: str | None = None) -> subprocess.CompletedProcess:
    """``script`` in a fresh interpreter; ``addon_dirs`` None leaves DANA_ADDON_DIRS unset (the site default)."""
    env = dict(os.environ, DATABASE_PATH=testenv.scratch_path(f"p{len(addon_dirs or '')}{len(site or '')}.sqlite3"))
    env.pop("DANA_ADDON_DIRS", None)
    if addon_dirs is not None:
        env["DANA_ADDON_DIRS"] = addon_dirs
    if site is not None:
        env["DANA_SITE"] = site
    return subprocess.run([sys.executable, "-c", textwrap.dedent(script)], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)


base = Path(testenv.scratch_dir())
one, two = base / "one", base / "two"
write_addon(one, "zeta")
write_addon(two, "alpha")
(one / "not_an_addon").mkdir()

out = run("""
    import asyncio
    import addons
    from core import db, users
    from core.addons import registry, store
    from core.addons.addon import all_addons
    db.migrate(); users.bootstrap()
    admin = users.first_admin()
    other = users.create("Maya")
    order = [k.name for k in all_addons()]
    print("SITE", list(addons.SITE_ADDONS))
    print("TAIL", order[-3:])
    print("BEFORE", registry.has_tool(admin, "zeta_echo"))
    store.connect(admin, "zeta")
    print("AFTER", registry.has_tool(admin, "zeta_echo"), registry.has_tool(other, "zeta_echo"))
    print("RESULT", asyncio.run(registry.execute(admin, "zeta_echo", {"text": "hi"})))
""", f"{one}{os.pathsep}{two}{os.pathsep}{base / 'missing'}")
lines = dict(line.split(" ", 1) for line in out.stdout.splitlines() if " " in line)
check("site addons: loaded in folder order, folders without addon.py skipped, a missing folder ignored",
      lines.get("SITE") == "['zeta', 'alpha']", out.stdout + out.stderr[-2000:])
check("site addons: after the official addons", lines.get("TAIL") == "['ops', 'zeta', 'alpha']", lines.get("TAIL"))
check("site addons: a tool only for a user who connected the addon",
      lines.get("BEFORE") == "False" and lines.get("AFTER") == "True False", (lines.get("BEFORE"), lines.get("AFTER")))
check("site addons: the tool runs with the user's connection", lines.get("RESULT") == "zeta:Robin Levi:hi", lines.get("RESULT"))

clash = base / "clash"
write_addon(clash, "tasks")
out = run("import addons", str(clash))
check("site addons: an official addon's name is refused", out.returncode != 0 and "clashes" in out.stderr, out.stderr[-500:])

again = base / "again"
write_addon(again, "zeta")
out = run("import addons", f"{one}{os.pathsep}{again}")
check("site addons: two with one name are refused", out.returncode != 0 and "clashes" in out.stderr, out.stderr[-500:])

out = run("import addons; print(addons.SITE_ADDONS)", "")
check("site addons: none with DANA_ADDON_DIRS empty", out.stdout.strip() == "()", out.stdout + out.stderr[-500:])

# --- the site folder: its addons/ is the default ------------------------------------------------
site = base / "site"
write_addon(site / "addons", "siteaddon")
out = run("import addons; print(addons.SITE_ADDONS)", None, str(site))
check("site: DANA_SITE's addons/ load when DANA_ADDON_DIRS is unset", out.stdout.strip() == "('siteaddon',)",
      out.stdout + out.stderr[-500:])
out = run("import addons; print(addons.SITE_ADDONS)", "", str(site))
check("site: DANA_ADDON_DIRS='' turns site addons off (what testenv does)", out.stdout.strip() == "()", out.stdout + out.stderr[-500:])
out = run("import addons; print(addons.site_dir())", None)
check("site: without DANA_SITE it is site/ at the repository root",
      out.stdout.strip() == str(ROOT.parent / "site"), out.stdout + out.stderr[-500:])

# --- the shipped example (examples/addons/hello): tool, setting, prompt line, route -------------
EXAMPLES = ROOT.parent / "examples" / "addons"


def check_example(folder: Path) -> None:
    """The example addon in ``folder`` loads as a site addon and works for its user only."""
    out = run("""
        import asyncio, json, os
        os.environ["API_TOKEN"] = "env-token"  # Bearer tokens decide the user (else every request is the first admin)
        import addons
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        from core import db, users
        from core.auth import setup_auth
        from core.server import site_route_modules
        from core.addons import registry, store
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
            for module in site_route_modules():
                module.setup(app)
            async with TestClient(TestServer(app)) as c:
                mine = await c.get("/api/hello", headers={"Authorization": "Bearer " + users.issue_token(admin.id, "t")[0]})
                theirs = await c.get("/api/hello", headers={"Authorization": "Bearer " + users.issue_token(other.id, "t")[0]})
                print("ROUTE", mine.status, json.dumps(await mine.json()), theirs.status)
        asyncio.run(http())
    """, str(folder))
    lines = dict(line.split(" ", 1) for line in out.stdout.splitlines() if " " in line)
    check("example: the tool uses the setting's default", lines.get("DEFAULT") == "Hello, Noa!", out.stdout + out.stderr[-2000:])
    check("example: … and the user's own setting", lines.get("SETTING") == "Shalom, Noa!", lines.get("SETTING"))
    check("example: its instructions only for a user who connected it", lines.get("PROMPT") == "True False", lines.get("PROMPT"))
    check("example: its route answers its user and refuses another (403)",
          lines.get("ROUTE") == '200 {"greeting": "Shalom"} 403', lines.get("ROUTE"))


if EXAMPLES.is_dir():
    check_example(EXAMPLES)
else:  # the image's test stage builds from server/ alone
    print("[SKIP] example: no examples/ beside server/")

if failures:
    raise SystemExit(f"{failures} check(s) failed")
print("\nAll addon loading checks passed")
