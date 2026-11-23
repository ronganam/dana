"""Addon loading (core/addons/loader.py): installed addons (the data dir's addons/, or DANA_ADDON_DIRS folders) after the official ones, ordered by needs then name, a broken one skipped without stopping the start, name clashes refused; the shipped example addon passes its own test.

Run: bin/test test_addon_loading

Each case runs in a fresh interpreter: addons load once, when ``addons`` is imported.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Optional

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("addon_loading_test")

ROOT = testenv.SERVER
failures = 0


def check(name: str, cond: bool, detail: object = "") -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    failures += 0 if cond else 1


def write_addon(folder: Path, name: str, manifest: str = "", tools_extra: str = "") -> None:
    """An addon ``name`` in ``folder`` with one echo tool; ``manifest``: more ``Addon(...)`` arguments."""
    pkg = folder / name
    pkg.mkdir(parents=True)
    (pkg / "addon.py").write_text(textwrap.dedent(f"""\
        from core.addons import Addon, Field, Need, PromptPart, Touches
        addon = Addon("{name}", description="A test addon"{manifest})
        """))
    (pkg / "tools.py").write_text(textwrap.dedent(f"""\
        from core.addons import Connection
        from addons.{name}.addon import addon
        {tools_extra}

        @addon.tool()
        def {name}_echo(conn: Connection, text: str) -> str:
            \"\"\"Echo the text back.

            Args:
                text: What to echo.
            \"\"\"
            return f"{name}:{{conn.user.name}}:{{text}}"
        """))


def run(script: str, addon_dirs: Optional[str], database: Optional[str] = None) -> subprocess.CompletedProcess:
    """``script`` in a fresh interpreter; ``addon_dirs`` None leaves DANA_ADDON_DIRS unset (the data dir's
    addons/ is then the installed source); ``database``: the database path (its folder is the data dir)."""
    env = dict(os.environ, DATABASE_PATH=database or testenv.scratch_path(f"p{len(addon_dirs or '')}.sqlite3"))
    env.pop("DANA_ADDON_DIRS", None)
    if addon_dirs is not None:
        env["DANA_ADDON_DIRS"] = addon_dirs
    return subprocess.run([sys.executable, "-c", textwrap.dedent(script)], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)


def lines_of(out: subprocess.CompletedProcess) -> dict:
    return dict(line.split(" ", 1) for line in out.stdout.splitlines() if " " in line)


base = Path(testenv.scratch_dir())
one, two = base / "one", base / "two"
write_addon(one, "zeta")
write_addon(one, "beta", ', needs=(Need("addon", "zeta", "Its data"),)')  # first by name, but it needs zeta
write_addon(two, "alpha")
write_addon(two, "broken", tools_extra="raise RuntimeError('broken on purpose')")
write_addon(two, "badslot", ', prompt_parts=(PromptPart("nowhere", "x"),)')
write_addon(two, "needy", ', needs=(Need("addon", "absent", "Its data"),)')
write_addon(two, "badneed", ', needs=(Need("gadget", "a gadget", "It needs one"),)')  # not a need kind
write_addon(two, "quietcreds", ', credentials=(Field("token", "A token"),), touches=Touches()')  # creds undeclared
write_addon(two, "missingpkg")
(two / "missingpkg" / "requirements.txt").write_text("# optional dep\nnonexistent-dana-pkg-xyz>=1.0\n")
write_addon(two, "haspkg")
(two / "haspkg" / "requirements.txt").write_text("# installed dep\npydantic>=2.0\naiohttp  # inline comment\n")
(one / "not_an_addon").mkdir()

out = run("""
    import asyncio
    from core import db, users
    from core.addons import loader, registry, store
    from core.addons.addon import all_addons
    print("LOADED", list(loader.non_official()))
    db.migrate(); users.bootstrap()
    admin = users.first_admin()
    other = users.create("Maya")
    order = list(loader.loaded())
    official = [n for n in order if loader.folder(n).source.official]
    print("AFTER_OFFICIAL", order[:len(official)] == official)
    print("ORDER_SAME", [a.name for a in all_addons()] == order)
    print("FAILED", sorted(loader.failed()))
    print("WHY", " | ".join(f.error for _, f in sorted(loader.failed().items())))
    print("BROKEN_TOOL", any(t.name == "broken_echo" for a in all_addons() for t in a.tools))
    print("BEFORE", registry.has_tool(admin, "zeta_echo"))
    store.connect(admin, "zeta")
    print("CONNECTED", registry.has_tool(admin, "zeta_echo"), registry.has_tool(other, "zeta_echo"))
    print("RESULT", asyncio.run(registry.execute(admin, "zeta_echo", {"text": "hi"})))
""", f"{one}{os.pathsep}{two}{os.pathsep}{base / 'missing'}")
lines = lines_of(out)
check("installed addons: by needs then name, folders without addon.py skipped, a missing folder ignored",
      lines.get("LOADED") == "['alpha', 'haspkg', 'zeta', 'beta']", out.stdout + out.stderr[-2000:])
check("installed addons: after the official addons; the addon order is the loaded order",
      lines.get("AFTER_OFFICIAL") == "True" and lines.get("ORDER_SAME") == "True", lines)
check("failure isolation: an installed addon that fails to import, names an unknown slot, needs a missing addon, "
      "declares a need of no known kind or credentials it does not say it stores, or has missing "
      "requirements.txt packages is skipped, the start goes on",
      lines.get("FAILED") == "['badneed', 'badslot', 'broken', 'missingpkg', 'needy', 'quietcreds']", lines.get("FAILED"))
why = lines.get("WHY", "")
check("failure isolation: each with its reason",
      "nowhere" in why and "broken on purpose" in why and "absent" in why
      and "nonexistent-dana-pkg-xyz" in why and "kind must be one of env, account, hardware, app, region, addon" in why
      and "stores_credentials=True" in why, why)
check("failure isolation: a skipped addon leaves no tool behind", lines.get("BROKEN_TOOL") == "False")
check("installed addons: a tool only for a user who connected the addon",
      lines.get("BEFORE") == "False" and lines.get("CONNECTED") == "True False", lines)
check("installed addons: the tool runs with the user's connection", lines.get("RESULT") == "zeta:Robin Levi:hi",
      lines.get("RESULT"))

clash = base / "clash"
write_addon(clash, "tasks")
out = run("import addons", str(clash))
check("names: an official addon's name is refused (the start stops)", out.returncode != 0 and "clashes" in out.stderr,
      out.stderr[-500:])

again = base / "again"
write_addon(again, "zeta")
out = run("import addons", f"{one}{os.pathsep}{again}")
check("names: two with one name are refused", out.returncode != 0 and "clashes" in out.stderr, out.stderr[-500:])

out = run("from core.addons import loader; print(loader.non_official())", "")
check("installed addons: none with DANA_ADDON_DIRS empty", out.stdout.strip() == "()", out.stdout + out.stderr[-500:])

# --- the data dir's addons/ is the default -----------------------------------------------------
data = base / "data"
write_addon(data / "addons", "dataaddon")
db_path = str(data / "db.sqlite3")
out = run("from core.addons import loader; f = loader.folder('dataaddon'); "
          "print(loader.non_official(), f.source.kind, f.source.label)", None, db_path)
check("data dir: <data dir>/addons loads when DANA_ADDON_DIRS is unset, as the installed source",
      out.stdout.strip() == "('dataaddon',) installed installed", out.stdout + out.stderr[-500:])
out = run("from core.addons import loader; print(loader.non_official())", "", db_path)
check("data dir: DANA_ADDON_DIRS='' turns installed addons off (what testenv does)", out.stdout.strip() == "()",
      out.stdout + out.stderr[-500:])
out = run("from core.addons import loader; f = loader.folder('zeta'); print(f.source.kind, f.source.label)", str(one))
check("DANA_ADDON_DIRS: its folders replace the data dir's and count as installed",
      out.stdout.strip() == "extra installed", out.stdout + out.stderr[-500:])
out = run("from core import addon_dirs; print([s.kind for s in addon_dirs.sources()])", None, db_path)
check("sources: official and installed only", out.stdout.strip() == "['official', 'installed']",
      out.stdout + out.stderr[-500:])

# --- the shipped example (examples/addons/hello) runs its own test --------------------------------
EXAMPLE_TEST = ROOT.parent / "examples" / "addons" / "hello" / "tests" / "test_hello.py"
if EXAMPLE_TEST.is_file():
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / "tests")]))
    proc = subprocess.run([sys.executable, str(EXAMPLE_TEST)], cwd=ROOT, env=env, capture_output=True, text=True,
                          timeout=300)
    check("example: examples/addons/hello, installed from its folder, passes its own test "
          "(tool, job, event, Now card, route, migration)",
          proc.returncode == 0, (proc.stdout + proc.stderr)[-2500:])
else:  # the image's test stage builds from server/ alone
    print("[SKIP] example: no examples/ beside server/")

if failures:
    raise SystemExit(f"{failures} check(s) failed")
print("\nAll addon loading checks passed")
