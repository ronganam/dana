"""Signals (docs/signals.md): POST /api/signals from the phone, the ranked sources, the wake flow and window, the
weather from Open-Meteo, zones changed → the phone, GET /api/signals per user.

Robin has the phone and, while the ranking is checked, a lower-ranked test source (``test_feed``, declared here on an
addon only she has: location, car, wake, screen sessions, weather), Gal has only the phone (a paired phone device,
whose token she posts with), Maya has neither (tasks only). Home Assistant is connected for Robin and is no source of
anything. Checks: every event type with its effect (tasks' car / location / wake reminders fired or not), per-event
errors (bad_event, unknown_zone for another user's zone id, stale), a repeated batch accepted without effect, the
ranking (a lower-ranked source's event used while the phone is quiet, ignored while the phone is active, used again
once the phone goes quiet), a late event recording the transition without a reminder, the wake window (default, set,
learned from a week of mornings, back to learned) and a sleep session as the wake at its exact time, the "signals"
self-check for a user with no source, Open-Meteo's answer mapped to the weather shape (a fake HTTP answer, never the
network) ranked above the test source's, the ``zones_changed`` phone event, GET /api/signals isolation (only the
phone and Open-Meteo once the test source is gone). Writes tests/fixtures/signals_web.json with WRITE_WEB_FIXTURE=1
(else checks it for shape drift).

Run: bin/test test_signals
"""

import asyncio
import datetime
import json
import os
from typing import Any, Dict, List, Tuple
from unittest.mock import AsyncMock, patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("signals_test")
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake credentials
# A zone where it is around noon now (Etc/GMT-N is UTC+N), so no check runs across a local midnight.
_OFFSET = max(-12, min(14, 12 - datetime.datetime.now(datetime.timezone.utc).hour))
ZONE = f"Etc/GMT{-_OFFSET:+d}" if _OFFSET else "Etc/GMT"
os.environ["TIMEZONE"] = ZONE

import httpx
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import db, users

db.migrate()
users.bootstrap()
from addons import bootstrap as addons_bootstrap  # noqa: E402
addons_bootstrap.bootstrap()

from core import (briefing, checks, clock, devices, events, notify as core_notify, profile_api, signals,  # noqa: E402
                  signals_api, wake, wake_window, zones)
from core.auth import setup_auth  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from core.addons import registry, store  # noqa: E402
from addons.essentials import weather as open_meteo  # noqa: E402
from addons.phone import api as phone_api, push  # noqa: E402
from addons.tasks import service as tasks_service  # noqa: E402
from addons.tasks.groups import ensure_default_groups_for_everyone  # noqa: E402

FIXTURE = os.path.join(testenv.FIXTURES, "signals_web.json")
results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


ROBIN = users.create("Robin")
GAL = users.create("Gal")
MAYA = users.create("Maya")
for _u in (ROBIN, GAL):
    store.connect(_u, "phone")
for _u in (ROBIN, GAL, MAYA):
    store.connect(_u, "tasks")
ensure_default_groups_for_everyone()
TOKENS = {u.id: users.issue_token(u.id, "test")[0] for u in (ROBIN, MAYA)}
TOKENS[GAL.id], GAL_PHONE = devices.create(GAL, "phone", "Gal's phone")


def auth(u) -> Dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[u.id]}"}


wake.WAKE_CONFIRM_SECONDS = 0
PUSHED: List[Tuple[str, str]] = []  # (user id, task id) of every reminder sent


async def fake_send(user, message):
    if message.kind == "reminder":
        PUSHED.append((user.id, message.data["task_id"]))
    return {"push": True}


def pushed(user, task_id: str) -> int:
    return PUSHED.count((user.id, task_id))


def task(user, title: str, trig: Dict[str, Any]) -> str:
    return tasks_service.create_task(user, title=title, triggers=json.dumps(trig))


def ms(dt: datetime.datetime) -> int:
    return int(dt.timestamp() * 1000)


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


async def settle() -> None:
    """Every event handler finished, and the reminders they start in the background."""
    for _ in range(3):
        await events.drain()
        await asyncio.sleep(0.05)


# --- A lower-ranked test source, on an addon only Robin has (cameras) ------------------------------

FEED = {"rank": 50, "source": "test_feed", "label": "Test feed"}
FEED_SCREEN: List[float] = []  # what the feed's screen_sessions answers (empty: nothing)
_cameras = store.get_addon("cameras")
for _signal in ("wake", "screen", "car", "location", "weather"):
    _cameras.provide(signals.source_key(_signal), **FEED)(lambda user: "")


@_cameras.provide("screen_sessions", **FEED)
async def feed_screen(user, since_utc, until_utc):
    return list(FEED_SCREEN) or None


@_cameras.provide("weather", **FEED)
async def feed_weather(user):
    return {"condition": "cloudy", "temperature": 14, "unit": "°C"}


def feed(user, signal: str, *, action: str = "", target: str = "") -> bool:
    """The test source delivers an event (as an addon's watcher would)."""
    return signals.deliver(user, signal, "test_feed", detail=f"{action} {target}".strip(), action=action, target=target)


def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    signals_api.setup(app)
    profile_api.setup(app)
    phone_api.setup(app)
    return app


def stale(user, signal: str, source: str) -> None:
    """Make ``source``'s last delivery of ``signal`` older than the active window."""
    old = (utcnow() - datetime.timedelta(hours=signals.ACTIVE_HOURS + 1)).isoformat(timespec="seconds")
    with get_db_connection() as conn:
        conn.execute("UPDATE signal_sources SET last_at = ? WHERE user_id = ? AND signal = ? AND source = ?",
                     (old, user.id, signal, source))
        conn.commit()


def wake_rows(user) -> List[Tuple[str, str, str]]:
    with get_db_connection() as conn:
        return [tuple(r) for r in conn.execute("SELECT date, at, why FROM wake_log WHERE user_id = ?", (user.id,))]


def clear_wake(user) -> None:
    with get_db_connection() as conn:
        conn.execute("DELETE FROM wake_log WHERE user_id = ?", (user.id,))
        conn.commit()


def sources_of(body: Dict[str, Any], signal: str) -> List[Dict[str, Any]]:
    return next(s["sources"] for s in body["signals"] if s["signal"] == signal)


def active_of(body: Dict[str, Any], signal: str):
    return next((s["source"] for s in sources_of(body, signal) if s["active"]), None)


async def get_signals(client, user) -> Dict[str, Any]:
    r = await client.get("/api/signals", headers=auth(user))
    assert r.status == 200, await r.text()
    return await r.json()


async def post(client, user, events_: List[Dict[str, Any]]):
    r = await client.post("/api/signals", headers=auth(user), json={"events": events_})
    return r.status, await r.json()


# --- 1. Sources and the overview before anything arrives ------------------------------------------

async def test_overview(client) -> None:
    r, g, m = await get_signals(client, ROBIN), await get_signals(client, GAL), await get_signals(client, MAYA)
    check("overview: every signal, always in that order",
          [s["signal"] for s in r["signals"]] == list(signals.SIGNALS) == [s["signal"] for s in m["signals"]])
    check("overview: Robin's sources ranked, the phone first, the test source next",
          [s["source"] for s in sources_of(r, "wake")] == ["phone", "test_feed"]
          and [s["source"] for s in sources_of(r, "car")] == ["phone", "test_feed"]
          and [s["source"] for s in sources_of(r, "sleep")] == ["phone"]
          and [s["source"] for s in sources_of(r, "weather")] == ["open_meteo", "test_feed"]
          and sources_of(r, "wake")[0]["label"] == "Dana's phone app"
          and sources_of(r, "weather")[0]["label"] == "Open-Meteo", r)
    check("overview: the test source only for the user who has its addon",
          all(s["source"] != "test_feed" for sig in g["signals"] for s in sig["sources"]))
    check("overview: Home Assistant (connected for Robin) is no source of anything",
          all(s["source"] != "homeassistant" for sig in r["signals"] for s in sig["sources"]))
    check("overview: Maya (neither) has no wake, car or place source; Open-Meteo for weather (everyone)",
          not sources_of(m, "wake") and not sources_of(m, "car") and not sources_of(m, "location")
          and [s["source"] for s in sources_of(m, "weather")] == ["open_meteo"]
          and sources_of(m, "weather")[0]["detail"] == "Set your Home zone (Settings → Zones)"
          and active_of(m, "weather") is None, m)
    check("overview: Robin has no paired phone yet → the test source (set up) is her active wake / location source",
          active_of(r, "wake") == "test_feed" and active_of(r, "location") == "test_feed"
          and sources_of(r, "wake")[0]["detail"] == "Pair Dana's phone app (Settings → Devices)"
          and sources_of(r, "wake")[0]["last_at"] is None, r)
    check("overview: Gal's paired phone is her active source",
          active_of(g, "wake") == "phone" and active_of(g, "car") == "phone" and active_of(g, "location") == "phone")
    check("overview: the default wake window", r["wake_window"] == {"start": "05:00", "end": "12:00", "learned": False})
    keys = {u.id: {c["key"]: c for c in checks.for_user(u)} for u in (ROBIN, GAL, MAYA)}
    mc = keys[MAYA.id].get("signals")
    check("self-check: Maya (reminders, no source) is told plainly",
          mc is not None and not mc["ok"] and mc["detail"] == "no source for wake, car, location"
          and mc["title"] == "Dana can't tell when you wake up, drive or arrive somewhere", mc)
    check("self-check: Robin and Gal have a source for each",
          keys[ROBIN.id]["signals"]["ok"] and keys[GAL.id]["signals"]["ok"], (keys[ROBIN.id]["signals"]))
    check("wake flow: Gal and Robin have it, Maya does not (no channel, no source)",
          wake.available(GAL) and wake.available(ROBIN) and not wake.available(MAYA))


# --- 2. POST /api/signals: shapes, errors, effects, repeats ----------------------------------------

async def test_post(client) -> None:
    status, body = await post(client, MAYA, [{"type": "unlock", "at": ms(utcnow())}])
    check("POST: a user without the phone addon → 403 not_connected",
          status == 403 and body.get("error") == "not_connected", (status, body))
    for bad in ([], "x", {"event": []}, {"events": "x"}, {"events": {}}):
        r = await client.post("/api/signals", headers=auth(GAL), json=bad)
        check(f"POST: a body that is not {{events: [...]}} → 400 ({json.dumps(bad)})", r.status == 400)
    r = await client.post("/api/signals", headers=auth(GAL), json={"events": [{"type": "unlock"}] * 201})
    check("POST: more than 200 events → 400", r.status == 400)

    g_home = zones.create(GAL, "Home", 32.08, 34.78, is_home=True)
    r_home = zones.create(ROBIN, "Home", 32.1, 34.8, is_home=True)
    zones.create(ROBIN, "Work", 32.2, 34.9)
    t_arrive = task(GAL, "Gal feed the dog", {"location": {"location": "home", "event": "arrive"}})
    t_car = task(GAL, "Gal buy fuel", {"car": "enter"})
    t_robin_car = task(ROBIN, "Robin car", {"car": "enter"})
    now = utcnow()
    batch = [
        {"type": "car", "action": "enter", "at": ms(now - datetime.timedelta(minutes=3)), "via": "bluetooth"},
        {"type": "zone", "action": "arrive", "zone_id": g_home.id, "at": ms(now - datetime.timedelta(minutes=2))},
        {"type": "zone", "action": "arrive", "zone_id": r_home.id, "at": ms(now)},           # Robin's zone
        {"type": "zone", "action": "arrive", "zone_id": "nope", "at": ms(now)},
        {"type": "car", "action": "enter", "at": ms(now - datetime.timedelta(hours=49)), "via": "bluetooth"},
        {"type": "car", "action": "fly", "at": ms(now), "via": "bluetooth"},
        {"type": "car", "action": "leave", "at": ms(now), "via": "teleport"},
        {"type": "unlock", "at": ms(now + datetime.timedelta(hours=1))},                     # in the future
        {"type": "unlock", "at": True},
        {"type": "screen", "start": ms(now), "end": ms(now - datetime.timedelta(minutes=1))},
        {"type": "sleep", "start": ms(now - datetime.timedelta(hours=7)), "end": ms(now), "via": "fitbit"},
        {"type": "teleport", "at": ms(now)},
        "unlock",
        {"type": "screen", "start": ms(now - datetime.timedelta(seconds=100)), "end": ms(now - datetime.timedelta(seconds=10))},
    ]
    with patch.object(core_notify, "send", AsyncMock(side_effect=fake_send)):
        status, body = await post(client, GAL, batch)
        await settle()
        check("POST: accepted and rejected per event, by index",
              status == 200 and body == {"accepted": 3, "rejected": [
                  {"index": 2, "error": "unknown_zone"}, {"index": 3, "error": "unknown_zone"},
                  {"index": 4, "error": "stale"}, {"index": 5, "error": "bad_event"}, {"index": 6, "error": "bad_event"},
                  {"index": 7, "error": "bad_event"}, {"index": 8, "error": "bad_event"}, {"index": 9, "error": "bad_event"},
                  {"index": 10, "error": "bad_event"}, {"index": 11, "error": "bad_event"},
                  {"index": 12, "error": "bad_event"}]}, body)
        check("POST: Gal's car-enter and arrive-home reminders fired, Robin's car reminder not",
              pushed(GAL, t_car) == 1 and pushed(GAL, t_arrive) == 1 and pushed(ROBIN, t_robin_car) == 0, PUSHED)
        with get_db_connection() as conn:
            screens = conn.execute("SELECT user_id, end_ms - start_ms FROM phone_screen_sessions").fetchall()
        check("POST: the screen session stored as Gal's", [tuple(s) for s in screens] == [(GAL.id, 90000)], screens)
        status, again = await post(client, GAL, batch)
        await settle()
        with get_db_connection() as conn:
            n = conn.execute("SELECT COUNT(*) FROM phone_screen_sessions").fetchone()[0]
        check("POST: the same batch again is accepted without a second effect",
              again == body and pushed(GAL, t_car) == 1 and pushed(GAL, t_arrive) == 1 and n == 1, (again, PUSHED))
        g = await get_signals(client, GAL)
        loc = sources_of(g, "location")[0]
        check("overview: Gal's last location signal is the zone's name, nothing more",
              loc["detail"] == "arrive Home" and loc["last_at"] and loc["active"], loc)
        check("overview: the time is local (the server's zone)", datetime.datetime.fromisoformat(loc["last_at"]).utcoffset() == datetime.timedelta(hours=_OFFSET), loc)
        r = await get_signals(client, ROBIN)
        check("isolation: Gal's events are not on Robin's overview",
              all(s["last_at"] is None for sig in r["signals"] for s in sig["sources"] if s["source"] == "phone"), r)


# --- 3. Ranking: the test source while the phone is quiet ----------------------------------------

async def test_ranking(client) -> None:
    PUSHED.clear()
    t_home = task(ROBIN, "Robin water plants", {"location": {"location": "home", "event": "arrive"}})
    t_leave = task(ROBIN, "Robin lock up", {"location": {"location": "home", "event": "leave"}})
    t_car_out = task(ROBIN, "Robin parking", {"car": "leave"})
    with patch.object(core_notify, "send", AsyncMock(side_effect=fake_send)):
        feed(ROBIN, "location", action="leave", target="home")
        await settle()
        check("ranking: the phone quiet → the test source's leave fires", pushed(ROBIN, t_leave) == 1, PUSHED)
        now = utcnow()
        home = zones.home(ROBIN)
        status, body = await post(client, ROBIN, [
            {"type": "zone", "action": "arrive", "zone_id": home.id, "at": ms(now - datetime.timedelta(minutes=1))}])
        await settle()
        check("ranking: the phone's arrive fires", body["accepted"] == 1 and pushed(ROBIN, t_home) == 1, (body, PUSHED))
        feed(ROBIN, "location", action="leave", target="home")
        await settle()
        check("ranking: the test source's event is ignored while the phone is active", pushed(ROBIN, t_leave) == 1,
              PUSHED)
        r = await get_signals(client, ROBIN)
        feed_loc = sources_of(r, "location")[1]
        check("ranking: … but recorded on the overview; the phone is the active source",
              active_of(r, "location") == "phone" and feed_loc["detail"] == "leave home" and feed_loc["last_at"]
              and not feed_loc["active"], r)
        stale(ROBIN, "location", "phone")
        with patch.object(events, "emit", wraps=events.emit) as emitted:
            feed(ROBIN, "location", action="leave", target="home")
        await settle()
        check("ranking: the phone quiet again (past the window) → the test source's event used",
              [(c.args[0], c.kwargs.get("source"), c.kwargs.get("action")) for c in emitted.call_args_list]
              == [("core.signal.location", "test_feed", "leave")], emitted.call_args_list)
        check("core.signal.location: the zone's name as zone and target, the source, late",
              [sorted(c.kwargs.items()) for c in emitted.call_args_list]
              == [[("action", "leave"), ("late", False), ("source", "test_feed"), ("target", "home"),
                   ("zone", "home")]], emitted.call_args_list)
        status, body = await post(client, ROBIN, [
            {"type": "car", "action": "leave", "at": ms(utcnow() - datetime.timedelta(hours=2)), "via": "android_auto"}])
        await settle()
        with get_db_connection() as conn:
            marks = {r[0] for r in conn.execute("SELECT key FROM task_trigger_marks WHERE user_id = ?", (ROBIN.id,))}
        check("late: a car event from 2 h ago records the transition, sends no reminder",
              body["accepted"] == 1 and pushed(ROBIN, t_car_out) == 0 and "last_event_v2_car_leave_-" in marks,
              (body, marks))
        feed(ROBIN, "car", action="enter")
        await settle()
        r = await get_signals(client, ROBIN)
        check("ranking: per signal — the phone's late car event makes it the active car source",
              active_of(r, "car") == "phone" and sources_of(r, "car")[1]["detail"] == "enter", r)


# --- 4. Wake: window, unlock, the poll, sleep -----------------------------------------------------

def window_now() -> Dict[str, str]:
    return {"start": "00:00", "end": "23:59"}


async def test_wake(client) -> None:
    PUSHED.clear()
    t_gal = task(GAL, "Gal vitamins", {"wake": True})
    t_robin = task(ROBIN, "Robin stretch", {"wake": True})
    on_wake = AsyncMock()
    r = await client.put("/api/profile", headers=auth(GAL), json={"wake_window": {"start": "9:00", "end": "10:00"}})
    bad = await r.json()
    r2 = await client.put("/api/profile", headers=auth(GAL), json={"wake_window": {"start": "10:00", "end": "09:00"}})
    check("window: malformed or reversed → 400 field wake_window",
          r.status == 400 and bad.get("field") == "wake_window" and r2.status == 400, bad)
    r = await client.put("/api/profile", headers=auth(GAL), json={"wake_window": window_now()})
    check("window: set → GET /api/profile has it (not learned)",
          (await r.json())["wake_window"] == {**window_now(), "learned": False})
    await client.put("/api/profile", headers=auth(ROBIN), json={"wake_window": window_now()})
    now = utcnow()
    with patch.object(core_notify, "send", AsyncMock(side_effect=fake_send)), patch.object(briefing, "on_wake", on_wake):
        status, body = await post(client, GAL, [
            {"type": "unlock", "at": ms(now - datetime.timedelta(seconds=300))},
            {"type": "screen", "start": ms(now - datetime.timedelta(seconds=290)), "end": ms(now - datetime.timedelta(seconds=150))}])
        await settle()
        today = clock.now().date().isoformat()
        check("wake: Gal's unlock + her screen sessions since (the phone's own) → her wake",
              body["accepted"] == 2 and [d for d, _, _ in wake_rows(GAL)] == [today]
              and wake_rows(GAL)[0][2].startswith("unlock, screen "), (body, wake_rows(GAL)))
        check("wake: her wake reminder and her briefing only", pushed(GAL, t_gal) == 1 and pushed(ROBIN, t_robin) == 0
              and [c.args[0].id for c in on_wake.await_args_list] == [GAL.id], PUSHED)

        # Robin: no phone wake yet → the test source's screen sessions are her wake source (the poll).
        FEED_SCREEN[:] = [200.0, 200.0, 200.0]
        out = await wake.poll_once()
        check("wake poll: the test source's screen sessions wake Robin (Gal is done for today)",
              out.get(ROBIN.id) is True and wake_rows(ROBIN) and out.get(GAL.id) is False, out)
        clear_wake(ROBIN)
        await client.put("/api/profile", headers=auth(ROBIN), json={"wake_window": {"start": "03:00", "end": "03:01"}})
        status, body = await post(client, ROBIN, [{"type": "unlock", "at": ms(utcnow() - datetime.timedelta(seconds=30))}])
        check("wake: an unlock outside Robin's window does nothing (but the phone is now her active wake source)",
              body["accepted"] == 1 and not wake_rows(ROBIN), body)
        await client.put("/api/profile", headers=auth(ROBIN), json={"wake_window": window_now()})
        out = await wake.poll_once()
        check("ranking: the test source's wake is ignored while the phone is active", out.get(ROBIN.id) is False
              and not wake_rows(ROBIN), out)
        end = utcnow() - datetime.timedelta(minutes=20)
        sleep = [{"type": "sleep", "start": ms(end - datetime.timedelta(hours=7)), "end": ms(end), "via": "health_connect"}]
        status, body = await post(client, ROBIN, sleep)
        await settle()
        rows = wake_rows(ROBIN)
        end_local = datetime.datetime.fromtimestamp(ms(end) / 1000, clock.get_timezone())
        check("sleep: a sleep ending in the window is Robin's wake, at that exact time",
              body["accepted"] == 1 and rows and rows[0][1] == end_local.isoformat(timespec="seconds")
              and rows[0][2] == f"sleep ended {end_local:%H:%M}", (rows, end_local))
        check("sleep: her wake reminder fired", pushed(ROBIN, t_robin) == 1)
        status, again = await post(client, ROBIN, sleep)
        await settle()
        check("sleep: the same sleep again: accepted, no second wake", again == body and pushed(ROBIN, t_robin) == 1
              and len(wake_rows(ROBIN)) == 1)
        check("wake: Maya has no wake flow (nothing recorded)", not wake.available(MAYA) and not wake_rows(MAYA))
    r = await get_signals(client, ROBIN)
    check("overview: Robin's wake and sleep come from the phone",
          active_of(r, "wake") == "phone" and active_of(r, "sleep") == "phone"
          and sources_of(r, "wake")[0]["detail"] == "unlock" and sources_of(r, "sleep")[0]["detail"].count(":") == 2, r)


def test_learned_window() -> None:
    noa = users.create("Noa")
    days = ("06:40", "07:00", "07:10", "06:50", "07:20", "08:30", "06:00", "05:10")
    base = datetime.datetime(2026, 11, 1, tzinfo=clock.get_timezone())
    for i, hhmm in enumerate(days[:6]):
        h, m = map(int, hhmm.split(":"))
        wake_window.note_unlock(noa, base + datetime.timedelta(days=i, hours=h, minutes=m))
        wake_window.note_unlock(noa, base + datetime.timedelta(days=i, hours=h + 1, minutes=m))  # not the first
    check("learned: six mornings are not enough yet", wake_window.get(noa).to_json()
          == {"start": "05:00", "end": "12:00", "learned": False})
    wake_window.note_unlock(noa, base + datetime.timedelta(days=6, hours=1))  # 01:00: not a morning
    wake_window.note_unlock(noa, base + datetime.timedelta(days=6, hours=6))
    check("learned: the seventh morning learns the window around the first unlocks",
          wake_window.get(noa).to_json() == {"start": "05:00", "end": "10:00", "learned": True},
          wake_window.get(noa))
    wake_window.note_unlock(noa, base + datetime.timedelta(days=7, hours=5, minutes=10))
    check("learned: it stays once learned", wake_window.get(noa).start == "05:00" and wake_window.get(noa).learned)
    wake_window.set_window(noa, {"start": "06:00", "end": "09:00"})
    check("learned: a set window wins", wake_window.get(noa).to_json() == {"start": "06:00", "end": "09:00",
                                                                             "learned": False})
    wake_window.set_window(noa, None)
    check("learned: null goes back to the learned one", wake_window.get(noa).to_json()
          == {"start": "05:00", "end": "10:00", "learned": True})


# --- 5. Weather: Open-Meteo first, the test source next -------------------------------------------

async def test_weather() -> None:
    asked: List[httpx.URL] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request.url)
        return httpx.Response(200, json={
            "current_units": {"temperature_2m": "°C"},
            "current": {"temperature_2m": 17.4, "weather_code": 61, "is_day": 1},
            "daily": {"temperature_2m_max": [21.0], "temperature_2m_min": [12.5],
                      "precipitation_probability_max": [70]}})

    open_meteo.clear_cache()
    open_meteo.transport = httpx.MockTransport(answer)
    w = await briefing.weather(ROBIN)
    check("weather: Open-Meteo for Robin's Home zone, in the weather shape",
          w == {"condition": "rainy", "temperature": 17.4, "unit": "°C", "high": 21.0, "low": 12.5, "rain_chance": 70},
          w)
    q = asked[0].params if asked else {}
    check("weather: asked for her Home zone's place, current + today",
          asked and asked[0].host == "127.0.0.1" and q.get("latitude") == "32.1" and q.get("longitude") == "34.8"
          and "weather_code" in q.get("current", "") and q.get("forecast_days") == "1", asked)
    await briefing.weather(ROBIN)
    check("weather: cached per place", len(asked) == 1)
    check("weather: Maya has no Home zone → none (Open-Meteo not asked)", await briefing.weather(MAYA) is None
          and len(asked) == 1)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network", request=request)

    open_meteo.clear_cache()
    open_meteo.transport = httpx.MockTransport(down)
    w = await briefing.weather(ROBIN)
    check("weather: Open-Meteo failing → Robin's next source (the test source)",
          w is not None and w["condition"] == "cloudy" and w["temperature"] == 14, w)
    check("weather: Gal (Open-Meteo only) → none while Open-Meteo fails", await briefing.weather(GAL) is None)
    check("weather: WMO codes as the weather shape's words",
          open_meteo.condition(0) == "sunny" and open_meteo.condition(0, 0) == "clear night"
          and open_meteo.condition(3) == "cloudy" and open_meteo.condition(95) == "lightning rainy"
          and open_meteo.condition(42) == "exceptional" and open_meteo.condition(None) == "exceptional")
    hooks = registry.providers("weather", ROBIN)
    check("providers: ranked (Open-Meteo 10, the test source 50); Gal gets Open-Meteo only",
          [registry.source_of(h) for h in hooks] == ["open_meteo", "test_feed"]
          and [registry.source_of(h) for h in registry.providers("weather", GAL)] == ["open_meteo"])
    open_meteo.clear_cache()
    open_meteo.transport = httpx.MockTransport(answer)
    await briefing.weather(ROBIN)


# --- 6. Zones changed → the phone ----------------------------------------------------------------

def zone_events(user) -> List[Dict[str, Any]]:
    return [e for e in push.events_after(user, 0)["events"] if e["type"] == "zones_changed"]


async def test_zones_changed() -> None:
    zones.create(ROBIN, "Gym", 32.0, 34.7)
    await settle()
    check("zones changed: no phone registered for push (Robin) → no event", not zone_events(ROBIN))
    with patch.object(push, "configured", return_value=True):  # registered only: no FCM key, nothing is sent
        push.register(GAL, GAL_PHONE, "fcm", "test-fcm-token")
    before = push.latest_seq(GAL)
    gym = zones.create(GAL, "Gym", 32.0, 34.7)
    await settle()
    check("zones changed: Gal's phone gets a zones_changed event", len(zone_events(GAL)) == 1
          and push.latest_seq(GAL) > before)
    seq = push.latest_seq(GAL)
    zones.update(GAL, gym.id, {"radius_m": 300})
    await settle()
    zones.delete(GAL, gym.id)
    await settle()
    zones.set_home(GAL, 32.09, 34.79)
    await settle()
    check("zones changed: update, delete and set home each send one; only the newest is kept",
          len(zone_events(GAL)) == 1 and push.latest_seq(GAL) == seq + 3, zone_events(GAL))
    with get_db_connection() as conn:
        top = conn.execute("SELECT MAX(seq) FROM phone_events").fetchone()[0]
    added, _ = zones.import_zones(GAL, [{"name": "Gym", "latitude": 32.0, "longitude": 34.7},
                                        {"name": "Pool", "latitude": 32.01, "longitude": 34.71}])
    zones.import_zones(GAL, [{"name": "Gym", "latitude": 32.0, "longitude": 34.7}])  # nothing new: no event
    await settle()
    with get_db_connection() as conn:
        new = conn.execute("SELECT user_id, type FROM phone_events WHERE seq > ?", (top,)).fetchall()
    check("zones changed: an import is one event, an import that adds nothing none",
          len(added) == 2 and [tuple(r) for r in new] == [(GAL.id, "zones_changed")], new)
    check("zones changed: not the other users'", not zone_events(MAYA)
          and all(e["type"] != "zones_changed" for e in push.events_after(MAYA, 0)["events"]))
    zones.create(MAYA, "Home", 31.0, 34.0, is_home=True)
    await settle()
    check("zones changed: Maya has no phone addon: nothing stored for her", not push.events_after(MAYA, 0)["events"])


# --- 7. The captured payload ---------------------------------------------------------------------

def shape(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: shape(x) for k, x in v.items()}
    if isinstance(v, list):
        return [shape(v[0])] if v else []
    return type(v).__name__


async def test_fixture(client) -> None:
    now = utcnow()
    await post(client, ROBIN, [
        {"type": "screen", "start": ms(now - datetime.timedelta(seconds=200)), "end": ms(now - datetime.timedelta(seconds=20))},
        {"type": "zone", "action": "arrive", "zone_id": zones.home(ROBIN).id, "at": ms(now - datetime.timedelta(seconds=10))}])
    await client.put("/api/profile", headers=auth(ROBIN), json={"wake_window": {"start": "06:00", "end": "10:30"}})
    await settle()
    body = await get_signals(client, ROBIN)
    check("fixture: Robin's phone is the only source of wake, screen, car, location and sleep, Open-Meteo of the "
          "weather (Home Assistant connected, the test source gone)",
          all([x["source"] for x in sources_of(body, s)] == ["phone"] and active_of(body, s) == "phone"
              for s in ("wake", "screen", "car", "location", "sleep"))
          and [x["source"] for x in sources_of(body, "weather")] == ["open_meteo"], body)
    if os.getenv("WRITE_WEB_FIXTURE"):
        with open(FIXTURE, "w", encoding="utf-8") as f:
            f.write(json.dumps(body, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {FIXTURE}")
    else:
        with open(FIXTURE, encoding="utf-8") as f:
            stored = json.load(f)
        check("fixture: tests/fixtures/signals_web.json has the payload's current shape "
              "(regenerate with WRITE_WEB_FIXTURE=1 bin/test test_signals)",
              [s["signal"] for s in stored["signals"]] == list(signals.SIGNALS)
              and shape(stored["wake_window"]) == shape(body["wake_window"])
              and shape(sources_of(stored, "wake")[0]) == shape(sources_of(body, "wake")[0]), stored)


async def main() -> None:
    store.connect(ROBIN, "homeassistant", credentials={"host": "http://ha-robin.invalid", "token": "tok-robin"})
    store.connect(ROBIN, "cameras", settings={"url": "http://frigate-robin.invalid"})  # carries the test source
    async with TestClient(TestServer(build_app())) as client:
        await test_overview(client)
        await test_post(client)
        await test_ranking(client)
        await test_wake(client)
        test_learned_window()
        await test_weather()
        await test_zones_changed()
        store.disconnect(ROBIN, "cameras")  # the test source gone: what a user can have
        await test_fixture(client)


asyncio.run(main())
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    raise SystemExit(1)
