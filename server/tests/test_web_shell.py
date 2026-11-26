"""The web shell and addons' pages: each declared page's files register with Dana.panel, page ids and core screens, the shell names no addon page, the service worker caches the shell and carries the release, /shopping.

Run: bin/test test_web_shell

Every page of every loaded addon (the official ones, plus a installed addon
``wx`` with two pages written to a temp folder) has its panel and style in
its addon's ``web/`` folder, the panel registers with ``Dana.panel`` and
every file its ``scripts`` names exists; page ids are unique and never a core
screen's (a manifest that breaks that is refused). ``static/index.html`` and
the shell's scripts mention no addon page (no ``panel-<id>``, no addon file),
the shell's core screens are the framework's, and every file the page loads
from ``/static/`` is in the service worker's ``SHELL`` list (and exists).
``GET /sw.js`` fills in the release, which changes when an addon's web file
changes. The pure parts are tested by tests/test_web_panels.mjs.
"""

import asyncio
import os
import re
import textwrap
from pathlib import Path
from typing import Callable

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("web_shell_test")
SITE = Path(testenv.scratch_dir()) / "dev_addons"
(SITE / "wx" / "web").mkdir(parents=True)
(SITE / "wx" / "addon.py").write_text(textwrap.dedent('''\
    from core.addons import Addon, Nav, Page
    addon = Addon("wx", title="Web test", web=(Page("wx", panel="wx.js", nav=Nav("Wx", icon="star")),
                                               Page("wx-more", panel="more.js", style="more.css")))
    '''), encoding="utf-8")
(SITE / "wx" / "web" / "wx.js").write_text('Dana.panel({ mount(el) { el.textContent = "wx"; } });\n', encoding="utf-8")
(SITE / "wx" / "web" / "more.js").write_text('Dana.panel({ mount(el) { el.textContent = "more"; } });\n', encoding="utf-8")
(SITE / "wx" / "web" / "more.css").write_text(".wx-more { color: red; }\n", encoding="utf-8")
os.environ["DANA_ADDON_DIRS"] = str(SITE)

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core import pages_api  # noqa: E402
from core.addons import loader  # noqa: E402
from core.addons import Addon, Page  # noqa: E402
from core.addons.addon import forget, get_addon, key_clash  # noqa: E402
from core.addons.hooks import CORE_SCREENS  # noqa: E402
from addons.shopping import api as shopping_api  # noqa: E402

STATIC = testenv.SERVER / "static"
failures = 0


def check(name: str, cond: bool, detail: object = "") -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    failures += 0 if cond else 1


# --- Each addon's pages ----------------------------------------------------------------------------
pages = {}   # page id → addon name
for name in loader.loaded():
    addon = get_addon(name)
    web_dir = loader.folder(name).path / "web"
    for page in addon.web:
        panel = web_dir / page.panel
        source = panel.read_text(encoding="utf-8") if panel.is_file() else ""
        check(f"{name}/{page.id}: its panel {page.panel} is in its web/ folder", bool(source), panel)
        if page.style:
            check(f"{name}/{page.id}: its style {page.style} is in its web/ folder", (web_dir / page.style).is_file())
        check(f"{name}/{page.id}: the panel registers with Dana.panel once", source.count("Dana.panel(") == 1)
        check(f"{name}/{page.id}: its page id is no other page's", page.id not in pages, (page.id, pages))
        check(f"{name}/{page.id}: its page id is not a core screen's", page.id not in CORE_SCREENS)
        pages[page.id] = name
        scripts = re.search(r"scripts:\s*\[([^\]]*)\]", source)
        for file in re.findall(r'"([^"]+)"', scripts.group(1)) if scripts else []:
            check(f"{name}/{page.id}: the script it loads first, {file}, is beside it", (web_dir / file).is_file())
        check(f"{name}/{page.id}: the panel uses no shell global (window.dana*, other pages' window.Dana*)",
              not re.search(r"window\.dana[A-Z]|window\.Dana(?!ShoppingCore|ProjectsCore|MoneyCore|MoneyDash|TasksCore|CallsCore|WatchesCore)[A-Z]\w*", source))
check("the official pages are all there, Tasks and Calls among them",
      {"shopping", "inbox", "calls", "tasks", "people", "projects", "money", "memory", "calendar"} <= set(pages), sorted(pages))
check("the phone addon has two pages, Inbox and Calls", [p.id for p in get_addon("phone").web] == ["inbox", "calls"])
check("a installed addon's pages are found like an official one's", pages.get("wx") == "wx" and pages.get("wx-more") == "wx")

# --- A manifest that breaks the page rules is refused ---------------------------------------------
def refused(make: Callable[[], object]) -> str:
    try:
        make()
    except (TypeError, ValueError) as e:
        return str(e)
    return ""


check("a page id must look like a screen name", "lowercase" in refused(lambda: Page("Bad Id", panel="x.js")))
check("a page may not take a core screen's id", "core screen" in refused(lambda: Page("settings", panel="x.js")))
check("a page needs its panel script", "panel" in refused(lambda: Page("nopanel", panel="")))
check("web= takes a tuple of pages, not one Page", "tuple of Page" in refused(lambda: Addon("wx_single", web=Page("single", panel="s.js"))))  # type: ignore[arg-type]
check("an addon declares a page id once", "twice" in refused(lambda: Addon("wx_twice", web=(Page("twice", panel="a.js"), Page("twice", panel="b.js")))))
clash = Addon("wx_clash", web=(Page("wx", panel="c.js"),))
check("two addons cannot share a page id (the later one fails to load)",
      "already the wx addon's" in (key_clash(clash, [get_addon("wx")]) or ""), key_clash(clash, [get_addon("wx")]))
forget("wx_clash")

# --- The shell names no addon page -----------------------------------------------------------------
index = (STATIC / "index.html").read_text(encoding="utf-8")
shell_js = {p.name: p.read_text(encoding="utf-8") for p in STATIC.glob("app*.js")} | {"dana.js": (STATIC / "dana.js").read_text()}
mentions = sorted({pid for pid in pages if f'panel-{pid}"' in index or f'data-tab="{pid}"' in index
                   or any(f'"{pid}"' in src for src in shell_js.values())})
check("index.html and the shell's scripts mention no addon page", not mentions, mentions)
nav_js = shell_js["app-nav.js"]
core_js = re.search(r"const CORE_SCREENS = \[([^\]]*)\]", nav_js)
check("the shell's core screens are the framework's (core/addons/hooks.py CORE_SCREENS)",
      core_js is not None and tuple(re.findall(r'"([a-z]+)"', core_js.group(1))) == CORE_SCREENS, core_js and core_js.group(1))
check("every core screen has its section in index.html", all(f'id="panel-{c}"' in index for c in CORE_SCREENS))
check("the core Today strip is a piece pages host, not an element of the shell",
      'id="today-strip"' not in index and "today:" in nav_js and "piece:" in shell_js["app-panels.js"])
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
        (SITE / "wx" / "web" / "wx.js").write_text('Dana.panel({ mount(el) { el.textContent = "wx 2"; } });\n')
        pages_api.web_release.cache_clear()
        changed = pages_api.web_release()
        check("an addon's web file changing changes the release (a new worker, a new cache)", changed != release, changed)
        (SITE / "wx" / "web" / "extra.css").write_text("a { color: red; }\n")
        pages_api.web_release.cache_clear()
        check("a file added to an addon's web/ changes it too", pages_api.web_release() != changed)
        r = await client.get("/shopping")
        page = await r.text()
        check("GET /shopping (the shopping addon's route) serves the web app, which opens the page by its path",
              r.status == 200 and 'id="rail-places"' in page and "/static/app-panels.js" in page)


asyncio.run(served())
print(f"\n{'All checks passed' if not failures else f'{failures} check(s) failed'}")
raise SystemExit(1 if failures else 0)
