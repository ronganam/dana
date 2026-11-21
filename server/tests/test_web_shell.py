"""The web shell and addons' pages: each declared page's files register with Dana.panel, the shell names no addon page, the service worker caches the shell and carries the release, /shopping.

Run: bin/test test_web_shell

Every loaded addon with ``web=`` (the official ones, plus a site addon
``wx`` written to a temp folder) has its panel and style in its ``web/``
folder, the panel registers with ``Dana.panel`` and every file its
``scripts`` names exists. ``static/index.html`` and the shell's scripts
mention no addon page (no ``panel-<id>``, no addon file), and every file the
page loads from ``/static/`` is in the service worker's ``SHELL`` list (and
exists). ``GET /sw.js`` fills in the release, which changes when an addon's
web file changes. The pure parts are tested by tests/test_web_panels.mjs.
"""

import asyncio
import os
import re
import textwrap
from pathlib import Path

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("web_shell_test")
SITE = Path(testenv.scratch_dir()) / "site_addons"
(SITE / "wx" / "web").mkdir(parents=True)
(SITE / "wx" / "addon.py").write_text(textwrap.dedent('''\
    from core.addons import Addon, Nav, Web
    addon = Addon("wx", title="Web test", web=Web(panel="wx.js", nav=Nav("Wx", icon="star")))
    '''), encoding="utf-8")
(SITE / "wx" / "web" / "wx.js").write_text('Dana.panel({ id: "wx", mount(el) { el.textContent = "wx"; } });\n', encoding="utf-8")
os.environ["DANA_ADDON_DIRS"] = str(SITE)

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core import pages_api  # noqa: E402
from core.addons import loader  # noqa: E402
from core.addons.addon import get_addon  # noqa: E402
from addons.shopping import api as shopping_api  # noqa: E402

STATIC = testenv.SERVER / "static"
failures = 0


def check(name: str, cond: bool, detail: object = "") -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    failures += 0 if cond else 1


# --- Each addon's page ---------------------------------------------------------------------------
pages = {}   # page id → addon name
for name in loader.loaded():
    addon = get_addon(name)
    if addon.web is None:
        continue
    web_dir = loader.folder(name).path / "web"
    panel = web_dir / addon.web.panel
    source = panel.read_text(encoding="utf-8") if panel.is_file() else ""
    check(f"{name}: its panel {addon.web.panel} is in its web/ folder", bool(source), panel)
    if addon.web.style:
        check(f"{name}: its style {addon.web.style} is in its web/ folder", (web_dir / addon.web.style).is_file())
    found = re.search(r'Dana\.panel\(\{\s*id:\s*"([a-z][a-z0-9_-]*)"', source)
    check(f"{name}: the panel registers with Dana.panel", found is not None and source.count("Dana.panel(") == 1)
    if found:
        check(f"{name}: its page id is no other addon's", found.group(1) not in pages, (found.group(1), pages))
        pages[found.group(1)] = name
    scripts = re.search(r"scripts:\s*\[([^\]]*)\]", source)
    for file in re.findall(r'"([^"]+)"', scripts.group(1)) if scripts else []:
        check(f"{name}: the script it loads first, {file}, is beside it", (web_dir / file).is_file())
    check(f"{name}: the panel uses no shell global (window.dana*, other pages' window.Dana*)",
          not re.search(r"window\.dana[A-Z]|window\.Dana(?!ShoppingCore|ProjectsCore|MoneyCore|MoneyDash)[A-Z]\w*", source))
check("the official pages are all there", {"shopping", "inbox", "people", "projects", "money", "memory", "calendar"} <= set(pages),
      sorted(pages))
check("a site addon's page is found like an official one", pages.get("wx") == "wx")

# --- The shell names no addon page -----------------------------------------------------------------
index = (STATIC / "index.html").read_text(encoding="utf-8")
shell_js = {p.name: p.read_text(encoding="utf-8") for p in STATIC.glob("app*.js")} | {"dana.js": (STATIC / "dana.js").read_text()}
mentions = sorted({pid for pid in pages if f'panel-{pid}"' in index or f'data-tab="{pid}"' in index
                   or any(f'"{pid}"' in src for src in shell_js.values())})
check("index.html and the shell's scripts mention no addon page", not mentions, mentions)
addon_files = sorted({f.name for n in pages.values() for f in (loader.folder(n).path / "web").iterdir()})
leftovers = sorted(f for f in addon_files if (STATIC / f).exists() or f"/static/{f}" in index)
check("no addon page file is left in static/ or loaded by index.html", not leftovers, leftovers)

# --- The service worker's shell list -----------------------------------------------------------------
sw = (STATIC / "sw.js").read_text(encoding="utf-8")
shell_list = re.findall(r'"(/[^"]+)"', sw[sw.index("const SHELL"):sw.index("];", sw.index("const SHELL"))])
loaded = re.findall(r'(?:src|href)="(/static/[^"]+)"', index)
check("the service worker keeps every file the page loads from /static/", not set(loaded) - set(shell_list),
      sorted(set(loaded) - set(shell_list)))
missing = [u for u in shell_list if u.startswith("/static/") and not (STATIC / u[len("/static/"):]).is_file()]
check("every file in the shell list exists", not missing, missing)
check("the service worker leaves the API and live streams to the network (it handles only the shell and /addons/*/web/)",
      "/api/" not in sw.replace("the API", "") and r"^\/addons\/[a-z0-9_]+\/web\//" in sw)


async def served() -> None:
    app = web.Application()
    pages_api.setup(app)
    shopping_api.setup(app)
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/sw.js")
        text = await r.text()
        release = pages_api.web_release()
        check("GET /sw.js: the release filled in, never cached",
              r.status == 200 and f'const RELEASE = "{release}";' in text and "__DANA_RELEASE__" not in text
              and r.headers.get("Cache-Control") == "no-cache" and "javascript" in r.headers.get("Content-Type", ""), release)
        check("the release is a short digest", re.fullmatch(r"[0-9a-f]{16}", release) is not None, release)
        (SITE / "wx" / "web" / "wx.js").write_text('Dana.panel({ id: "wx", mount(el) { el.textContent = "wx 2"; } });\n')
        pages_api.web_release.cache_clear()
        changed = pages_api.web_release()
        check("an addon's web file changing changes the release (a new worker, a new cache)", changed != release, changed)
        (SITE / "wx" / "web" / "extra.css").write_text("a { color: red; }\n")
        pages_api.web_release.cache_clear()
        check("a file added to an addon's web/ changes it too", pages_api.web_release() != changed)
        r = await client.get("/shopping")
        page = await r.text()
        check("GET /shopping (the shopping addon's route) serves the web app, which opens the page by its path",
              r.status == 200 and 'id="rail-addons"' in page and "/static/app-panels.js" in page)


asyncio.run(served())
print(f"\n{'All checks passed' if not failures else f'{failures} check(s) failed'}")
raise SystemExit(1 if failures else 0)
