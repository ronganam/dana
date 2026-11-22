"""bin/dana addons new: the addon and its kit, with --web and --worker, written into a site that bin/test then runs and passes; refused names; the example addon keeps the scaffold's shape.

Run: bin/test test_addon_scaffold

End to end, as an author does it: ``bin/dana addons new`` (a subprocess, the
real command line) writes into ``$DANA_SITE/addons`` of a temp site, then
``bin/test <name>`` (a subprocess too) finds the new addons' tests there and
they pass as written. ``examples/addons/hello`` has every code file the
scaffold writes with ``--web``, at the same path (the kit's files, AGENTS.md,
REFERENCE.md and the tests' harness, are checked by ``test_addon_kit.py``).
"""

import os
import subprocess
import sys
from pathlib import Path

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("addon_scaffold_test")

SERVER = testenv.SERVER
SITE = Path(testenv.scratch_dir()) / "site"
ADDONS = SITE / "addons"
EXAMPLE = SERVER.parent / "examples" / "addons" / "hello"
ENV = {**os.environ, "DANA_SITE": str(SITE), "DANA_ENV_FILE": ""}
ENV.pop("DANA_ADDON_DIRS", None)  # the site's addons/ is where bin/test looks
failures = 0


def check(name: str, cond: object, detail: object = "") -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    failures += 0 if cond else 1


def dana(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SERVER / "bin" / "dana"), *args], cwd=str(SERVER), env=ENV,
                          capture_output=True, text=True, timeout=120)


def files(folder: Path) -> list:
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts and ".git" not in p.parts)


KIT = [".gitignore", "AGENTS.md", "README.md", "REFERENCE.md", "addon.py", "hooks.py", "tests/dana_harness.py",
       "tests/fakes.py"]


r = dana("addons", "new", "weather")
check("new: the addon and its kit (manifest, tools, hooks, test, harness, guides) in the site's addons/",
      r.returncode == 0 and files(ADDONS / "weather") == sorted(KIT + ["tests/test_weather.py", "tools.py"]),
      r.stdout + r.stderr)
weather = (ADDONS / "weather" / "addon.py").read_text()
check("new: the name is filled in everywhere, no page or worker",
      "{{" not in "".join((ADDONS / "weather" / f).read_text() for f in files(ADDONS / "weather"))
      and 'Addon(\n    "weather"' in weather and "web=" not in weather and "worker=" not in weather, weather)

r = dana("addons", "new", "tide_chart", "--web", "--worker", "--title", "Tide chart")
made = files(ADDONS / "tide_chart")
check("new --web --worker: a page and the route it reads, a worker and a tool calling it", r.returncode == 0 and made == sorted(
    KIT + ["api.py", "tests/test_tide_chart.py", "tools.py", "web/tide_chart.css", "web/tide_chart.js",
           "worker/Dockerfile", "worker/server.py"]), made)
tide = (ADDONS / "tide_chart" / "addon.py").read_text()
check("new --web --worker: the manifest declares the page (its id from the name) and the worker",
      'web=(Page("tide-chart", panel="tide_chart.js", style="tide_chart.css", nav=Nav("Tide chart"' in tide
      and 'worker=Worker(port=8091, url_env="TIDE_CHART_WORKER_URL")' in tide and "{{" not in tide, tide)

r = dana("addons", "new", "pager", "--web")
check("new --web: no worker files", r.returncode == 0 and not (ADDONS / "pager" / "worker").exists()
      and "worker" not in (ADDONS / "pager" / "tools.py").read_text() and (ADDONS / "pager" / "web" / "pager.js").is_file())

run = subprocess.run([sys.executable, str(SERVER / "bin" / "test"), "weather", "tide_chart", "pager"], cwd=str(SERVER),
                     env=ENV, capture_output=True, text=True, timeout=300)
check("bin/test finds the new addons' tests in the site and they pass as written",
      run.returncode == 0 and "3 passed" in run.stdout and "0 failed" in run.stdout, run.stdout[-1500:] + run.stderr[-800:])

for name, why in (("weather", "already exists"), ("tasks", "official"), ("Bad-Name", "not a usable addon name"),
                  ("_hidden", "not a usable addon name"), ("settings", "not a usable addon name"),
                  ("core", "not a usable addon name")):
    r = dana("addons", "new", name)
    check(f"new {name}: refused ({why})", r.returncode == 1 and why in r.stderr, r.stdout + r.stderr)

# The example lives at the repository root, outside the image's server/ (the container's test stage).
if EXAMPLE.parent.is_dir():
    shape = [f.replace("tide_chart", "hello") for f in made if not f.startswith("worker/")
             and f not in (".gitignore", "AGENTS.md", "REFERENCE.md", "tests/dana_harness.py", "tests/fakes.py")]
    missing = [f for f in shape if not (EXAMPLE / f).is_file()]
    check("examples/addons/hello has the scaffold's shape (every code file new --web writes, at its path)", not missing,
          missing)

print(f"\n{'All checks passed' if not failures else f'{failures} check(s) failed'}")
raise SystemExit(1 if failures else 0)
