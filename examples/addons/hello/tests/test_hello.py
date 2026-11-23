"""The hello example addon: its tool and setting, prompt line, page and route, table, event handler, Now card and action, and job, for the users who have it only.

Run from server/: PYTHONPATH=.:tests python ../examples/addons/hello/tests/test_hello.py
(``tests/test_addon_loading.py`` runs it). Robin (the first admin) and Gal
turn hello on, Maya does not.
"""

import asyncio
import datetime
import json
import os
import sys
from pathlib import Path

import testenv  # first: clean env, temp DB removed at exit, no .env (server/tests/testenv.py)
testenv.isolate("hello_example")
HERE = Path(__file__).resolve().parents[1]  # this addon's folder
os.environ.pop("DANA_ADDON_DIRS", None)  # the real sources: official, then <data dir>/addons
os.environ["API_TOKEN"] = "hello-test-env-token"  # a test value: the first admin's token (a headless install)

# Installed the way every addon is: the folder packed as for an upload, into the data dir's addons/.
from cli import remote  # noqa: E402
from core.addons import install  # noqa: E402
from core.paths import ADDONS_DIR  # noqa: E402

_ARCHIVE = Path(testenv.scratch_dir()) / "hello.tar.gz"
remote.pack_folder(HERE, _ARCHIVE)
INSTALLED = install.install_archive(_ARCHIVE, ADDONS_DIR, by="test", accept=install.manifest_touches(HERE / "addon.py"))

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core import db, events, users  # noqa: E402

APPLIED = db.migrate()
users.bootstrap()

from core import now as now_screen  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from core.addons import loader, registry, scheduler, store  # noqa: E402
from core.addons.addon import get_addon  # noqa: E402
from core.addons.registry import ToolNotAvailable  # noqa: E402
from addons.hello import store as hello_store  # noqa: E402
from addons.tasks import service as tasks_service  # noqa: E402

failures = 0


def check(name: str, cond: object, detail: object = "") -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    failures += 0 if cond else 1


ROBIN = users.first_admin()
assert ROBIN is not None
GAL = users.create("Gal")
MAYA = users.create("Maya")
store.connect(ROBIN, "hello")
store.connect(GAL, "hello", settings={"greeting": "Shalom"})
for u in (ROBIN, GAL, MAYA):
    if not store.is_connected(u, "tasks"):
        store.connect(u, "tasks")


async def main() -> None:
    check("installed from its folder into the data dir; loaded after the official ones",
          INSTALLED.folder == ADDONS_DIR / "hello" and loader.non_official() == ("hello",)
          and loader.loaded()[-1] == "hello" and loader.folder("hello").source.kind == "installed", loader.loaded())
    check("migration: its table was created", "hello:0001_baseline" in APPLIED, APPLIED)

    # Tool and setting.
    check("tool: the setting's default", await registry.execute(ROBIN, "hello_greet", {"name": "Noa"}) == "Hello, Noa!")
    check("tool: each user's own setting", await registry.execute(GAL, "hello_greet", {"name": "Noa"}) == "Shalom, Noa!")
    try:
        await registry.execute(MAYA, "hello_greet", {"name": "Noa"})
        refused = False
    except ToolNotAvailable:
        refused = True
    check("tool: refused for a user without the addon", refused and not registry.has_tool(MAYA, "hello_greet"))
    check("prompt: its line only for its users",
          all(any("hello_greet" in p for p in registry.prompt_parts(u, "guides")) for u in (ROBIN, GAL))
          and not any("hello_greet" in p for p in registry.prompt_parts(MAYA, "guides")))
    page = (HERE / "web" / "hello.js").read_text(encoding="utf-8")
    check("page: declared, its script registers with Dana.panel and reads its route",
          [p.id for p in get_addon("hello").web] == ["hello"] and "Dana.panel(" in page and "/api/hello" in page)

    # Event: a task "Greet <name>" adds a greeting, only for a user who has hello.
    tasks_service.create_task(ROBIN, title="Greet Dana")
    tasks_service.create_task(ROBIN, title="Buy milk")
    tasks_service.create_task(MAYA, title="Greet Omer")
    tasks_service.create_task(GAL, title="Greet Lior")
    await events.drain()
    check("event: the task titled 'Greet Dana' is a greeting to do", [g["name"] for g in hello_store.pending(ROBIN)]
          == ["Dana"], hello_store.pending(ROBIN))
    check("event: each user's own greetings; nothing for Maya (no hello)",
          [g["name"] for g in hello_store.pending(GAL)] == ["Lior"] and hello_store.pending(MAYA) == [])

    # Now card and its action.
    board = await now_screen.build(ROBIN)
    mine = [c for c in board["items"] if c["kind"] == "hello"]
    check("now: one card per greeting to do", [c["title"] for c in mine] == ["Greet Dana"], mine)
    theirs = await now_screen.build(MAYA)
    check("now: no card for Maya", not [c for c in theirs["items"] if c["kind"] == "hello"])
    result = await now_screen.act(ROBIN, mine[0]["id"], "done")
    check("now: 'done' marks it greeted", result.get("message") == "Greeted" and hello_store.pending(ROBIN) == [], result)

    # Job: a pass for every user who has hello; old done greetings go.
    with get_db_connection() as conn:
        conn.execute("UPDATE hello_greetings SET created_at = ?",
                     ((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=40)).isoformat(),))
        conn.commit()
    out = await scheduler.run_once(registry.job("hello.purge_old"))
    check("job: ran for its users only and purged the old greeting", out == {ROBIN.id: 1, GAL.id: 0}, out)
    check("job: wrote its heartbeat", scheduler.last_run("hello", "purge_old") is not None)

    # Route.
    hello_store.add(ROBIN, "Gal")
    app = web.Application()
    setup_auth(app)
    dict(loader.apis())["hello"].setup(app)
    async with TestClient(TestServer(app)) as c:
        async def get(user: users.User) -> tuple:
            r = await c.get("/api/hello", headers={"Authorization": "Bearer " + users.issue_token(user.id, "t")[0]})
            return r.status, await r.json()
        mine, gals, theirs = await get(ROBIN), await get(GAL), await get(MAYA)
        check("route: each user gets their greeting and who they still greet",
              mine == (200, {"greeting": "Hello", "pending": ["Gal"]})
              and gals == (200, {"greeting": "Shalom", "pending": ["Lior"]}), json.dumps([mine, gals]))
        check("route: a user without the addon is refused (403 not_connected)", theirs[0] == 403, theirs)

asyncio.run(main())
if failures:
    sys.exit(f"{failures} check(s) failed")
print("\nAll hello checks passed")
