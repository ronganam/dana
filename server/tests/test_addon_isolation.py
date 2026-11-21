"""Site addon isolation (core/addons/loader.py, core/server.py): a wrong declared name, a tool needing a missing addon, a BaseException at import, a repeated hook key, a failing migration, a failing or shadowing api.py each fail that addon alone (with what needs it), whatever it registered is gone, the others load.

Run: bin/test test_addon_isolation

Each case runs in a fresh interpreter (addons load once, when ``addons`` is
imported), with its own temp addon folder named in DANA_ADDON_DIRS.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Dict

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("addon_isolation_test")

ROOT = testenv.SERVER
BASE = Path(testenv.scratch_dir())
failures = 0


def check(name: str, cond: bool, detail: object = "") -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    failures += 0 if cond else 1


def addon(folder: Path, name: str, files: Dict[str, str]) -> None:
    """An addon folder ``folder/name`` with ``files`` (relative path → source, dedented)."""
    for rel, body in files.items():
        path = folder / name / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body))


def manifest(name: str, extra: str = "") -> str:
    return f"from core.addons import Addon\naddon = Addon({name!r}, description='Test addon'{extra})\n"


def echo_tool(name: str, flags: str = "") -> str:
    return textwrap.dedent(f"""\
        from core.addons import Connection
        from addons.{name}.addon import addon

        @addon.tool({flags})
        def {name}_echo(conn: Connection, text: str) -> str:
            \"\"\"Echo the text back.

            Args:
                text: What to echo.
            \"\"\"
            return text
        """)


def run(case: str, script: str) -> Dict[str, str]:
    """``script`` in a fresh interpreter with ``BASE/case`` as the only site folder; its ``KEY value`` lines."""
    env = dict(os.environ, DATABASE_PATH=testenv.scratch_path(f"{case}.sqlite3"), DANA_ADDON_DIRS=str(BASE / case))
    proc = subprocess.run([sys.executable, "-c", textwrap.dedent(script)], cwd=ROOT, env=env, capture_output=True,
                          text=True, timeout=180)
    out = dict(line.split(" ", 1) for line in proc.stdout.splitlines() if " " in line)
    out["_rc"] = str(proc.returncode)
    out["_err"] = proc.stderr[-1500:]
    return out


# --- 1. Loading: names, validation, BaseException, repeated keys ---------------------------------------------
case = BASE / "load"
addon(case, "good", {"addon.py": manifest("good"), "tools.py": echo_tool("good")})
addon(case, "misnamed", {"addon.py": manifest("other_name")})
addon(case, "twoaddons", {"addon.py": manifest("twoaddons"),
                          "tools.py": "from core.addons import Addon\nextra = Addon('stowaway')\n"})
addon(case, "needtool", {"addon.py": manifest("needtool"), "tools.py": echo_tool("needtool", "needs=('absent',)")})
addon(case, "basexc", {"addon.py": manifest("basexc"),
                       "tools.py": "class Weird(BaseException):\n    pass\n\nraise Weird('not an Exception')\n"})
addon(case, "dupkey", {"addon.py": manifest("dupkey"), "hooks.py": """\
    from core.addons import Check
    from addons.dupkey.addon import addon

    @addon.selfcheck("reminders", "Mine too")
    def mine(user, now):
        return Check(True, "ok")
    """})
addon(case, "dupcount", {"addon.py": manifest("dupcount"), "hooks.py": """\
    from addons.dupcount.addon import addon

    @addon.briefing("dupcount", counts=("due",))
    def section(user, ctx):
        return None
    """})
addon(case, "dupprefix", {"addon.py": manifest("dupprefix"), "hooks.py": """\
    from addons.dupprefix.addon import addon

    @addon.now_action("task")
    async def act(user, ref, action, args, now):
        return {}
    """})
addon(case, "dupllm", {"addon.py": manifest("dupllm", ", llm_jobs={'task_emoji': 'fast'}")})
addon(case, "needsbad", {"addon.py": manifest("needsbad", ", needs=('needtool',)"), "tools.py": echo_tool("needsbad")})

out = run("load", """
    from core import purposes
    from core.addons import loader, registry
    from core.addons.addon import all_addons, find_tool
    loaded = loader.loaded()
    print("LOADED", sorted(n for n in loaded if not loader.folder(n).source.official))
    print("FAILED", sorted(loader.failed()))
    for name, f in sorted(loader.failed().items()):
        print("WHY_" + name, f.error.replace(chr(10), " "))
    names = {a.name for a in all_addons()}
    print("DECLARED_LEFT", sorted(names & {"other_name", "stowaway", "misnamed", "twoaddons", "needtool", "basexc"}))
    print("TOOLS_LEFT", sorted(t for t in ("needtool_echo", "needsbad_echo") if find_tool(t)))
    print("GOOD_TOOL", find_tool("good_echo") is not None)
    print("PURPOSE_OWNER", purposes.get("task_emoji").addon)
    print("CHECK_OWNER", registry.hook_for("selfcheck", "reminders", None).addon)
    print("OFFICIAL_ALL", all(n in loaded for n in ("tasks", "essentials", "knowledge")))
""")
failed = out.get("FAILED", "")
check("load: the healthy site addon loads beside the broken ones", out.get("LOADED") == "['good']",
      f"{out.get('LOADED')} {out['_err']}")
check("load: every broken site addon is failed, the start goes on",
      failed == "['basexc', 'dupcount', 'dupkey', 'dupllm', 'dupprefix', 'misnamed', 'needsbad', 'needtool', "
                "'twoaddons']", failed)
check("names: a declared name unlike the folder's fails it", "misnamed" in out.get("WHY_misnamed", "")
      and "Addon('misnamed'" in out.get("WHY_misnamed", ""), out.get("WHY_misnamed"))
check("names: a module declaring a second addon fails it", "stowaway" in out.get("WHY_twoaddons", ""),
      out.get("WHY_twoaddons"))
check("names: neither the wrong name nor the stowaway stays registered", out.get("DECLARED_LEFT") == "[]",
      out.get("DECLARED_LEFT"))
check("validation per addon: a tool needing a missing addon fails that addon only",
      "absent" in out.get("WHY_needtool", ""), out.get("WHY_needtool"))
check("validation per addon: an addon that needs it goes too, and neither leaves a tool",
      "needtool" in out.get("WHY_needsbad", "") and out.get("TOOLS_LEFT") == "[]" and out.get("GOOD_TOOL") == "True",
      f"{out.get('WHY_needsbad')} {out.get('TOOLS_LEFT')}")
check("BaseException: a non-Exception raised at import is caught for a site addon",
      "Weird" in out.get("WHY_basexc", ""), out.get("WHY_basexc"))
check("keys: a check key, a briefing count and a card prefix another addon has fail the later addon",
      "reminders" in out.get("WHY_dupkey", "") and "tasks" in out.get("WHY_dupkey", "")
      and "'due'" in out.get("WHY_dupcount", "") and "'task'" in out.get("WHY_dupprefix", ""),
      f"{out.get('WHY_dupkey')} | {out.get('WHY_dupcount')} | {out.get('WHY_dupprefix')}")
check("keys: the first owner keeps the key; a taken LLM purpose fails the later addon",
      out.get("CHECK_OWNER") == "tasks" and out.get("PURPOSE_OWNER") == "tasks"
      and "task_emoji" in out.get("WHY_dupllm", ""), f"{out.get('CHECK_OWNER')} {out.get('WHY_dupllm')}")
check("official addons all load", out.get("OFFICIAL_ALL") == "True", out["_err"])

# --- 2. An official addon failing still stops the start -------------------------------------------------------
case = BASE / "official"
addon(case, "tasks", {"addon.py": manifest("tasks")})
out = run("official", "import addons")
check("official: a clash with an official addon still stops the start", out["_rc"] != "0" and "clashes" in out["_err"],
      out["_err"][-300:])

# --- 3. Migrations: a failing site addon is unloaded with what needs it -----------------------------------------
case = BASE / "migrate"
addon(case, "mfine", {"addon.py": manifest("mfine"), "tools.py": echo_tool("mfine"),
                      "migrations/m0001_base.py": """\
    VERSION = 1
    NAME = "base"

    def up(conn):
        conn.execute("CREATE TABLE mfine_items (id INTEGER PRIMARY KEY)")
    """})
addon(case, "mbroken", {"addon.py": manifest("mbroken"), "tools.py": echo_tool("mbroken"),
                        "migrations/m0001_base.py": """\
    VERSION = 1
    NAME = "base"

    def up(conn):
        conn.execute("CREATE TABLE mbroken_items (id INTEGER PRIMARY KEY)")
        raise RuntimeError("migration broken on purpose")
    """})
addon(case, "mneeds", {"addon.py": manifest("mneeds", ", needs=('mbroken',)"), "tools.py": echo_tool("mneeds")})
out = run("migrate", """
    from core import db
    from core.addons import loader
    from core.addons.addon import find_tool
    result = loader.migrate()
    print("APPLIED_SITE", [l for l in result if l.split(":")[0] in ("mfine", "mbroken", "mneeds")])
    print("RESULT_FAILED", sorted(result.failed))
    print("LOADED", sorted(n for n in loader.loaded() if not loader.folder(n).source.official))
    print("FAILED", sorted(loader.failed()))
    print("WHY", loader.failed()["mbroken"].error.replace(chr(10), " "))
    print("TOOLS", sorted(t for t in ("mfine_echo", "mbroken_echo", "mneeds_echo") if find_tool(t)))
    conn = db.get_db_connection()
    print("TABLES", sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'm%_items'")))
""")
check("migrate: the healthy site addon's migration applies", out.get("APPLIED_SITE") == "['mfine:0001_base']",
      f"{out.get('APPLIED_SITE')} {out['_err']}")
check("migrate: the failing addon is rolled back and taken out with the addon that needs it",
      out.get("LOADED") == "['mfine']" and out.get("FAILED") == "['mbroken', 'mneeds']"
      and out.get("TABLES") == "['mfine_items']", f"{out.get('LOADED')} {out.get('FAILED')} {out.get('TABLES')}")
check("migrate: the reason says the migration failed", "migration broken on purpose" in out.get("WHY", ""),
      out.get("WHY"))
check("migrate: the taken-out addons leave no tool", out.get("TOOLS") == "['mfine_echo']", out.get("TOOLS"))

# --- 4. Routes: setup raising, shadowing core, /static/ ---------------------------------------------------------
case = BASE / "routes"


def api(paths: str, extra: str = "") -> str:
    return textwrap.dedent(f"""\
        from aiohttp import web

        async def ok(request):
            return web.json_response({{"ok": True}})

        def setup(app):
            {extra}
            for path in {paths}:
                app.router.add_get(path, ok)
        """)


addon(case, "rfine", {"addon.py": manifest("rfine"), "api.py": api("['/api/rfine', '/api/rfine/{item}']")})
addon(case, "rboom", {"addon.py": manifest("rboom"), "api.py": api("['/api/rboom']", "raise RuntimeError('setup broke')")})
addon(case, "rhealth", {"addon.py": manifest("rhealth"), "api.py": api("['/api/rhealth', '/api/health']")})
addon(case, "rdynamic", {"addon.py": manifest("rdynamic"), "api.py": api("['/api/sessions/special']")})
addon(case, "rstatic", {"addon.py": manifest("rstatic"), "api.py": api("['/static/app.js']")})
addon(case, "rlater", {"addon.py": manifest("rlater"), "api.py": api("['/api/rfine']")})
out = run("routes", """
    from aiohttp import web
    from core import server
    from core.addons import loader
    app = web.Application()
    server.setup_routes(app)
    paths = {r.canonical for r in app.router.resources()}
    print("LOADED", sorted(n for n in loader.loaded() if not loader.folder(n).source.official))
    print("FAILED", sorted(loader.failed()))
    for name, f in sorted(loader.failed().items()):
        print("WHY_" + name, f.error.replace(chr(10), " "))
    print("RFINE", "/api/rfine" in paths and "/api/rfine/{item}" in paths)
    print("REFUSED_LEFT", sorted(p for p in ("/api/rboom", "/api/rhealth", "/api/sessions/special", "/static/app.js")
                                if p in paths))
    print("OWNER", app[server.ROUTE_OWNERS].get(next(r for r in app.router.resources() if r.canonical == "/api/rfine")))
""")
check("routes: a well-behaved site addon's routes are added and owned by it",
      out.get("RFINE") == "True" and out.get("OWNER") == "rfine", f"{out.get('RFINE')} {out['_err']}")
check("routes: refused site addons are failed, the others stay",
      out.get("LOADED") == "['rfine']" and out.get("FAILED") == "['rboom', 'rdynamic', 'rhealth', 'rlater', 'rstatic']",
      f"{out.get('LOADED')} {out.get('FAILED')}")
check("routes: a setup that raises is refused", "setup broke" in out.get("WHY_rboom", ""), out.get("WHY_rboom"))
check("routes: a route shadowing a core route is refused (none of that addon's routes added)",
      "/api/health" in out.get("WHY_rhealth", "") and out.get("REFUSED_LEFT") == "[]",
      f"{out.get('WHY_rhealth')} {out.get('REFUSED_LEFT')}")
check("routes: a plain route a core dynamic route answers is refused",
      "shadow" in out.get("WHY_rdynamic", ""), out.get("WHY_rdynamic"))
check("routes: /static/ is refused", "/static/" in out.get("WHY_rstatic", ""), out.get("WHY_rstatic"))
check("routes: a route an earlier site addon has is refused", "/api/rfine" in out.get("WHY_rlater", ""),
      out.get("WHY_rlater"))

if failures:
    raise SystemExit(f"{failures} check(s) failed")
print("\nAll addon isolation checks passed")
