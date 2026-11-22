"""The addons of a server: /api/addons (sources, states, web, failed), the server-wide disable, addon web files, the catalog, install / update / remove from git, restart, the real server start, and bin/dana addons (install, remove, disable, enable, list).

Run: bin/test test_addon_market

A site addon ``mk`` (written to a temp folder) has a tool, a prompt part, a
job, a service, an event handler, a notify channel, a Now card and action, a
route and a ``web/`` folder; ``mkplus`` needs it; ``mkbroken`` fails to load.
A local git repository (made here) holds addons at two commits; ``greeter``
is installed from its first commit into ``<data dir>/addons`` before the
server loads. ``watcher`` declares a host at the first commit and also acting
outside Dana at the second (an update that adds a touch asks again);
``samey`` declares the same touches at both; ``vague`` writes its touches
with a name, not plain values. The catalog is a local file (``DANA_CATALOG_FILE``). The first
admin and Bob have ``mk``; Carl has nothing. No network.
"""

import asyncio
import io
import json
import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
DB_PATH = testenv.isolate("addon_market_test")
os.environ["API_TOKEN"] = "market-env-token"  # a test value: the first admin's token (a headless install)
SCRATCH = Path(testenv.scratch_dir())
SITE = SCRATCH / "site_addons"
DATA_ADDONS = Path(DB_PATH).parent / "addons"   # core.paths.ADDONS_DIR for this database
REPO = SCRATCH / "repo"
CATALOG = SCRATCH / "catalog.json"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


# --- The site addons -------------------------------------------------------------------------------
write(SITE / "mk" / "addon.py", '''\
    from core.addons import Addon, Nav, Page, PromptPart
    addon = Addon("mk", title="Market test", description="A site addon for the market test", version="1.2.3",
                  prompt_parts=(PromptPart("guides", "Use mk_ping to ping."),),
                  web=(Page("mk", panel="panel.js", style="panel.css", nav=Nav("Market", icon="store", order=70)),))
    ''')
write(SITE / "mk" / "tools.py", '''\
    from core.addons import Connection
    from addons.mk.addon import addon

    @addon.tool()
    def mk_ping(conn: Connection) -> str:
        """Ping."""
        return "pong"
    ''')
write(SITE / "mk" / "hooks.py", '''\
    import asyncio
    from core import cards
    from core.addons import Card, minutes
    from addons.mk.addon import addon

    SEEN = []
    STARTS = []
    STOPS = []

    @addon.job(every=minutes(5))
    async def tick(user):
        return user.name

    @addon.service
    async def watch(app):
        STARTS.append(1)
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            STOPS.append(1)
            raise

    @addon.on("tasks.task.created")
    async def on_task(user, data):
        SEEN.append(("event", user.id))

    @addon.channel("mk_beacon")
    async def beacon(user, message):
        SEEN.append(("channel", user.id))
        return True

    @addon.now_cards
    def mk_cards(user, ctx):
        return [Card(id="mk:1", kind="mk", source="mk", title="Ping")]

    @addon.now_action("mk")
    async def act(user, ref, action, args, now):
        return cards.result("Done")
    ''')
write(SITE / "mk" / "api.py", '''\
    from aiohttp import web
    from core.auth import request_user
    from core.addons import store

    async def get_mk(request):
        store.require(request_user(request), "mk")
        return web.json_response({"ok": True})

    def setup(app):
        app.router.add_get("/api/mk", get_mk)
    ''')
write(SITE / "mk" / "web" / "panel.js", "Dana.panel({mount() {}});\n")
write(SITE / "mk" / "web" / "panel.css", ".mk { color: red; }\n")
write(SITE / "mk" / "web" / ".hidden", "not served\n")
write(SITE / "mk" / "web" / "sub" / "deep.js", "// deep\n")
write(SCRATCH / "outside.txt", "OUTSIDE-SECRET\n")
os.symlink(SCRATCH / "outside.txt", SITE / "mk" / "web" / "link.js")
write(SITE / "mkplus" / "addon.py", '''\
    from core.addons import Addon, Need
    addon = Addon("mkplus", description="Needs mk", needs=(Need("addon", "mk", "Its data"),))
    ''')
write(SITE / "mkbroken" / "addon.py", "raise RuntimeError('mkbroken is broken on purpose')\n")


# --- The git repository with addons at two commits ----------------------------------------------------

def git(*args: str, cwd: Path = REPO) -> str:
    return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout.strip()


def addon_py(name: str, version: str, declared: str = "", touches: str = "") -> str:
    return (f"from core.addons import Addon, Touches\naddon = Addon({(declared or name)!r}, description='From git', "
            f"version={version!r}{', touches=' + touches if touches else ''})\n")


WATCH_HOST = 'Touches(hosts=("api.example.com",))'
WATCH_MORE = 'Touches(hosts=("api.example.com",), acts_outside=True)'


REPO.mkdir()
git("init", "-q")
write(REPO / "addons" / "greeter" / "addon.py", addon_py("greeter", "1.0.0"))
write(REPO / "addons" / "fresh" / "addon.py", addon_py("fresh", "0.1.0"))
write(REPO / "addons" / "tasks" / "addon.py", addon_py("tasks", "9.9.9"))          # an official name
write(REPO / "addons" / "mk" / "addon.py", addon_py("mk", "9.9.9"))                # a site addon's name
write(REPO / "addons" / "mismatch" / "addon.py", addon_py("mismatch", "1.0.0", declared="other"))
write(REPO / "addons" / "noaddon" / "README.md", "no addon.py here\n")
write(REPO / "addons" / "linky" / "addon.py", addon_py("linky", "1.0.0"))
os.symlink("/etc/hostname", REPO / "addons" / "linky" / "host.txt")
write(REPO / "addons" / "future" / "addon.py", addon_py("future", "1.0.0"))
write(REPO / "addons" / "watcher" / "addon.py", addon_py("watcher", "1.0.0", touches=WATCH_HOST))
write(REPO / "addons" / "samey" / "addon.py", addon_py("samey", "1.0.0", touches=WATCH_HOST))
write(REPO / "addons" / "vague" / "addon.py", "HOSTS = ('api.example.com',)\n" + addon_py("vague", "1.0.0", touches="Touches(hosts=HOSTS)"))
git("add", "-A")
git("commit", "-qm", "one")
COMMIT1 = git("rev-parse", "HEAD")
write(REPO / "addons" / "greeter" / "addon.py", addon_py("greeter", "1.1.0"))
write(REPO / "addons" / "watcher" / "addon.py", addon_py("watcher", "1.1.0", touches=WATCH_MORE))
write(REPO / "addons" / "samey" / "addon.py", addon_py("samey", "1.1.0", touches=WATCH_HOST))
git("commit", "-qam", "two")
COMMIT2 = git("rev-parse", "HEAD")


def entry(name: str, ref: str, path: str = "", **extra: Any) -> Dict[str, Any]:
    return {"name": name, "title": name.title(), "description": f"The {name} addon", "author": "Test",
            "source": {"repo": str(REPO), "path": path or f"addons/{name}", "ref": ref}, **extra}


CATALOG.write_text(json.dumps({"addons": [
    entry("greeter", COMMIT2, tags=["demo"], min_dana="9.0.0"),
    entry("hello", "FILL-AT-RELEASE"),
    entry("Bad-Name", COMMIT1),
    entry("greeter", COMMIT1),
    entry("mismatch", COMMIT1),
    entry("future", COMMIT1, min_dana="99.0"),
    entry("watcher", COMMIT2, touches={"hosts": ["api.example.com"], "acts_outside": True}),
    entry("samey", COMMIT2, touches={"hosts": ["api.example.com"]}),
    entry("badtouch", COMMIT1, touches={"hosts": "api.example.com"}),
]}), encoding="utf-8")
os.environ["DANA_CATALOG_FILE"] = str(CATALOG)
os.environ["DANA_ADDON_DIRS"] = f"{SITE}{os.pathsep}{DATA_ADDONS}"

from core.addons import install  # noqa: E402  (folders only: nothing of the app is loaded yet)

DATA_ADDONS.mkdir(parents=True)
install.install(install.Source(str(REPO), "addons/greeter", COMMIT1), DATA_ADDONS, by="test")

from aiohttp import ClientSession, web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from yarl import URL  # noqa: E402

from core import db, events, lifecycle, server, users  # noqa: E402

db.migrate()
users.bootstrap()

from core.addons import catalog, loader, registry, scheduler, state, store  # noqa: E402
from core.addons.registry import ToolNotAvailable  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from cli import main as cli  # noqa: E402

loader.load()
from addons.mk import hooks as mk_hooks  # noqa: E402

results: List[Tuple[str, bool]] = []


def check(name: str, cond: Any, detail: Any = "") -> None:
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"\n       {str(detail)[:600]}" if detail and not cond else ""))


ADMIN = users.first_admin()
assert ADMIN is not None
BOB = users.create("Bob")
CARL = users.create("Carl")
for u in (ADMIN, BOB):
    store.connect(u, "mk")
store.connect(ADMIN, "mkplus")
TOKENS = {u.id: users.issue_token(u.id, "t")[0] for u in (BOB, CARL)}
ADMIN_AUTH = {"Authorization": "Bearer market-env-token"}
BOB_AUTH = {"Authorization": f"Bearer {TOKENS[BOB.id]}"}
CARL_AUTH = {"Authorization": f"Bearer {TOKENS[CARL.id]}"}


async def call(c: TestClient, method: str, path: str, auth: Dict[str, str], body: Any = None) -> Tuple[int, Any]:
    r = await c.request(method, path, headers=auth, json=body, allow_redirects=False)
    text = await r.text()
    try:
        return r.status, json.loads(text)
    except ValueError:
        return r.status, text


def by_name(body: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {i["name"]: i for i in body["addons"]}


# --- 1. /api/addons ----------------------------------------------------------------------------------

async def test_list(c: TestClient) -> None:
    status, body = await call(c, "GET", "/api/addons", ADMIN_AUTH)
    items = by_name(body)
    mk, greeter, broken = items.get("mk", {}), items.get("greeter", {}), items.get("mkbroken", {})
    check("list: loaded addons with their source (official, site, installed) and the failed one",
          status == 200 and items["tasks"]["source"] == "official" and mk.get("source") == "site"
          and greeter.get("source") == "installed" and broken.get("state") == "failed", list(items))
    check("list: mk's shape (title, version, state, needs, touches, web URLs and nav while on, no worker)",
          mk["title"] == "Market test" and mk["version"] == "1.2.3" and mk["state"] == "on" and mk["needs"] == []
          and mk["touches"] == {"hosts": [], "acts_outside": False, "stores_credentials": False, "reads": []}
          and mk["web"] == [{"id": "mk", "panel": "/addons/mk/web/panel.js", "style": "/addons/mk/web/panel.css",
                             "nav": {"label": "Market", "icon": "store", "order": 70, "primary": False}}] and mk["worker"] is False, mk)
    check("list: needs are listed with their kind, why and whether optional",
          items["mkplus"]["needs"] == [{"kind": "addon", "what": "mk", "why": "Its data", "optional": False}],
          items["mkplus"]["needs"])
    wa = items["whatsapp"]
    check("list: an official addon's needs and touches (WhatsApp: a WAHA server, optional keys; its server, "
          "acting outside, stored credentials, other addons' data)",
          [n["kind"] for n in wa["needs"] if not n["optional"]] == ["hardware", "account"]
          and {"kind": "env", "what": "WAHA_WEBHOOK_SECRET", "optional": True}.items() <= next(
              n for n in wa["needs"] if n["what"] == "WAHA_WEBHOOK_SECRET").items()
          and wa["touches"] == {"hosts": ["your WAHA server"], "acts_outside": True, "stores_credentials": True,
                                "reads": ["knowledge", "people"]}, wa)
    check("list: an addon that did not load has no touches", broken.get("touches", "missing") is None
          and broken.get("needs") == [], broken)
    check("list: the admin sees where an installed addon came from",
          greeter["installed"] == {"repo": str(REPO), "path": "addons/greeter", "commit": COMMIT1}
          and greeter["version"] == "1.0.0" and greeter["state"] == "available", greeter)
    check("list: the admin sees why an addon failed", "mkbroken is broken on purpose" in broken.get("error", ""), broken)
    check("list: nothing to restart for yet", body["restart_needed"] is False and body["pending"] == [], body.get("pending"))
    status, body = await call(c, "GET", "/api/addons", CARL_AUTH)
    items = by_name(body)
    check("list (member): mk can be turned on, no web while off, no error of the failed addon, no install record",
          items["mk"]["state"] == "available" and items["mk"]["web"] == [] and "error" not in items["mkbroken"]
          and items["mkbroken"]["state"] == "failed" and "installed" not in items["greeter"]
          and body["can_manage"] is False and "pending" not in body, items["mk"])


# --- 2. Web files ------------------------------------------------------------------------------------

async def test_web_files(c: TestClient) -> None:
    r = await c.get("/addons/mk/web/panel.js", headers=CARL_AUTH)
    text = await r.text()
    check("web: a loaded addon's panel is served (any signed-in user)", r.status == 200
          and text == "Dana.panel({mount() {}});\n" and "javascript" in r.headers.get("Content-Type", ""),
          (r.status, r.headers.get("Content-Type")))
    r = await c.get("/addons/mk/web/sub/deep.js", headers=ADMIN_AUTH)
    check("web: a file in a sub-folder", r.status == 200 and await r.text() == "// deep\n")
    r = await c.get("/addons/mk/web/panel.css", headers=ADMIN_AUTH)
    check("web: CSS with its type", r.status == 200 and "text/css" in r.headers.get("Content-Type", ""))
    for path in ("/addons/mk/web/%2e%2e/addon.py", "/addons/mk/web/..%2faddon.py", "/addons/mk/web/sub/%2e%2e/%2e%2e/addon.py",
                 "/addons/mk/web/.hidden", "/addons/mk/web/link.js", "/addons/mk/web/%2e%2e/%2e%2e/%2e%2e/outside.txt",
                 "/addons/mk/web/sub", "/addons/mk/web/nope.js", "/addons/nope/web/panel.js",
                 "/addons/mkbroken/web/x.js", "/addons/mk/web/%5c..%5caddon.py"):
        r = await c.get(URL(path, encoded=True), headers=ADMIN_AUTH, allow_redirects=False)
        body = await r.text()
        check(f"web: refused {path}", r.status == 404 and "OUTSIDE-SECRET" not in body and "Addon(" not in body,
              (r.status, body[:100]))
    r = await c.get("/addons/mk/web/panel.js", allow_redirects=False)
    check("web: needs a sign-in (redirect to the login page)", r.status == 302 and "/login" in r.headers.get("Location", ""))


# --- 3. Disabling on the server -------------------------------------------------------------------------

async def test_disable(c: TestClient) -> None:
    scheduler.STATE_POLL_SECONDS = 0.05
    svc = next(s for s in registry.services() if s.key == "mk.watch")
    service_task = asyncio.create_task(scheduler._run_service(svc, None))
    await asyncio.sleep(0.15)
    check("before: the service runs", mk_hooks.STARTS == [1], mk_hooks.STARTS)
    check("before: tool, prompt part, hooks, job, channel for the users who have mk",
          registry.has_tool(ADMIN, "mk_ping") and any("mk_ping" in p for p in registry.prompt_parts(ADMIN, "guides"))
          and [h.addon for h in registry.hooks("now_cards", ADMIN)].count("mk") == 1
          and registry.hook_for("now_action", "mk", None) is not None
          and any(ch.addon == "mk" for ch in registry.channels(ADMIN)))
    out = await scheduler.run_once(registry.job("mk.tick"))
    check("before: the job runs for both users", out == {ADMIN.id: ADMIN.name, BOB.id: "Bob"}, out)
    beat = scheduler.last_run("mk", "tick")
    status, _ = await call(c, "GET", "/api/mk", BOB_AUTH)
    check("before: its route answers", status == 200)

    status, body = await call(c, "POST", "/api/addons/mk/disable", BOB_AUTH)
    check("disable: a member is refused (403 manage_server)", status == 403 and body == {"error": "forbidden",
                                                                                         "needs": "manage_server"})
    check("disable: still enabled after the refusal", state.is_enabled("mk"))
    status, body = await call(c, "POST", "/api/addons/mk/disable", ADMIN_AUTH)
    check("disable: the admin disables it; the payload says so",
          status == 200 and body["state"] == "disabled-on-server" and body["can_change"] is False
          and body["why_not"] == "Disabled on this server" and body["web"] == [] and body["connected"] is True, body)

    check("disabled: no tool for anyone", not registry.has_tool(ADMIN, "mk_ping") and not registry.has_tool(BOB, "mk_ping")
          and all(t.name != "mk_ping" for t in registry.tools_for(ADMIN)))
    try:
        await registry.execute(ADMIN, "mk_ping", {})
        refused = False
    except ToolNotAvailable:
        refused = True
    check("disabled: running its tool is refused like an unknown tool", refused)
    check("disabled: no prompt part", not any("mk_ping" in p for p in registry.prompt_parts(ADMIN, "guides")))
    check("disabled: no composer hooks (for a user, without one, by key)",
          not [h for h in registry.hooks("now_cards", ADMIN) if h.addon == "mk"]
          and not [h for h in registry.hooks("now_cards", None) if h.addon == "mk"]
          and registry.hook_for("now_action", "mk", None) is None)
    check("disabled: no notify channel", not any(ch.addon == "mk" for ch in registry.channels(ADMIN)))
    check("disabled: not connected for anyone (and mkplus, which needs it, neither)",
          "mk" not in store.connected_addons(ADMIN) and "mkplus" not in store.connected_addons(ADMIN)
          and store.get(ADMIN, "mk") is None and store.users_with("mk") == []
          and store.get(ADMIN, "mk", disabled_too=True) is not None)
    out = await scheduler.run_once(registry.job("mk.tick"))
    check("disabled: the job does nothing, no heartbeat", out == {} and scheduler.last_run("mk", "tick") == beat, out)
    mk_hooks.SEEN.clear()
    events.emit("tasks.task.created", ADMIN, task_id="t1", title="x")
    await events.drain()
    check("disabled: its event handler is not called", not [s for s in mk_hooks.SEEN if s[0] == "event"], mk_hooks.SEEN)
    await asyncio.sleep(0.2)
    check("disabled: its service was stopped", mk_hooks.STOPS == [1] and mk_hooks.STARTS == [1],
          (mk_hooks.STARTS, mk_hooks.STOPS))
    status, body = await call(c, "GET", "/api/mk", BOB_AUTH)
    check("disabled: its route answers 404 addon_disabled", status == 404 and body == {"error": "addon_disabled",
                                                                                      "addon": "mk"}, body)
    r = await c.get("/addons/mk/web/panel.js", headers=ADMIN_AUTH)
    check("disabled: its web files are not served", r.status == 404)
    status, body = await call(c, "PUT", "/api/addons/mk", CARL_AUTH, {})
    check("disabled: turning it on is refused (409 addon_disabled)", status == 409 and body["error"] == "addon_disabled"
          and not store.get(CARL, "mk", disabled_too=True))
    status, body = await call(c, "GET", "/api/addons", CARL_AUTH)
    items = by_name(body)
    check("disabled: the list says so for everyone, mkplus too",
          items["mk"]["state"] == "disabled-on-server" and items["mkplus"]["state"] == "disabled-on-server")
    status, me = await call(c, "GET", "/api/me", ADMIN_AUTH)
    check("disabled: not in /api/me addons", "mk" not in me["addons"] and "essentials" in me["addons"])
    status, body = await call(c, "POST", "/api/addons/essentials/disable", ADMIN_AUTH)
    check("disable: essentials cannot be disabled (400)", status == 400 and body["error"] == "cannot_disable"
          and state.is_enabled("essentials"))
    status, body = await call(c, "POST", "/api/addons/nope/disable", ADMIN_AUTH)
    check("disable: an unknown addon is 404", status == 404)

    status, body = await call(c, "POST", "/api/addons/mk/enable", ADMIN_AUTH)
    check("enable: back on", status == 200 and body["state"] == "on" and registry.has_tool(BOB, "mk_ping")
          and "mkplus" in store.connected_addons(ADMIN), body.get("state"))
    await asyncio.sleep(0.2)
    check("enable: the service started again", mk_hooks.STARTS == [1, 1], mk_hooks.STARTS)
    status, _ = await call(c, "GET", "/api/mk", BOB_AUTH)
    check("enable: its route answers again", status == 200)
    service_task.cancel()
    await asyncio.gather(service_task, return_exceptions=True)


# --- 4. The catalog ----------------------------------------------------------------------------------

async def test_catalog(c: TestClient) -> None:
    shipped = catalog.parse(json.loads(catalog.BUNDLED.read_text(encoding="utf-8")))
    check("server/catalog.json (shipped in the image): every entry is valid, hello among them",
          [e.name for e in shipped.entries] == ["hello", "israel_rail"] and shipped.problems == [], shipped.problems)
    saved_file = os.environ.pop("DANA_CATALOG_FILE")
    os.environ["DANA_CATALOG_URL"] = "http://127.0.0.1:9/catalog.json"
    try:
        fallback = catalog.load(fresh=True)
    finally:
        os.environ["DANA_CATALOG_FILE"] = saved_file
        del os.environ["DANA_CATALOG_URL"]
    check("catalog URL unreachable: the shipped copy is used, with a notice naming the URL",
          [e.name for e in fallback.entries] == ["hello", "israel_rail"] and "127.0.0.1:9" in fallback.notice
          and fallback.origin == "http://127.0.0.1:9/catalog.json", fallback.notice)
    check("catalog from a file: no notice", catalog.load(fresh=True).notice == "")
    src = {"repo": "https://example.invalid/r.git", "path": "", "ref": "c" * 40}
    iconed = catalog.parse({"addons": [{"name": "iconed", "icon": "cloud-sun", "source": src},
                                       {"name": "badicon", "icon": "Cloud Sun", "source": src}]})
    check("catalog: an entry's optional icon is a Lucide name (kept), anything else skips the entry",
          [e.icon for e in iconed.entries] == ["cloud-sun"] and iconed.entries[0].to_json()["icon"] == "cloud-sun"
          and any(p.startswith("badicon:") and "icon" in p for p in iconed.problems), iconed.problems)
    status, body = await call(c, "GET", "/api/addons/catalog", BOB_AUTH)
    check("catalog: admins only", status == 403)
    status, body = await call(c, "GET", "/api/addons/catalog", ADMIN_AUTH)
    entries = {e["name"]: e for e in body.get("entries", [])}
    check("catalog: valid entries kept, the others skipped with a reason",
          status == 200 and list(entries) == ["greeter", "mismatch", "future", "watcher", "samey"]
          and body["origin"] == str(CATALOG)
          and len(body["problems"]) == 4 and any("hello" in p for p in body["problems"])
          and any(p.startswith("badtouch:") and "hosts" in p for p in body["problems"])
          and any("Bad-Name" in p for p in body["problems"]) and any("twice" in p for p in body["problems"]), body)
    g = entries.get("greeter", {})
    check("catalog: merged with what is installed (commit, loaded, update available)",
          g.get("installed") == {"commit": COMMIT1, "loaded": True, "version": "1.0.0", "touches": NOTHING}
          and g["update_available"] is True and g["touches"] is None and g["touches_added"] is None
          and g["present"] == "installed" and g["official"] is False and g["compatible"] is True
          and g["source"] == {"repo": str(REPO), "path": "addons/greeter", "ref": COMMIT2} and g["tags"] == ["demo"], g)
    check("catalog: an entry for a newer Dana is not compatible", entries["future"]["compatible"] is False
          and entries["future"]["installed"] is None)
    check("catalog: an entry's touches are shown before installing (missing parts: none)",
          entries["watcher"]["touches"] == {"hosts": ["api.example.com"], "acts_outside": True,
                                            "stores_credentials": False, "reads": []}
          and entries["watcher"]["installed"] is None and entries["watcher"]["touches_added"] is None, entries["watcher"])


# --- 5. Install, update, remove ---------------------------------------------------------------------

async def test_install(c: TestClient) -> None:
    status, body = await call(c, "POST", "/api/addons/install", BOB_AUTH, {"name": "greeter"})
    check("install: admins only", status == 403)
    status, body = await call(c, "POST", "/api/addons/install", ADMIN_AUTH, {"name": "greeter"})
    record = json.loads((DATA_ADDONS / "greeter" / ".installed.json").read_text())
    check("update from the catalog: the newer commit replaces the folder; a restart is needed",
          status == 201 and body["replaced"] is True and body["commit"] == COMMIT2 and body["restart_needed"] is True
          and record["commit"] == COMMIT2 and record["installed_by"] == ADMIN.id
          and "1.1.0" in (DATA_ADDONS / "greeter" / "addon.py").read_text(), body)
    check("update: no .git copied", not (DATA_ADDONS / "greeter" / ".git").exists())
    status, body = await call(c, "POST", "/api/addons/install", ADMIN_AUTH,
                              {"repo": str(REPO), "path": "addons/fresh", "ref": COMMIT2.upper()})
    check("install from any repo + commit: a new addon in the data dir", status == 201 and body["name"] == "fresh"
          and body["replaced"] is False and (DATA_ADDONS / "fresh" / "addon.py").is_file(), body)
    status, body = await call(c, "GET", "/api/addons", ADMIN_AUTH)
    check("after installing: restart needed, the changes listed",
          body["restart_needed"] is True and {"name": "greeter", "change": "update"} in body["pending"]
          and {"name": "fresh", "change": "install"} in body["pending"], body.get("pending"))

    refusals = [
        ({"repo": str(REPO), "path": "addons/fresh", "ref": COMMIT2[:12]}, 400, "full 40-character"),
        ({"repo": str(REPO), "path": "addons/fresh", "ref": "master"}, 400, "full 40-character"),
        ({"repo": str(REPO), "path": "addons/fresh", "ref": "0" * 40}, 400, "git failed"),
        ({"repo": str(REPO), "path": "../outside", "ref": COMMIT1}, 400, "inside the repository"),
        ({"repo": "--upload-pack=touch /tmp/x", "path": "", "ref": COMMIT1}, 400, "git URL"),
        ({"repo": str(REPO), "path": "addons/tasks", "ref": COMMIT1}, 400, "official addon"),
        ({"repo": str(REPO), "path": "addons/mk", "ref": COMMIT1}, 400, "already in"),
        ({"repo": str(REPO), "path": "addons/noaddon", "ref": COMMIT1}, 400, "no addon.py"),
        ({"repo": str(REPO), "path": "addons/linky", "ref": COMMIT1}, 400, "symbolic links"),
        ({"repo": str(REPO), "path": "addons/nothere", "ref": COMMIT1}, 400, "no folder"),
        ({"name": "mismatch"}, 400, "declares 'other'"),
        ({"name": "future"}, 409, "needs Dana 99.0"),
        ({"name": "nope"}, 404, "no addon 'nope'"),
        ({}, 400, "Give the name"),
    ]
    for payload, want, words in refusals:
        status, body = await call(c, "POST", "/api/addons/install", ADMIN_AUTH, payload)
        check(f"install refused: {payload} → {want}", status == want and words in str(body.get("message", "")), body)
    check("refusals left nothing behind", sorted(p.name for p in DATA_ADDONS.iterdir()) == ["fresh", "greeter"],
          sorted(p.name for p in DATA_ADDONS.iterdir()))

    status, body = await call(c, "POST", "/api/addons/fresh/remove", BOB_AUTH)
    check("remove: admins only", status == 403 and (DATA_ADDONS / "fresh").is_dir())
    status, body = await call(c, "POST", "/api/addons/fresh/remove", ADMIN_AUTH)
    check("remove: the folder is deleted", status == 200 and body == {"name": "fresh", "removed": True,
                                                                     "restart_needed": True}
          and not (DATA_ADDONS / "fresh").exists(), body)
    for name, want in (("tasks", 400), ("mk", 400), ("nope", 404)):
        status, body = await call(c, "POST", f"/api/addons/{name}/remove", ADMIN_AUTH)
        check(f"remove: {name} → {want}", status == want and (name == "nope" or (SITE / name).is_dir()
                                                              or name == "tasks"), body)
    status, body = await call(c, "POST", "/api/addons/greeter/remove", ADMIN_AUTH)
    status, body = await call(c, "GET", "/api/addons", ADMIN_AUTH)
    check("after removing a loaded installed addon: a restart unloads it",
          {"name": "greeter", "change": "remove"} in body["pending"], body.get("pending"))


NOTHING = {"hosts": [], "acts_outside": False, "stores_credentials": False, "reads": []}
HOST_ONLY = {**NOTHING, "hosts": ["api.example.com"]}
ACTS_ONLY = {**NOTHING, "acts_outside": True}


async def test_touches(c: TestClient) -> None:
    status, body = await call(c, "POST", "/api/addons/install", ADMIN_AUTH,
                              {"repo": str(REPO), "path": "addons/watcher", "ref": COMMIT1})
    check("install: an addon that touches something is not installed until the admin agrees (409 with its touches)",
          status == 409 and body.get("error") == "confirm_touches" and body["name"] == "watcher"
          and body["touches"] == HOST_ONLY and body["added"] == HOST_ONLY and not (DATA_ADDONS / "watcher").exists(), body)
    status, body = await call(c, "POST", "/api/addons/install", ADMIN_AUTH,
                              {"repo": str(REPO), "path": "addons/watcher", "ref": COMMIT1, "accept": body["touches"]})
    check("install: agreed to what it touches → installed, its touches in the answer",
          status == 201 and body["touches"] == HOST_ONLY and (DATA_ADDONS / "watcher" / "addon.py").is_file(), body)
    status, body = await call(c, "GET", "/api/addons/catalog", ADMIN_AUTH)
    w = next(e for e in body["entries"] if e["name"] == "watcher")
    check("catalog: an update whose entry adds a touch says which (touches_added), with what is installed",
          w["update_available"] is True and w["installed"]["touches"] == HOST_ONLY and w["touches_added"] == ACTS_ONLY, w)
    status, body = await call(c, "POST", "/api/addons/install", ADMIN_AUTH, {"name": "watcher"})
    record = json.loads((DATA_ADDONS / "watcher" / ".installed.json").read_text())
    check("update: a new touch asks again (409: added = only what is new), nothing replaced",
          status == 409 and body.get("error") == "confirm_touches" and body["added"] == ACTS_ONLY
          and body["touches"] == {**HOST_ONLY, "acts_outside": True} and record["commit"] == COMMIT1, body)
    status, body = await call(c, "POST", "/api/addons/install", ADMIN_AUTH, {"name": "watcher", "accept": HOST_ONLY})
    check("update: agreeing to less than it now touches still asks", status == 409 and body["added"] == ACTS_ONLY, body)
    status, body = await call(c, "POST", "/api/addons/install", ADMIN_AUTH,
                              {"name": "watcher", "accept": w["touches"]})
    check("update: agreed to the new touch → updated", status == 201 and body["commit"] == COMMIT2
          and json.loads((DATA_ADDONS / "watcher" / ".installed.json").read_text())["commit"] == COMMIT2, body)

    status, body = await call(c, "POST", "/api/addons/install", ADMIN_AUTH,
                              {"repo": str(REPO), "path": "addons/samey", "ref": COMMIT1, "accept": HOST_ONLY})
    check("install: samey at its first commit, its touches agreed", status == 201, body)
    status, body = await call(c, "GET", "/api/addons/catalog", ADMIN_AUTH)
    sm = next(e for e in body["entries"] if e["name"] == "samey")
    check("catalog: an update with the same touches adds none", sm["update_available"] is True
          and sm["touches_added"] is None, sm)
    status, body = await call(c, "POST", "/api/addons/install", ADMIN_AUTH, {"name": "samey"})
    check("update: the same touches → no question, updated", status == 201 and body["commit"] == COMMIT2
          and body["replaced"] is True, body)

    status, body = await call(c, "POST", "/api/addons/install", ADMIN_AUTH,
                              {"repo": str(REPO), "path": "addons/vague", "ref": COMMIT1})
    check("install: touches not written as plain values are refused", status == 400
          and "plain values" in body.get("message", "") and not (DATA_ADDONS / "vague").exists(), body)
    status, body = await call(c, "POST", "/api/addons/install", ADMIN_AUTH, {"name": "samey", "accept": {"hosts": "x"}})
    check("install: a malformed accept is refused", status == 400 and "accept" in body.get("message", ""), body)
    for name in ("watcher", "samey"):
        await call(c, "POST", f"/api/addons/{name}/remove", ADMIN_AUTH)


async def test_restart(c: TestClient) -> None:
    status, _ = await call(c, "POST", "/api/server/restart", BOB_AUTH)
    check("restart: admins only", status == 403 and not lifecycle.restart_requested())
    status, body = await call(c, "POST", "/api/server/restart", ADMIN_AUTH)
    check("restart: answered first", status == 202 and body == {"restarting": True} and not lifecycle.restart_requested())
    await asyncio.wait_for(lifecycle.wait_for_restart(), 3)
    check("restart: then asked for (main.py exits for the supervisor)", lifecycle.restart_requested())


# --- 6. bin/dana addons --------------------------------------------------------------------------------

def run(*argv: str) -> Tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    try:
        rc = cli.main(list(argv), stdout=out, stdin=io.StringIO(""), stderr=err)
    except SystemExit as e:
        rc = int(e.code or 0)
    return rc, out.getvalue(), err.getvalue()


def test_cli() -> None:
    target = SCRATCH / "cli_site"
    rc, out, err = run("addons", "install", f"{REPO}@{COMMIT1}:addons/fresh", "--dir", str(target))
    check("cli install: REPO@COMMIT:PATH into a folder, with the full-access warning", rc == 0
          and (target / "fresh" / ".installed.json").is_file() and "full access" in out, out + err)
    rc, out, err = run("addons", "install", "greeter", "--dir", str(target))
    check("cli install: a catalog name", rc == 0 and json.loads((target / "greeter" / ".installed.json").read_text())
          ["commit"] == COMMIT2, out + err)
    rc, out, err = run("addons", "install", f"{REPO}@master:addons/fresh", "--dir", str(target))
    check("cli install: a branch is refused", rc == 1 and "neither a catalog name" in err, err)
    rc, out, err = run("addons", "install", f"{REPO}@{COMMIT1}:addons/tasks", "--dir", str(target))
    check("cli install: an official name is refused", rc == 1 and "official addon" in err, err)
    rc, out, err = run("addons", "install", f"{REPO}@{COMMIT2}:addons/watcher", "--dir", str(target))
    check("cli install: an addon that touches something asks first; no answer → not installed",
          rc == 1 and "watcher may touch:" in out and "connects to api.example.com" in out
          and "acts for you outside Dana" in out and "Not installed" in err and not (target / "watcher").exists(),
          out + err)
    rc, out, err = run("addons", "install", f"{REPO}@{COMMIT2}:addons/watcher", "--dir", str(target), "--yes")
    check("cli install: --yes agrees to what it touches", rc == 0 and (target / "watcher" / "addon.py").is_file(),
          out + err)
    rc, out, err = run("addons", "table")
    check("cli table: a Markdown table of the official addons (not site or installed ones)", rc == 0
          and out.startswith("| Feature | What it does | Required | Optional | Region |") and "\n| Tasks |" in out
          and "Market test" not in out and "`GOOGLE_CLIENT_ID`" in out, out[:800])
    rc, out, err = run("addons", "remove", "fresh", "--dir", str(target))
    check("cli remove", rc == 0 and not (target / "fresh").exists(), err)
    rc, out, err = run("addons", "disable", "mk")
    check("cli disable: off for everyone, mkplus with it", rc == 0 and not state.is_enabled("mk")
          and "mkplus" in out, out + err)
    rc, out, err = run("addons", "list")
    check("cli list: sources, the state, the failed addon", rc == 0 and "mk: A site addon for the market test (v1.2.3, site, "
          "disabled on this server" in out and f"installed @ {COMMIT1[:12]}" in out and "mkbroken: FAILED" in out, out)
    rc, out, err = run("addons", "enable", "mk")
    check("cli enable", rc == 0 and state.is_enabled("mk"))
    rc, out, err = run("addons", "disable", "essentials")
    check("cli disable: essentials refused", rc == 1 and "every user has it" in err, err)


async def test_start_listener() -> None:
    """The real start (main.py's start_webhook_listener): routes, loops and the scheduler, then a clean stop."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    runner = await server.start_webhook_listener(port=port)
    try:
        async with ClientSession() as s, s.get(f"http://127.0.0.1:{port}/api/health") as r:
            check("start: the server main.py starts answers", r.status == 200, r.status)
        check("start: the addons' background work is stopped on cleanup",
              scheduler.stop in runner.app.on_cleanup)
    finally:
        await runner.cleanup()


async def main() -> None:
    app = web.Application()
    setup_auth(app)
    server.setup_routes(app)
    async with TestClient(TestServer(app)) as c:
        await test_list(c)
        await test_web_files(c)
        await test_disable(c)
        await test_catalog(c)
        await test_install(c)
        await test_touches(c)
        test_cli()
        await test_restart(c)
    await test_start_listener()


asyncio.run(main())
passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
