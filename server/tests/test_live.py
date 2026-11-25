"""Live activities (core/live.py, core/live_api.py): validation, start / update with no-op updates dropped, rev, batched pushes on a fake clock, expiry, finals kept then purged, selection kept across updates, stop calling the addon's live_action, the refresh loop calling due activities only, the API per user, the ``live`` push event and the wire fixture (tests/fixtures/live_web.json).

Run: bin/test test_live  (WRITE_WEB_FIXTURE=1 rewrites the fixture from the code)

Two installed addons (a DANA_ADDON_DIRS folder): ``rail`` declares ``live_refresh`` and
``live_action``, ``plain`` neither. Robin and Gal have the phone addon, Maya does not (she
gets no push). ``clock.now`` is a fake clock: no real sleeps.
"""

import asyncio
import datetime
import json
import os
import textwrap
import zoneinfo
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("live_test")
os.environ["TIMEZONE"] = "Asia/Jerusalem"  # the wire's offsets are the server zone's

ROOT = Path(testenv.scratch_path("addon_dirs"))
for name in ("rail", "plain", "slow"):
    (ROOT / name).mkdir(parents=True)
(ROOT / "rail" / "addon.py").write_text("from core.addons import Addon\naddon = Addon('rail', title='Rail', "
                                        "description='Live test')\n")
(ROOT / "plain" / "addon.py").write_text("from core.addons import Addon\naddon = Addon('plain', description='No hooks')\n")
(ROOT / "rail" / "hooks.py").write_text(textwrap.dedent('''\
    from dataclasses import replace
    from core import live
    from addons.rail.addon import addon

    CALLS = []
    NEXT = {}   # (user id, key) -> what the next refresh returns ("boom" raises)

    @addon.live_refresh(every=60)
    async def refresh(user, current):
        CALLS.append(("refresh", user.id, current.key))
        result = NEXT.pop((user.id, current.key), None)
        if result == "boom":
            raise RuntimeError("refresh bug")
        return result

    @addon.live_action
    async def act(user, current, action, choice):
        CALLS.append(("action", user.id, current.key, action, choice))
        if action == "boom":
            raise RuntimeError("action bug")
        if action == "arrived":
            return live.END
        if action == "later":
            return replace(current.activity, title="Later, as asked")
        return None
    '''))
(ROOT / "slow" / "addon.py").write_text("from core.addons import Addon\naddon = Addon('slow', description='Hangs')\n")
(ROOT / "slow" / "hooks.py").write_text(textwrap.dedent('''\
    import asyncio
    from addons.slow.addon import addon

    CALLS = []
    RELEASE = asyncio.Event()

    @addon.live_refresh(every=5)
    async def refresh(user, current):
        CALLS.append(current.key)
        await RELEASE.wait()
        return None
    '''))
os.environ["DANA_ADDON_DIRS"] = str(ROOT)

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core import clock, db, live, live_api, notify, users  # noqa: E402
from core.addons import store  # noqa: E402
from core.addons.addon import Addon, get_addon  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from core.live import Action, Activity, Choice, Countdown, Current, Final, Point, Progress, Seg  # noqa: E402

db.migrate()
users.bootstrap()
from addons.rail import hooks as rail  # noqa: E402
from addons.slow import hooks as slow  # noqa: E402

FIXTURE = testenv.FIXTURES / "live_web.json"
TZ = zoneinfo.ZoneInfo("Asia/Jerusalem")
results: List[Tuple[str, bool]] = []


def check(name: str, cond: Any, detail: Any = "") -> None:
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


ROBIN = users.first_admin()
assert ROBIN is not None
GAL = users.create("Gal")
MAYA = users.create("Maya")
for u in (ROBIN, GAL, MAYA):
    store.connect(u, "rail")
store.connect(ROBIN, "plain")
store.connect(ROBIN, "slow")
for u in (ROBIN, GAL):  # Maya has no phone addon
    store.connect(u, "phone")
RAIL: Addon = get_addon("rail")
PLAIN: Addon = get_addon("plain")
SLOW: Addon = get_addon("slow")
AUTH = {u.id: {"Authorization": f"Bearer {users.issue_token(u.id, 'test')[0]}"} for u in (ROBIN, GAL, MAYA)}

# The fake clock: clock.now() reads it; tests move it.
CLOCK = {"t": datetime.datetime(2026, 11, 25, 17, 0, 0, tzinfo=TZ)}
clock.now = lambda: CLOCK["t"]


def at(**delta: float) -> datetime.datetime:
    CLOCK["t"] = CLOCK["t"] + datetime.timedelta(**delta)
    return CLOCK["t"]


# Every live push, recorded on its way through core.notify.send to the phone addon's channel.
SENT: List[Tuple[str, Dict[str, Any], bool]] = []
_real_send = notify.send


async def _recording_send(user: users.User, message: notify.Message) -> Dict[str, bool]:
    if message.kind == "live":
        SENT.append((user.id, message.data, message.replace))
    return await _real_send(user, message)


notify.send = _recording_send


def pushes(user: users.User) -> List[Dict[str, Any]]:
    return [data for uid, data, _ in SENT if uid == user.id]


def T(h: int, m: int, s: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 11, 25, h, m, s, tzinfo=TZ)


def train(title: str = "Departs 17:46 (+4)") -> Activity:
    """The train-like activity of the fixture: countdown, progress, three actions, two choices."""
    first = Activity(title=title, text="Platform 3 · Arrives 18:35", tone="warn",
                     countdown=Countdown(T(17, 46), "Departs"))
    return replace(first, context="Central → Harbour · Train 404", icon="train-front",
                   progress=Progress(segments=(Seg(4, "warn"), Seg(6, "warn")), points=(Point(4, "neutral"),),
                                     value=0, tracker="train-front"),
                   actions=(Action("prev", "Earlier", "prev"), Action("next", "Next train", "next"),
                            Action("stop", "Stop", "stop")),
                   choices=(Choice("404", first),
                            Choice("406", Activity(title="Departs 17:52", text="Platform 1 · Arrives 18:41",
                                                   tone="good", countdown=Countdown(T(17, 52), "Departs")))),
                   selected="404", open="/rail", alert="delay:4", ends_at=T(18, 40))


def fixture_payload() -> Dict[str, Any]:
    """The fixture: (a) the train, (b) an ended coding-agent session with a final."""
    running = Current(id="rail:watch-12", addon="rail", key="watch-12", activity=train(), selected="404", rev=4,
                      started_at=T(17, 5), updated_at=T(17, 30, 2))
    ended = Current(id="phone:agent-claude-abc", addon="phone", key="agent-claude-abc",
                    activity=Activity(title="Fix the parser", text="Running the tests", icon="bot",
                                      since=T(17, 1), chip="3/7"),
                    selected="", rev=9, started_at=T(17, 1), updated_at=T(17, 29), ended_at=T(17, 29),
                    final=Final("Done · Fix the parser", "All 42 tests pass", "good"))
    return {"activities": [live.to_json(running), live.to_json(ended)]}


def test_fixture() -> None:
    data = fixture_payload()
    if os.environ.get("WRITE_WEB_FIXTURE"):
        FIXTURE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    on_disk = json.loads(FIXTURE.read_text(encoding="utf-8"))
    check("fixture: tests/fixtures/live_web.json is what to_json gives (WRITE_WEB_FIXTURE=1 rewrites it)",
          on_disk == data)
    a, b = data["activities"]
    check("wire: the train carries every field of the contract, ISO times with the zone's offset",
          a["id"] == "rail:watch-12" and a["source"] == "Rail" and a["rev"] == 4
          and a["updated_at"] == "2026-11-25T17:30:02+02:00" and a["countdown"] == {
              "to": "2026-11-25T17:46:00+02:00", "label": "Departs"}
          and a["progress"] == {"segments": [{"length": 4, "tone": "warn"}, {"length": 6, "tone": "warn"}],
                                "points": [{"at": 4, "tone": "neutral"}], "value": 0, "tracker": "train-front",
                                "indeterminate": False, "styled_by_progress": True}
          and [x["kind"] for x in a["actions"]] == ["prev", "next", "stop"]
          and a["choices"][1] == {"key": "406", "title": "Departs 17:52", "text": "Platform 1 · Arrives 18:41",
                                  "tone": "good", "countdown": {"to": "2026-11-25T17:52:00+02:00", "label": "Departs"}}
          and a["selected"] == "404" and a["open"] == "/rail" and a["alert"] == "delay:4"
          and a["ends_at"] == "2026-11-25T18:40:00+02:00" and "ended" not in a and "refresh_at" not in a, a)
    check("wire: the ended one has since, chip, ended and its final; empty fields are left out (no nulls)",
          b["since"] == "2026-11-25T17:01:00+02:00" and b["chip"] == "3/7" and b["ended"] is True
          and b["final"] == {"title": "Done · Fix the parser", "text": "All 42 tests pass", "tone": "good"}
          and b["source"] == "Phone" and "countdown" not in b and "choices" not in b and "selected" not in b
          and "null" not in json.dumps(data), b)


def refused(activity: Any) -> str:
    try:
        live._check(activity)
    except live.LiveError as e:
        return str(e) or "refused"
    return ""


async def test_validation() -> None:
    naive = datetime.datetime(2026, 11, 25, 18, 0)
    good = train()
    check("validation: the train is fine", refused(good) == "")
    bad = {
        "empty title": Activity(title="  "),
        "long title": Activity(title="x" * 121),
        "long text": Activity(title="t", text="x" * 241),
        "long context": Activity(title="t", context="x" * 121),
        "long chip": Activity(title="t", chip="12345678"),
        "unknown tone": Activity(title="t", tone="purple"),
        "unknown icon": Activity(title="t", icon="rocket"),
        "unknown tracker": Activity(title="t", progress=Progress(tracker="rocket")),
        "countdown and since": Activity(title="t", countdown=Countdown(T(18, 0)), since=T(17, 0)),
        "naive countdown": Activity(title="t", countdown=Countdown(naive)),
        "long countdown label": Activity(title="t", countdown=Countdown(T(18, 0), "x" * 25)),
        "four actions": Activity(title="t", actions=tuple(Action(f"a{i}", "A") for i in range(4))),
        "repeated action id": Activity(title="t", actions=(Action("a", "A"), Action("a", "B"))),
        "unknown action kind": Activity(title="t", actions=(Action("a", "A", "jump"),)),
        "two stops": Activity(title="t", actions=(Action("s1", "S", "stop"), Action("s2", "S", "stop"))),
        "a server action called select": Activity(title="t", actions=(Action("select", "S"),)),
        "long action label": Activity(title="t", actions=(Action("a", "x" * 25),)),
        "nine choices": Activity(title="t", choices=tuple(Choice(str(i), Activity(title="c")) for i in range(9))),
        "repeated choice key": Activity(title="t", choices=(Choice("a", Activity(title="c")),
                                                            Choice("a", Activity(title="d")))),
        "selected not a choice": Activity(title="t", choices=(Choice("a", Activity(title="c")),), selected="b"),
        "a choice without a title": Activity(title="t", choices=(Choice("a", Activity(title="")),)),
        "open over http": Activity(title="t", open="http://example.com"),
        "open protocol-relative": Activity(title="t", open="//evil.example"),
        "segment of 0": Activity(title="t", progress=Progress(segments=(Seg(0),))),
        "point past the end": Activity(title="t", progress=Progress(segments=(Seg(3),), points=(Point(4),))),
        "value past the end": Activity(title="t", progress=Progress(value=101)),
        "naive ends_at": Activity(title="t", ends_at=naive),
    }
    big = Activity(title="t", progress=Progress(segments=tuple(Seg(1) for _ in range(2000))))
    bad["over 16 KB"] = big
    missed = [name for name, a in bad.items() if not refused(a)]
    check("validation: every bad activity is refused with a reason", not missed, missed)
    check("validation: the reason says what", "chip" in refused(bad["long chip"])
          and "16384" in refused(big) and "countdown and since" in refused(bad["countdown and since"]))
    check("validation: a Dana path, an https URL and no progress segments (total 100) are fine",
          refused(Activity(title="t", open="/money")) == "" and refused(Activity(title="t", open="https://x.example/a"))
          == "" and refused(Activity(title="t", progress=Progress(value=100, points=(Point(100),)))) == "")
    for key in ("", "has space", "a/b", "x" * 101):
        try:
            await live.start(ROBIN, RAIL, key, Activity(title="t"))
            ok = False
        except live.LiveError:
            ok = True
        check(f"validation: key {key[:12]!r} refused", ok)
    try:
        await live.end(ROBIN, RAIL, "nothing", Final(""))
        ok = False
    except live.LiveError:
        ok = True
    check("validation: a final without a title is refused", ok)


async def test_start_update() -> None:
    at(minutes=1)
    SENT.clear()
    c = await live.start(ROBIN, RAIL, "watch-12", train())
    await live.drain()
    check("start: rev 1, the choice resolved, stored and listed for Robin only",
          c.id == "rail:watch-12" and c.rev == 1 and c.selected == "404" and live.get(ROBIN, RAIL, "watch-12") == c
          and [x.id for x in live.active(ROBIN)] == ["rail:watch-12"] and live.active(GAL) == []
          and live.get(GAL, RAIL, "watch-12") is None, c)
    check("start: pushed at once, the whole state, replace=True",
          len(SENT) == 1 and SENT[0][0] == ROBIN.id and SENT[0][2] is True
          and SENT[0][1] == live.payload(ROBIN) and SENT[0][1]["activities"][0]["rev"] == 1, SENT)
    stored = live.to_json(live.get(ROBIN, RAIL, "watch-12"))
    want = live.to_json(replace(c, rev=1))
    check("start: what is read back is what was given", stored == want
          and stored["countdown"]["to"] == "2026-11-25T17:46:00+02:00", stored)

    at(seconds=1)
    same = await live.update(ROBIN, RAIL, "watch-12", train())
    await live.drain()
    check("update: an unchanged activity is dropped (no rev, no push)",
          same is not None and same.rev == 1 and live.get(ROBIN, RAIL, "watch-12").rev == 1 and len(SENT) == 1)
    silent = await live.update(ROBIN, RAIL, "watch-12", replace(train(), refresh_at=T(17, 40)))
    await live.drain()
    check("update: a change clients do not see (refresh_at) is stored without a rev or push",
          silent.rev == 1 and live.get(ROBIN, RAIL, "watch-12").activity.refresh_at == T(17, 40) and len(SENT) == 1)

    at(seconds=1)
    moved = await live.update(ROBIN, RAIL, "watch-12", train("Departs 17:47 (+5)"))
    await live.drain()
    check("update: a change bumps rev; within 10 s of the last push it waits (batched)",
          moved.rev == 2 and live.get(ROBIN, RAIL, "watch-12").rev == 2 and len(SENT) == 1)
    at(seconds=1)
    await live.update(ROBIN, RAIL, "watch-12", train("Departs 17:48 (+6)"))
    at(seconds=3)
    out = await live.tick()
    await live.drain()
    check("batching: a tick before the interval sends nothing", len(SENT) == 1 and out["pushed"] == 0, out)
    at(seconds=5)
    out = await live.tick()
    await live.drain()
    check("batching: once 10 s passed, one trailing push carries the latest",
          len(SENT) == 2 and out["pushed"] == 1 and SENT[1][1]["activities"][0]["title"] == "Departs 17:48 (+6)"
          and SENT[1][1]["activities"][0]["rev"] == 3, SENT[-1])
    out = await live.tick()
    check("batching: nothing left to send", out["pushed"] == 0 and len(SENT) == 2)

    at(seconds=1)
    await live.update(ROBIN, RAIL, "watch-12", replace(train("Departs 17:48 (+6)"), alert="delay:6"))
    await live.drain()
    check("alert: a new non-empty alert goes at once", len(SENT) == 3 and SENT[2][1]["activities"][0]["alert"]
          == "delay:6")
    at(seconds=1)
    await live.update(ROBIN, RAIL, "watch-12", replace(train("Departs 17:49 (+7)"), alert="delay:6"))
    await live.drain()
    at(seconds=1)
    await live.update(ROBIN, RAIL, "watch-12", replace(train("Departs 17:49 (+7)"), alert=""))
    at(seconds=1)
    await live.update(ROBIN, RAIL, "watch-12", replace(train("Departs 17:49 (+7)"), alert="delay:6"))
    await live.drain()
    check("alert: the same alert again (even after a gap) is batched like any change", len(SENT) == 3)
    at(seconds=11)
    await live.tick()
    await live.drain()
    check("rev: one per change clients see", live.get(ROBIN, RAIL, "watch-12").rev == 7
          and SENT[-1][1]["activities"][0]["rev"] == 7, live.get(ROBIN, RAIL, "watch-12").rev)

    check("update: None for an activity that is not running",
          await live.update(ROBIN, RAIL, "nope", Activity(title="t")) is None
          and await live.update(GAL, RAIL, "watch-12", Activity(title="t")) is None)
    try:
        await live.update(ROBIN, RAIL, "watch-12", Activity(title=""))
        ok = False
    except live.LiveError:
        ok = True
    check("update: an invalid activity raises LiveError", ok)

    restarted = await live.start(ROBIN, RAIL, "watch-12", train())
    await live.drain()
    check("start again: replaces the running one with a later rev, pushed at once",
          restarted.rev == 8 and len(live.active(ROBIN)) == 1 and SENT[-1][1]["activities"][0]["rev"] == 8)


async def test_selection() -> None:
    at(seconds=30)
    three = Activity(title="Pick", choices=tuple(Choice(k, Activity(title=k.upper())) for k in ("a", "b", "c")))
    c = await live.start(ROBIN, RAIL, "pick", three)
    await live.drain()
    check("selection: none given → the first, written on the wire", c.selected == "a"
          and live.to_json(c)["selected"] == "a")
    rail.CALLS.clear()
    n = len(SENT)
    picked = await live.select(ROBIN, "rail:pick", "b")
    await live.drain()
    check("select: stored, rev bumped, pushed at once, then live_action gets 'select' with the key",
          picked.selected == "b" and picked.rev == 2 and live.get(ROBIN, RAIL, "pick").selected == "b"
          and len(SENT) == n + 1 and rail.CALLS == [("action", ROBIN.id, "pick", "select", "b")], rail.CALLS)
    rail.CALLS.clear()
    same = await live.select(ROBIN, "rail:pick", "b")
    check("select: the same choice again changes nothing (no hook)", same.rev == 2 and rail.CALLS == [])
    kept = await live.update(ROBIN, RAIL, "pick", replace(three, title="Pick one"))
    check("selection: kept across an update whose choices still have it", kept.selected == "b")
    gone = await live.update(ROBIN, RAIL, "pick", Activity(title="Pick one", selected="c", choices=(
        Choice("a", Activity(title="A")), Choice("c", Activity(title="C")))))
    check("selection: its choice gone → the activity's selected", gone.selected == "c")
    gone = await live.update(ROBIN, RAIL, "pick", Activity(title="Pick one", choices=(
        Choice("x", Activity(title="X")), Choice("y", Activity(title="Y")))))
    check("selection: neither there → the first", gone.selected == "x")
    try:
        await live.select(ROBIN, "rail:pick", "zzz")
        ok = False
    except live.UnknownChoice:
        ok = True
    check("select: an unknown key raises UnknownChoice", ok)
    nxt = await live.act(ROBIN, "rail:watch-12", "next")
    check("act next: selects the next choice", nxt.selected == "406")
    nxt = await live.act(ROBIN, "rail:watch-12", "next")
    check("act next: stays on the last", nxt.selected == "406")
    prev = await live.act(ROBIN, "rail:watch-12", "prev")
    check("act prev: back to the first", prev.selected == "404")
    await live.end(ROBIN, RAIL, "pick")


async def test_end_stop_expiry() -> None:
    at(seconds=30)
    await live.start(ROBIN, RAIL, "trip", Activity(title="On the way", icon="car"))
    await live.drain()
    n = len(SENT)
    ended = await live.end(ROBIN, RAIL, "trip", Final("Arrived", "At 18:35", "good"))
    await live.drain()
    listed = live.payload(ROBIN)["activities"]
    trip = next((a for a in listed if a["id"] == "rail:trip"), {})
    check("end with a final: pushed at once, listed as ended with the final, no longer running",
          ended and len(SENT) == n + 1 and trip.get("ended") is True and trip.get("final") == {
              "title": "Arrived", "text": "At 18:35", "tone": "good"} and trip["rev"] == 2
          and live.get(ROBIN, RAIL, "trip") is None and "rail:trip" not in [c.id for c in live.active(ROBIN)], trip)
    check("end: again → False", await live.end(ROBIN, RAIL, "trip") is False)
    try:
        await live.act(ROBIN, "rail:trip", "x")
        found = True
    except LookupError:
        found = False
    check("an ended activity cannot be acted on", not found)
    at(minutes=9)
    await live.tick()
    check("final: kept for 10 minutes", any(a["id"] == "rail:trip" for a in live.payload(ROBIN)["activities"]))
    at(minutes=2)
    out = await live.tick()
    check("final: then deleted", out["purged"] == 1
          and not any(a["id"] == "rail:trip" for a in live.payload(ROBIN)["activities"]), out)

    await live.start(ROBIN, PLAIN, "quick", Activity(title="Quick"))
    await live.drain()
    n = len(SENT)
    check("end without a final: gone at once, pushed", await live.end(ROBIN, PLAIN, "quick")
          and not any(a["id"] == "plain:quick" for a in live.payload(ROBIN)["activities"]))
    await live.drain()
    check("end without a final: the push no longer lists it", len(SENT) == n + 1
          and "plain:quick" not in [a["id"] for a in SENT[-1][1]["activities"]])
    check("end without a final: no longer running, not stoppable", live.get(ROBIN, PLAIN, "quick") is None
          and live.find(ROBIN, "plain:quick") is None and not await live.stop(ROBIN, "plain:quick"))
    again = await live.start(ROBIN, PLAIN, "quick", Activity(title="Quick again"))
    check("start again after an end without a final: rev carries on (1, ended 2, now 3), started_at new",
          again.rev == 3 and live.to_json(again)["rev"] == 3 and again.started_at == CLOCK["t"], again.rev)
    await live.end(ROBIN, PLAIN, "quick")
    at(hours=23)
    await live.tick()
    with get_db_connection() as conn:
        kept = conn.execute("SELECT rev FROM live_activities WHERE user_id = ? AND addon = 'plain' AND key = 'quick'",
                            (ROBIN.id,)).fetchone()
    check("a gone row keeps its rev (hidden) for a day", kept is not None and kept["rev"] == 4, kept)
    at(hours=2)
    await live.tick()
    with get_db_connection() as conn:
        kept = conn.execute("SELECT rev FROM live_activities WHERE user_id = ? AND addon = 'plain' AND key = 'quick'",
                            (ROBIN.id,)).fetchone()
    check("then it is deleted", kept is None)

    now = at(seconds=30)
    await live.start(ROBIN, PLAIN, "brief", Activity(title="Brief", ends_at=now + datetime.timedelta(seconds=30)))
    at(seconds=29)
    out = await live.tick()
    check("expiry: not before ends_at", out["expired"] == 0 and live.get(ROBIN, PLAIN, "brief") is not None)
    at(seconds=2)
    await live.drain()
    n = len(SENT)
    out = await live.tick()
    await live.drain()
    check("expiry: the loop ends it once ends_at passed, pushed at once",
          out["expired"] == 1 and live.get(ROBIN, PLAIN, "brief") is None and len(SENT) == n + 1, out)

    at(seconds=30)
    stopping = await live.start(ROBIN, RAIL, "stopme", Activity(title="Stop me", actions=(Action("x", "X", "stop"),)))
    rail.CALLS.clear()
    check("stop: True, the activity is gone", await live.stop(ROBIN, "rail:stopme")
          and live.get(ROBIN, RAIL, "stopme") is None)
    check("stop: then live_action gets 'stop' with the activity as it was",
          rail.CALLS == [("action", ROBIN.id, "stopme", "stop", "")] and stopping.key == "stopme", rail.CALLS)
    check("stop: again / another user's / unknown → False", not await live.stop(ROBIN, "rail:stopme")
          and not await live.stop(GAL, "rail:watch-12") and not await live.stop(ROBIN, "nonsense"))
    await live.start(ROBIN, RAIL, "stopme", Activity(title="Stop me", actions=(Action("x", "X", "stop"),)))
    rail.CALLS.clear()
    after = await live.act(ROBIN, "rail:stopme", "x")
    check("act on a stop action: ends it, live_action gets 'stop'", after is None
          and live.get(ROBIN, RAIL, "stopme") is None and [c[3] for c in rail.CALLS] == ["stop"], rail.CALLS)
    await live.start(ROBIN, PLAIN, "nohook", Activity(title="No hooks"))
    check("stop without a live_action hook: just ends it", await live.stop(ROBIN, "plain:nohook")
          and live.get(ROBIN, PLAIN, "nohook") is None)
    await live.start(ROBIN, RAIL, "kept", Activity(title="Kept"))
    await live.end(ROBIN, RAIL, "kept", Final("Done"))
    await live.drain()
    n = len(SENT)
    check("stop on an ended one kept for its final: dismissed", await live.stop(ROBIN, "rail:kept")
          and not any(a["id"] == "rail:kept" for a in live.payload(ROBIN)["activities"]))
    await live.drain()
    check("dismissing a final: pushed at once, so the other clients drop it", len(SENT) == n + 1
          and "rail:kept" not in [a["id"] for a in SENT[-1][1]["activities"]], len(SENT) - n)
    check("dismissing a final: again → False", not await live.stop(ROBIN, "rail:kept"))


async def test_actions() -> None:
    at(seconds=30)
    acts = (Action("later", "Later"), Action("arrived", "Arrived"), Action("boom", "Boom"))
    await live.start(ROBIN, RAIL, "act", Activity(title="Act on me", actions=acts, choices=(
        Choice("p", Activity(title="P")), Choice("q", Activity(title="Q")))))
    rail.CALLS.clear()
    c = await live.act(ROBIN, "rail:act", "later", "q")
    check("act: a server action goes to live_action with its choice; an Activity answer updates it",
          rail.CALLS == [("action", ROBIN.id, "act", "later", "q")] and c.activity.title == "Later, as asked"
          and c.rev == 2, rail.CALLS)
    rail.CALLS.clear()
    await live.act(ROBIN, "rail:act", "later")
    check("act: no choice given → the selected one", rail.CALLS[-1][4] == "p", rail.CALLS)
    c = await live.act(ROBIN, "rail:act", "boom")
    check("act: a failing hook is logged, the activity goes on unchanged", c is not None and c.rev == 2)
    for bad, exc in (("nope", live.UnknownAction), ("select", live.UnknownAction)):
        try:
            await live.act(ROBIN, "rail:act", bad)
            ok = False
        except exc:
            ok = True
        check(f"act: {bad!r} is not one of its actions → UnknownAction", ok)
    try:
        await live.act(ROBIN, "rail:act", "later", "zzz")
        ok = False
    except live.UnknownChoice:
        ok = True
    check("act: an unknown choice → UnknownChoice", ok)
    c = await live.act(ROBIN, "rail:act", "arrived")
    check("act: live.END from the hook ends it", c is None and live.get(ROBIN, RAIL, "act") is None)


async def test_refresh_loop() -> None:
    now = at(minutes=5)
    for c in list(live.active(ROBIN)):
        await live.end(ROBIN, get_addon(c.addon), c.key)
    await live.start(ROBIN, RAIL, "every", Activity(title="Every minute"))
    await live.start(ROBIN, RAIL, "soon", Activity(title="At a time", refresh_at=now + datetime.timedelta(seconds=10)))
    await live.start(ROBIN, PLAIN, "plain", Activity(title="No refresh hook"))
    await live.start(GAL, RAIL, "gals", Activity(title="Gal's"))
    rail.CALLS.clear()
    at(seconds=5)
    out = await live.tick()
    await live.drain()
    check("refresh: nothing due yet → no call", rail.CALLS == [] and out["refreshed"] == 0, rail.CALLS)
    at(seconds=6)
    rail.NEXT[(ROBIN.id, "soon")] = Activity(title="Refreshed")
    out = await live.tick()
    await live.drain()
    check("refresh: an activity's refresh_at makes it due then; only it is called; its Activity updates it",
          rail.CALLS == [("refresh", ROBIN.id, "soon")] and out["refreshed"] == 1
          and live.get(ROBIN, RAIL, "soon").activity.title == "Refreshed", rail.CALLS)
    rail.CALLS.clear()
    at(seconds=50)  # 61 s after the start
    rail.NEXT[(GAL.id, "gals")] = live.END
    out = await live.tick()
    await live.drain()
    called = sorted(c[1:] for c in rail.CALLS)
    check("refresh: every=60 after the start, each as its own user; never for an addon without the hook",
          called == sorted([(ROBIN.id, "every"), (GAL.id, "gals")]) and out["refreshed"] == 2, called)
    check("refresh: live.END ends it", live.get(GAL, RAIL, "gals") is None)
    rail.CALLS.clear()
    at(seconds=10)  # 60 s after "soon" was refreshed
    rail.NEXT[(ROBIN.id, "soon")] = "boom"
    await live.tick()
    await live.drain()
    check("refresh: a failing hook is logged and never ends the activity",
          rail.CALLS == [("refresh", ROBIN.id, "soon")] and live.get(ROBIN, RAIL, "soon") is not None, rail.CALLS)
    rail.CALLS.clear()
    at(seconds=5)
    await live.tick()
    await live.drain()
    check("refresh: a failed one is not called again until it is due", rail.CALLS == [], rail.CALLS)
    store.disconnect(ROBIN, "rail")
    at(minutes=2)
    await live.tick()
    await live.drain()
    check("refresh: not called while the addon is not active for the user", rail.CALLS == [], rail.CALLS)
    store.connect(ROBIN, "rail")
    rail.CALLS.clear()
    at(minutes=2)
    rail.NEXT[(ROBIN.id, "every")] = None
    await live.tick()
    await live.drain()
    check("refresh: None leaves it as it is", live.get(ROBIN, RAIL, "every").rev == 1
          and ("refresh", ROBIN.id, "every") in rail.CALLS)
    for key in ("every", "soon"):
        await live.end(ROBIN, RAIL, key)
    await live.end(ROBIN, PLAIN, "plain")


async def test_slow_refresh() -> None:
    """A hanging live_refresh of one addon holds up nothing else: not the loop,
    another activity's refresh, expiry or a trailing push; and it is never
    started twice for its activity."""
    now = at(minutes=5)
    for c in list(live.active(ROBIN)):
        await live.end(ROBIN, get_addon(c.addon), c.key)
    await live.drain()
    rail.CALLS.clear()
    slow.CALLS.clear()
    await live.start(ROBIN, SLOW, "hang", Activity(title="Hangs"))
    await live.start(ROBIN, RAIL, "quick", Activity(title="Quick", refresh_at=now + datetime.timedelta(seconds=10)))
    await live.start(ROBIN, PLAIN, "short", Activity(title="Short", ends_at=now + datetime.timedelta(seconds=10)))
    await live.drain()
    at(seconds=6)
    out = await asyncio.wait_for(live.tick(), timeout=2)
    for _ in range(5):
        await asyncio.sleep(0)
    check("slow: its refresh starts in the background; tick does not wait for it",
          out["refreshed"] == 1 and slow.CALLS == ["hang"] and len(live._inflight) == 1, (out, slow.CALLS))
    at(seconds=5)
    rail.NEXT[(ROBIN.id, "quick")] = Activity(title="Quick, refreshed")
    await live.update(ROBIN, PLAIN, "short", Activity(title="Short, changed", ends_at=now + datetime.timedelta(seconds=10)))
    await asyncio.wait_for(asyncio.gather(*list(live._tasks)), timeout=2)  # not drain: the slow one still hangs
    n = len(SENT)
    out = await asyncio.wait_for(live.tick(), timeout=2)
    others = [t for k, t in live._inflight.items() if k[1] != "slow"]
    await asyncio.wait_for(asyncio.gather(*others), timeout=2)
    check("slow: another addon's refresh runs and applies meanwhile; the hanging one is not started again",
          out["refreshed"] == 1 and rail.CALLS == [("refresh", ROBIN.id, "quick")]
          and live.get(ROBIN, RAIL, "quick").activity.title == "Quick, refreshed" and slow.CALLS == ["hang"],
          (out, rail.CALLS, slow.CALLS))
    check("slow: expiry still runs", out["expired"] == 1 and live.get(ROBIN, PLAIN, "short") is None, out)
    at(seconds=11)
    out = await asyncio.wait_for(live.tick(), timeout=2)
    await asyncio.wait_for(asyncio.gather(*list(live._tasks)), timeout=2)
    check("slow: pushes still go (the expiry's at once, the refresh's trailing one later)",
          len(SENT) == n + 2 and out["pushed"] == 1
          and "Quick, refreshed" in [a["title"] for a in SENT[-1][1]["activities"]]
          and slow.CALLS == ["hang"], (out, len(SENT) - n))
    slow.RELEASE.set()
    await asyncio.wait_for(live.drain(), timeout=2)
    check("slow: once it answers it is done; drain waits for it", live._inflight == {} and slow.CALLS == ["hang"])
    await live.end(ROBIN, SLOW, "hang")
    await live.end(ROBIN, RAIL, "quick")
    await live.drain()


def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    live_api.setup(app)
    return app


def phone_events(user: users.User) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute("SELECT type, data FROM phone_events WHERE user_id = ? AND type = 'live'",
                            (user.id,)).fetchall()
    return [json.loads(r["data"]) for r in rows]


async def test_api() -> None:
    at(minutes=5)
    for u in (ROBIN, GAL, MAYA):
        for c in live.active(u):
            await live.end(u, get_addon(c.addon), c.key)
    robins = await live.start(ROBIN, RAIL, "watch-12", replace(train(), actions=train().actions + ()))
    await live.start(GAL, RAIL, "watch-12", replace(train(), title="Gal's train"))
    await live.start(MAYA, RAIL, "maya", Activity(title="Maya's", actions=(Action("later", "Later"),)))
    await live.drain()
    async with TestClient(TestServer(build_app())) as c:
        r = await c.get("/api/live", headers=AUTH[ROBIN.id])
        body = await r.json()
        check("GET /api/live: the caller's activities only, the wire form",
              r.status == 200 and [a["id"] for a in body["activities"]] == ["rail:watch-12"]
              and body == live.payload(ROBIN) and body["activities"][0]["title"] == "Departs 17:46 (+4)", body)
        r = await c.get("/api/live", headers=AUTH[MAYA.id])
        check("GET /api/live: Maya (no phone addon) still reads hers", [a["id"] for a in (await r.json())["activities"]]
              == ["rail:maya"])
        r = await c.get("/api/live")
        check("GET /api/live: signed-in users only", r.status == 401)

        for method, path, payload in (("POST", "/api/live/rail:maya/act", {"action": "later"}),
                                      ("POST", "/api/live/rail:maya/select", {"choice": "404"}),
                                      ("DELETE", "/api/live/rail:maya", None),
                                      ("POST", "/api/live/rail:nothing/act", {"action": "later"})):
            r = await c.request(method, path, headers=AUTH[ROBIN.id], json=payload)
            check(f"{method} {path} by Robin → 404 like a missing one",
                  r.status == 404 and await r.json() == {"error": "not_found"})
        check("Maya's activity untouched", live.find(MAYA, "rail:maya") is not None)

        r = await c.post("/api/live/rail:watch-12/act", headers=AUTH[ROBIN.id], json={"action": "jump"})
        check("act: unknown action → 400 unknown_action", r.status == 400
              and await r.json() == {"error": "unknown_action"})
        r = await c.post("/api/live/rail:watch-12/select", headers=AUTH[ROBIN.id], json={"choice": "999"})
        check("select: unknown choice → 400 unknown_choice", r.status == 400
              and await r.json() == {"error": "unknown_choice"})
        r = await c.post("/api/live/rail:watch-12/select", headers=AUTH[ROBIN.id], data="nope")
        check("select: not JSON → 400 invalid", r.status == 400 and (await r.json())["error"] == "invalid")

        r = await c.post("/api/live/rail:watch-12/select", headers=AUTH[ROBIN.id], json={"choice": "406"})
        body = await r.json()
        check("select: the activity in the reply, its selection stored",
              r.status == 200 and body["activity"]["selected"] == "406" and body["activity"]["rev"] == robins.rev + 1
              and live.find(ROBIN, "rail:watch-12").selected == "406"
              and live.find(GAL, "rail:watch-12").selected == "404", body)
        r = await c.post("/api/live/rail:watch-12/act", headers=AUTH[ROBIN.id], json={"action": "prev"})
        body = await r.json()
        check("act prev: the reply is the activity on the previous choice", body["activity"]["selected"] == "404")
        r = await c.post("/api/live/rail:maya/act", headers=AUTH[MAYA.id], json={"action": "later"})
        body = await r.json()
        check("act: a server action's reply is the updated activity",
              r.status == 200 and body["activity"]["title"] == "Later, as asked", body)
        r = await c.post("/api/live/rail:watch-12/act", headers=AUTH[GAL.id], json={"action": "stop"})
        check("act stop: the reply is null (ended)", r.status == 200 and await r.json() == {"activity": None}
              and live.find(GAL, "rail:watch-12") is None and live.find(ROBIN, "rail:watch-12") is not None)
        r = await c.delete("/api/live/rail:watch-12", headers=AUTH[ROBIN.id])
        check("DELETE: ok, stopped", r.status == 200 and await r.json() == {"ok": True}
              and live.find(ROBIN, "rail:watch-12") is None)
        r = await c.delete("/api/live/rail:watch-12", headers=AUTH[ROBIN.id])
        check("DELETE: again → 404", r.status == 404)
    await live.drain()

    robin_events, gal_events = phone_events(ROBIN), phone_events(GAL)
    check("push: one live event per user with the phone addon (each replaces the last), the GET body",
          len(robin_events) == 1 and robin_events[0] == live.payload(ROBIN)
          and len(gal_events) == 1 and gal_events[0] == live.payload(GAL), (robin_events, gal_events))
    check("push: none for Maya (no phone addon), though her activity changed",
          phone_events(MAYA) == [] and any(uid == MAYA.id for uid, _, _ in SENT))


async def main() -> None:
    test_fixture()
    await test_validation()
    await test_start_update()
    await test_selection()
    await test_end_stop_expiry()
    await test_actions()
    await test_refresh_loop()
    await test_slow_refresh()
    await test_api()


asyncio.run(main())
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
raise SystemExit(1 if failed else 0)
