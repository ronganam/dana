"""The hello example addon: its table, tool and setting, prompt line, event handler, Now card and action, job and route, for its user only.

Run from server/: PYTHONPATH=.:tests python ../examples/addons/hello/tests/test_hello.py
(``tests/test_addon_loading.py`` runs it). Two users: the first admin connects
hello, Maya does not.
"""

import asyncio
import datetime
import json
import os
import sys
from pathlib import Path

import testenv  # first: clean env, temp DB removed at exit, no .env (server/tests/testenv.py)
testenv.isolate("hello_example")
os.environ["DANA_ADDON_DIRS"] = str(Path(__file__).resolve().parents[2])  # examples/addons: hello is a site addon
os.environ["API_TOKEN"] = "hello-test-env-token"  # a test value: with it set, Bearer tokens decide the user

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core import db, events, users  # noqa: E402

APPLIED = db.migrate()
users.bootstrap()

from core import now as now_screen  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from core.addons import loader, registry, scheduler, store  # noqa: E402
from core.addons.registry import ToolNotAvailable  # noqa: E402
from addons.hello import store as hello_store  # noqa: E402
from addons.tasks import service as tasks_service  # noqa: E402

failures = 0


def check(name: str, cond: object, detail: object = "") -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    failures += 0 if cond else 1


ADMIN = users.first_admin()
assert ADMIN is not None
MAYA = users.create("Maya")
store.connect(ADMIN, "hello")
for u in (ADMIN, MAYA):
    if not store.is_connected(u, "tasks"):
        store.connect(u, "tasks")


async def main() -> None:
    check("loaded as a site addon, after the official ones", loader.non_official() == ("hello",)
          and loader.loaded()[-1] == "hello", loader.loaded())
    check("migration: its table was created", "hello:0001_baseline" in APPLIED, APPLIED)

    # Tool and setting.
    check("tool: the setting's default", await registry.execute(ADMIN, "hello_greet", {"name": "Noa"}) == "Hello, Noa!")
    store.update(ADMIN, "hello", settings={"greeting": "Shalom"})
    check("tool: the user's own setting", await registry.execute(ADMIN, "hello_greet", {"name": "Noa"}) == "Shalom, Noa!")
    try:
        await registry.execute(MAYA, "hello_greet", {"name": "Noa"})
        refused = False
    except ToolNotAvailable:
        refused = True
    check("tool: refused for a user without the addon", refused and not registry.has_tool(MAYA, "hello_greet"))
    check("prompt: its line only for its user",
          any("hello_greet" in p for p in registry.prompt_parts(ADMIN, "guides"))
          and not any("hello_greet" in p for p in registry.prompt_parts(MAYA, "guides")))

    # Event: a task "Greet <name>" adds a greeting, only for a user who has hello.
    tasks_service.create_task(ADMIN, title="Greet Dana")
    tasks_service.create_task(ADMIN, title="Buy milk")
    tasks_service.create_task(MAYA, title="Greet Omer")
    await events.drain()
    check("event: the task titled 'Greet Dana' is a greeting to do", [g["name"] for g in hello_store.pending(ADMIN)]
          == ["Dana"], hello_store.pending(ADMIN))
    check("event: nothing for Maya (no hello)", hello_store.pending(MAYA) == [])

    # Now card and its action.
    board = await now_screen.build(ADMIN)
    mine = [c for c in board["items"] if c["kind"] == "hello"]
    check("now: one card per greeting to do", [c["title"] for c in mine] == ["Greet Dana"], mine)
    theirs = await now_screen.build(MAYA)
    check("now: no card for Maya", not [c for c in theirs["items"] if c["kind"] == "hello"])
    result = await now_screen.act(ADMIN, mine[0]["id"], "done")
    check("now: 'done' marks it greeted", result.get("message") == "Greeted" and hello_store.pending(ADMIN) == [], result)

    # Job: a pass for every user who has hello; old done greetings go.
    with get_db_connection() as conn:
        conn.execute("UPDATE hello_greetings SET created_at = ?",
                     ((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=40)).isoformat(),))
        conn.commit()
    out = await scheduler.run_once(registry.job("hello.purge_old"))
    check("job: ran for its user only and purged the old greeting", out == {ADMIN.id: 1}, out)
    check("job: wrote its heartbeat", scheduler.last_run("hello", "purge_old") is not None)

    # Route.
    hello_store.add(ADMIN, "Gal")
    app = web.Application()
    setup_auth(app)
    for module in loader.route_modules():
        if module.__name__ == "addons.hello.api":
            module.setup(app)
    async with TestClient(TestServer(app)) as c:
        mine_r = await c.get("/api/hello", headers={"Authorization": "Bearer " + users.issue_token(ADMIN.id, "t")[0]})
        theirs_r = await c.get("/api/hello", headers={"Authorization": "Bearer " + users.issue_token(MAYA.id, "t")[0]})
        body = await mine_r.json()
        check("route: its user gets the greeting and who to greet",
              mine_r.status == 200 and body == {"greeting": "Shalom", "pending": ["Gal"]}, json.dumps(body))
        check("route: another user is refused (403 not_connected)", theirs_r.status == 403, theirs_r.status)


asyncio.run(main())
if failures:
    sys.exit(f"{failures} check(s) failed")
print("\nAll hello checks passed")
