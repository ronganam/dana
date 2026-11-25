"""The showcase example addon: every capability once (tools, approval, undo, blocked, settings rule, connection test, options, prompt, model, HTTP service, worker, job, service, event, channel, Now card and action, day item, briefing, review, search, self-check, context, provider, route, page, dashboard widget, live activity, migration), for the users who have it only.

Run from server/: bin/dana addons test ../examples/addons/showcase (``tests/test_addon_kit.py``
runs it in Dana's suite). Robin (the first admin) and Gal turn it on, Maya does not.
"""

import asyncio
import datetime
import os
import socket
import subprocess
import sys
from unittest.mock import patch

import dana_harness  # first: finds Dana (DANA_HOME), a clean environment and a temporary database
dana_harness.start("showcase_test")
os.environ["API_TOKEN"] = "showcase-test-env-token"  # a test value: with it set, Bearer tokens decide the user

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core import clock, db, events, live, users  # noqa: E402

APPLIED = db.migrate()
users.bootstrap()

from core import weekly_review  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from core.addons import (BriefingContext, ContextRequest, InvalidValue, NotConnected, NowContext,  # noqa: E402
                         WidgetContext)
from core.addons import loader, registry, scheduler, store  # noqa: E402
from core.addons.addon import get_addon  # noqa: E402
from core.addons.registry import ToolNotAvailable  # noqa: E402
from addons.showcase import hooks as showcase_hooks  # noqa: E402
from addons.showcase import store as plants  # noqa: E402
import fakes  # noqa: E402

failures = 0


def check(name: str, cond: object, detail: object = "") -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    failures += 0 if cond else 1


ROBIN, GAL, MAYA = fakes.three_users("showcase", credentials={"api_key": "pk-test-robin"},
                                     gal_settings={"room": "balcony"}, gal_credentials={})
LLM = fakes.FakeLLM(lambda messages, purpose: "Green leaves, quiet light;\nbasil grows through the night.").install()
NOW = clock.now()


def hook(kind: str, user: users.User, key: str = ""):
    """The showcase's hook of ``kind`` for ``user`` (None: not offered to them)."""
    return next((h for h in registry.hooks(kind, user) if h.addon == "showcase" and (not key or h.key == key)), None)


def age(plant_id: int, days: float) -> None:
    """Pretend a plant was last watered ``days`` ago."""
    with get_db_connection() as conn:
        conn.execute("UPDATE showcase_plants SET last_watered = ? WHERE id = ?",  # scoped: a test fixture, by id
                     ((NOW - datetime.timedelta(days=days)).isoformat(timespec="seconds"), plant_id))
        conn.commit()


async def tools_and_manifest() -> None:
    check("loaded beside the official addons; its migration ran", "showcase" in loader.non_official()
          and "showcase:0001_baseline" in APPLIED, (loader.failed(), APPLIED))
    check("on_connect: each user who turned it on got a first plant (Basil); Maya nothing",
          [p["name"] for p in plants.plants(ROBIN)] == ["Basil"] and [p["name"] for p in plants.plants(GAL)] == ["Basil"]
          and plants.plants(MAYA) == [])
    added = await registry.execute(ROBIN, "showcase_add_plant", {"name": "Fern", "every_days": 7})
    fern = next(p for p in plants.plants(ROBIN) if p["name"] == "Fern")
    check("tool: adds a plant for the caller only", added.startswith("Added Fern") and fern["every_days"] == 7
          and [p["name"] for p in plants.plants(GAL)] == ["Basil"], added)
    listed = await registry.execute(GAL, "showcase_plants", {})
    check("tool: each user lists their own plants", [p["name"] for p in listed] == ["Basil"], listed)
    for name in ("showcase_plants", "showcase_add_plant"):
        try:
            await registry.execute(MAYA, name, {"name": "Cactus"} if name == "showcase_add_plant" else {})
            refused = False
        except ToolNotAvailable:
            refused = True
        check(f"tool {name}: refused for Maya (no addon), not offered to her", refused and not registry.has_tool(MAYA, name))
    watered = await registry.execute(ROBIN, "showcase_water", {"plant_id": fern["id"]})
    check("tool: water marks the plant watered", watered == "Watered Fern." and plants.plant(ROBIN, fern["id"])["last_watered"])
    check("tool: another user's plant id answers like a missing one",
          await registry.execute(GAL, "showcase_water", {"plant_id": fern["id"]}) == "Error: no such plant.")
    schema = next(s for s in registry.tool_schemas(ROBIN) if s["function"]["name"] == "showcase_add_plant")["function"]
    check("schema: from the docstring and type hints, conn left out",
          schema["description"] == "Add a house plant to look after."
          and set(schema["parameters"]["properties"]) == {"name", "every_days"}
          and schema["parameters"]["required"] == ["name"]
          and schema["parameters"]["properties"]["every_days"]["type"] == "integer", schema)
    check("confirm: showcase_forget asks first, the others do not",
          registry.needs_confirmation("showcase_forget", {"plant_id": fern["id"]})
          and not registry.needs_confirmation("showcase_water", {"plant_id": fern["id"]}))
    card = get_addon("showcase").tools[[t.name for t in get_addon("showcase").tools].index("showcase_forget")]
    approval = card.describe(ROBIN, {"plant_id": fern["id"]})
    check("describe: the Approve card names the plant", approval.title == "Forget Fern" and approval.emoji == "🪴", approval)
    store.update(GAL, "showcase", settings={"paused": True})
    paused = await registry.execute(GAL, "showcase_plants", {})
    store.update(GAL, "showcase", settings={"paused": False})
    check("blocked: a paused connection's tools answer with the reason, not run",
          paused == "Error: Plant care is paused in your settings.", paused)
    try:
        store.update(ROBIN, "showcase", settings={"remind_hour": 3})
        rule = ""
    except InvalidValue as e:
        rule = e.field
    check("validate: a reminder hour outside the day is refused", rule == "remind_hour")
    options = await get_addon("showcase").options(store.require(ROBIN, "showcase"))
    check("options: the rooms offered for the room setting", [o["value"] for o in options["room"]]
          == ["kitchen", "bedroom", "balcony"], options)
    check("prompt section: the user's own plants; nothing for Maya",
          "Basil, Fern" in registry.prompt_section(ROBIN, "showcase")
          and "Fern" not in registry.prompt_section(GAL, "showcase") and registry.prompt_section(MAYA, "showcase") == "")
    check("prompt parts: only for its users", any("showcase_" in p for p in registry.prompt_parts(ROBIN, "guides"))
          and not any("showcase_" in p for p in registry.prompt_parts(MAYA, "guides")))
    poem = await registry.execute(ROBIN, "showcase_poem", {"plant_id": fern["id"]})
    check("model: the poem comes from core.llm under its purpose (the fake answered)",
          poem.startswith("Green leaves") and LLM.calls[-1]["purpose"] == "showcase_poem"
          and "Fern" in LLM.calls[-1]["messages"][0]["content"], (poem, LLM.calls))


async def outside() -> None:
    def tips(request: web.Request) -> tuple:
        if request.headers.get("Authorization") != "Bearer pk-test-robin":
            return 401, {"error": "bad key"}
        return 200, {"tip": f"Keep the {request.query['plant']} soil moist."}

    async with fakes.FakeHTTP({"GET /tips": tips}) as svc:
        os.environ["SHOWCASE_API_URL"] = svc.url
        tip = await registry.execute(ROBIN, "showcase_tip", {"plant": "fern"})
        check("HTTP: the tip from the plant service, asked with the user's own key",
              tip == "Keep the fern soil moist." and svc.requests[-1]["query"] == {"plant": "fern"}, tip)
        refused = await registry.execute(GAL, "showcase_tip", {"plant": "fern"})
        check("HTTP: a user without a key gets the service's refusal in plain words",
              refused == "Error: The plant service refused the API key.", refused)
        test = get_addon("showcase").test
        check("test=: Robin's connection works, Gal's (no key) says why",
              await test(store.require(ROBIN, "showcase")) is None
              and await test(store.require(GAL, "showcase")) == "The plant service refused the API key.")
    os.environ.pop("SHOWCASE_API_URL")

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    worker = subprocess.Popen([sys.executable, str(dana_harness.ADDON / "worker" / "server.py")],
                              env={**os.environ, "PORT": str(port)})
    os.environ["SHOWCASE_WORKER_URL"] = f"http://127.0.0.1:{port}"
    try:
        answer = ""
        for _ in range(50):  # until it listens
            try:
                answer = await registry.execute(ROBIN, "showcase_worker_ping", {})
                break
            except OSError:
                await asyncio.sleep(0.1)
        check("worker: the tool reaches the sidecar", answer == "The showcase worker is up", answer)
    finally:
        worker.terminate()
        worker.wait(10)


async def background() -> None:
    fern = next(p for p in plants.plants(ROBIN) if p["name"] == "Fern")
    age(fern["id"], 1)
    out = await scheduler.run_once(registry.job("showcase.remind"))
    check("job: a pass for its users only, each counting their thirsty plants (never-watered Basil)",
          out == {ROBIN.id: 1, GAL.id: 1}, out)
    check("channel: the reminder was delivered to the showcase channel, per user",
          plants.inbox(ROBIN) == ["Water today: Basil"] and plants.inbox(GAL) == ["Water today: Basil"]
          and plants.inbox(MAYA) == [], plants.inbox(ROBIN))
    check("service: registered once for the server", "showcase.tick" in [s.key for s in registry.services()]
          and showcase_hooks.TICKS["count"] == 0)
    events.emit("tasks.task.created", ROBIN, title="Water Monstera")
    events.emit("tasks.task.created", MAYA, title="Water Cactus")
    events.emit("tasks.task.created", GAL, title="Buy soil")
    await events.drain()
    check("event: 'Water Monstera' added the plant for Robin; nothing for Maya or for another title",
          "Monstera" in [p["name"] for p in plants.plants(ROBIN)] and plants.plants(MAYA) == []
          and [p["name"] for p in plants.plants(GAL)] == ["Basil"])


async def composers() -> None:
    ctx = NowContext(now=NOW, hidden={})
    mine = hook("now_cards", ROBIN).func(ROBIN, ctx)
    check("now card: one per thirsty plant (Basil, Monstera; the Fern was watered yesterday)",
          [c.title for c in mine] == ["Water Basil", "Water Monstera"] and mine[0].id.startswith("plant:"), mine)
    check("now card: none offered to Maya", hook("now_cards", MAYA) is None)
    action = registry.hook_for("now_action", "plant", ROBIN)
    basil = mine[0].id.split(":", 1)[1]
    done = await action.func(ROBIN, basil, "water", {}, NOW)
    check("now action: 'water' waters that plant", done.get("message") == "Watered"
          and plants.plant(ROBIN, int(basil))["last_watered"], done)
    try:
        registry.hook_for("now_action", "plant", MAYA)
        blocked = False
    except NotConnected:
        blocked = True
    check("now action: refused for Maya (403 not_connected)", blocked)
    items = hook("day_items", ROBIN).func(ROBIN, ctx)
    check("day item: today's watering at the reminder hour", [(i.title, i.at.hour) for i in items]
          == [("Water 1 plant", 9)], items)
    brief = hook("briefing", ROBIN, "plants").func(ROBIN, BriefingContext(now=NOW))
    check("briefing: the plants that need water, with the push count",
          brief.data == {"thirsty": ["Monstera"]} and brief.counts == {"thirsty_plants": 1}, brief)
    old = next(p for p in plants.plants(ROBIN) if p["name"] == "Fern")
    age(old["id"], 20)
    review = hook("review", ROBIN, "plants").func(ROBIN, BriefingContext(now=NOW))
    check("review: a plant not watered for two weeks is an item to decide",
          [(i["type"], i["title"]) for i in review.items] == [("plant", "Fern")], review)
    check("review action: its actions, then 'forget' applied for Robin only",
          weekly_review.actions_for("plant") == ("forget",)
          and await weekly_review._apply_one({"ref": str(old["id"]), "type": "plant", "title": "Fern"}, "forget", {},
                                              NOW.date(), ROBIN) == "Forgot Fern"
          and plants.plant(ROBIN, old["id"]) is None)
    hits = hook("search", ROBIN).func(ROBIN, "monst", 5)
    check("search: the user's matching plants only", [h.title for h in hits] == ["Monstera"]
          and hook("search", GAL).func(GAL, "monst", 5) == [], hits)
    monstera = next(p for p in plants.plants(ROBIN) if p["name"] == "Monstera")
    age(monstera["id"], 10)
    late = hook("selfcheck", ROBIN, "showcase_overdue").func(ROBIN, NOW)
    fine = hook("selfcheck", GAL, "showcase_overdue").func(GAL, NOW)
    check("self-check: fails while a plant waited twice its interval, passes otherwise",
          late.ok is False and "Monstera" in late.why and fine.ok is True, (late, fine))
    req = ContextRequest(block=lambda kind, ref: "")
    text = registry.hook_for("context", "plant", ROBIN).func(ROBIN, str(monstera["id"]), req)
    card = registry.hook_for("context_card", "plant", ROBIN).func(ROBIN, str(monstera["id"]))
    try:
        registry.hook_for("context_card", "plant", GAL).func(GAL, str(monstera["id"]))
        hidden = False
    except LookupError:
        hidden = True
    check("context: about the user's plant; the card is refused for another user's plant",
          "Monstera" in text and card["title"] == "Monstera" and hidden
          and registry.hook_for("context_block", "plant", ROBIN).func(ROBIN, str(monstera["id"])) == "Plant: Monstera")
    provider = registry.provider("showcase.thirsty_count", ROBIN)
    check("provide: a named capability for its users; None for Maya",
          provider is not None and provider.func(ROBIN) == 1 and registry.provider("showcase.thirsty_count", MAYA) is None)
    widget = hook("widget", ROBIN, "plants_thirsty")
    small = widget.func(ROBIN, WidgetContext(settings={"heading": "Water me"}, size="small", now=NOW))
    check("widget: the user's thirsty plants under the heading they set; offered to its users only",
          small == {"heading": "Water me", "plants": ["Monstera"]} and widget.option("sizes") == ("small", "medium")
          and hook("widget", MAYA, "plants_thirsty") is None, small)


async def web_parts() -> None:
    page = (dana_harness.ADDON / "web" / "plants.js").read_text(encoding="utf-8")
    drawn = (dana_harness.ADDON / "web" / "widget.js").read_text(encoding="utf-8")
    check("widget: its script registers its renderer with Dana.widget",
          'Dana.widget("plants_thirsty"' in drawn and hook("widget", ROBIN, "plants_thirsty").option("script") == "widget.js")
    check("page: declared, its script registers with Dana.panel and reads its route",
          [p.id for p in get_addon("showcase").web] == ["plants"] and "Dana.panel(" in page
          and "/api/showcase/plants" in page)
    app = web.Application()
    setup_auth(app)
    dict(loader.apis())["showcase"].setup(app)
    async with TestClient(TestServer(app)) as c:
        async def get(user: users.User) -> tuple:
            r = await c.get("/api/showcase/plants",
                            headers={"Authorization": "Bearer " + users.issue_token(user.id, "t")[0]})
            return r.status, await r.json()
        (s1, mine), (s2, gals), (s3, _) = await get(ROBIN), await get(GAL), await get(MAYA)
        check("route: each user gets their own room and plants",
              s1 == 200 and mine["room"] == "kitchen" and [p["name"] for p in mine["plants"]] == ["Basil", "Monstera"]
              and s2 == 200 and gals["room"] == "balcony" and [p["name"] for p in gals["plants"]] == ["Basil"],
              (mine, gals))
        check("route: a user without the addon is refused (403 not_connected)", s3 == 403)


async def live_activity() -> None:
    basil = next(p for p in plants.plants(GAL) if p["name"] == "Basil")
    age(basil["id"], 5)
    started = await registry.execute(GAL, "showcase_water_now", {"plant_id": basil["id"], "minutes": 2})
    current = live.get(GAL, get_addon("showcase"), f"water:{basil['id']}")
    wire = live.to_json(current) if current else {}
    check("live: 'water now' starts a watering timer for the caller only (countdown, droplets, two buttons)",
          started.startswith("Watering Basil") and wire.get("icon") == "droplets"
          and wire.get("countdown", {}).get("label") == "Done in" and [a["kind"] for a in wire.get("actions", [])]
          == ["server", "stop"] and live.active(ROBIN) == [] and live.active(MAYA) == [], wire)
    later = current.activity.countdown.to + datetime.timedelta(seconds=1)
    with patch.object(clock, "now", lambda: later):
        await live.tick()
    watered = plants.plant(GAL, basil["id"])["last_watered"]
    check("live_refresh: once the countdown is over the plant is watered and the activity ended",
          live.active(GAL) == [] and watered and datetime.datetime.fromisoformat(watered) >= later
          - datetime.timedelta(seconds=1), watered)
    await registry.execute(GAL, "showcase_water_now", {"plant_id": basil["id"]})
    ended = await live.act(GAL, f"showcase:water:{basil['id']}", "watered")
    check("live_action: 'Watered' ends it early", ended is None and live.active(GAL) == [])
    await live.drain()


async def main() -> None:
    await tools_and_manifest()
    await outside()
    await background()
    await composers()
    await live_activity()
    await web_parts()


asyncio.run(main())
if failures:
    sys.exit(f"{failures} check(s) failed")
print("\nAll showcase checks passed")
