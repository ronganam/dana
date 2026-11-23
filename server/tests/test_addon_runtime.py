"""The addon runtime: jobs per user (isolation, failures, heartbeat), services restarted, events, notify channels, prompt slot joiners, LLM purposes.

Run: bin/test test_addon_runtime

One installed addon written to a temp folder (``rt``, loaded through
DANA_ADDON_DIRS) declares a job of each kind, a service, event handlers (one
failing), a channel, prompt parts in three slots and an LLM job. Two users have
it (one whose job raises), a third does not.
"""

import asyncio
import os
import sys
import textwrap
from pathlib import Path

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("addon_runtime_test")

ROOT = Path(testenv.scratch_path("addon_dirs"))
(ROOT / "rt").mkdir(parents=True)
(ROOT / "rt" / "addon.py").write_text(textwrap.dedent('''\
    from core.addons import Addon, PromptPart
    addon = Addon("rt", description="Runtime test addon", llm_jobs={"rt_summary": "deep"},
                  prompt_parts=(PromptPart("tools", "  greeting people  "), PromptPart("tools", "waving", rank=1),
                                PromptPart("guides", "Wave at {user}."), PromptPart("send_via", " or a wave")))
    '''))
(ROOT / "rt" / "hooks.py").write_text(textwrap.dedent('''\
    import asyncio
    from core import users
    from core.addons import minutes
    from addons.rt.addon import addon

    SEEN = []      # (what, user id, acting user id, data)
    STARTS = []

    @addon.job(every=minutes(5))
    async def tick(user):
        SEEN.append(("tick", user.id, users.current().id, None))
        if user.name == "Fails":
            raise RuntimeError("this user's pass breaks")
        return user.name

    @addon.job(daily="07:30")
    async def morning(user):
        return None

    @addon.job(at_start=True)
    async def warm(user):
        return None

    @addon.service
    async def watcher(app):
        STARTS.append(len(STARTS))
        if len(STARTS) < 3:
            raise RuntimeError("connection lost")
        await asyncio.sleep(3600)

    @addon.on("tasks.task.created")
    async def on_task(user, data):
        SEEN.append(("event", user.id, users.current().id, dict(data)))

    @addon.on("tasks.task.created")
    async def on_task_broken(user, data):
        raise RuntimeError("handler bug")

    @addon.channel("rt_beacon")
    async def beacon(user, message):
        SEEN.append(("channel", user.id, message.kind, message.data))
        return message.kind == "reminder"
    '''))
os.environ["DANA_ADDON_DIRS"] = str(ROOT)

from core import db, events, llm, notify, purposes, users  # noqa: E402
from core.agent import SLOTS, slot_text  # noqa: E402
from core.addons import loader, registry, scheduler, store  # noqa: E402

db.migrate()
users.bootstrap()
from addons.rt import hooks as rt  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


ADMIN = users.first_admin()
assert ADMIN is not None
FAILS = users.create("Fails")
NONE = users.create("Nobody")
store.connect(ADMIN, "rt")
store.connect(FAILS, "rt")


async def test_jobs() -> None:
    check("jobs: declared with their schedule", {j.key: (j.every is not None, j.daily, j.at_start)
                                                for j in registry.jobs() if j.addon == "rt"}
          == {"rt.tick": (True, None, False), "rt.morning": (False, "07:30", False), "rt.warm": (False, None, True)})
    rt.SEEN.clear()
    out = await scheduler.run_once(registry.job("rt.tick"))
    ran = [(u, acting) for what, u, acting, _ in rt.SEEN if what == "tick"]
    check("jobs: run for every user who has the addon, each as that user, never for others",
          sorted(ran) == sorted([(ADMIN.id, ADMIN.id), (FAILS.id, FAILS.id)]), ran)
    check("jobs: one user's failure is left out, the other's result kept", out == {ADMIN.id: ADMIN.name}, out)
    check("jobs: a pass writes the heartbeat", scheduler.last_run("rt", "tick") is not None)
    check("jobs: a job that never ran has none", scheduler.last_run("rt", "morning") is None)
    try:
        registry.job("rt.nope")
        missing = False
    except KeyError:
        missing = True
    check("jobs: an unknown job key raises", missing)


async def test_service() -> None:
    real = (scheduler.SERVICE_BACKOFF_MIN, scheduler.SERVICE_BACKOFF_MAX)
    scheduler.SERVICE_BACKOFF_MIN, scheduler.SERVICE_BACKOFF_MAX = 0.01, 0.02
    try:
        service = next(s for s in registry.services() if s.key == "rt.watcher")
        task = asyncio.create_task(scheduler._run_service(service, {}))
        for _ in range(100):
            if len(rt.STARTS) >= 3:
                break
            await asyncio.sleep(0.01)
        check("services: started again after it raised, until it runs", len(rt.STARTS) == 3 and not task.done(),
              rt.STARTS)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    finally:
        scheduler.SERVICE_BACKOFF_MIN, scheduler.SERVICE_BACKOFF_MAX = real


async def test_events() -> None:
    rt.SEEN.clear()
    n = events.emit("tasks.task.created", ADMIN, task_id="t1", title="Hi")
    events.emit("tasks.task.created", NONE, task_id="t2", title="Other")
    check("events: emit returns at once (handlers in the background)", not [s for s in rt.SEEN if s[0] == "event"])
    await events.drain()
    seen = [(u, acting, data) for what, u, acting, data in rt.SEEN if what == "event"]
    check("events: the handler ran once, as the user, with the data; a failing handler did not stop it",
          n == 2 and seen == [(ADMIN.id, ADMIN.id, {"task_id": "t1", "title": "Hi"})], (n, seen))
    check("events: nothing for a user without the addon", not [s for s in seen if s[0] == NONE.id])
    check("events: nothing pending after drain", events.pending() == [])


def test_events_without_loop() -> None:
    rt.SEEN.clear()
    events.emit("tasks.task.created", FAILS, task_id="t3", title="Sync")
    check("events: without a running loop the handlers run before emit returns",
          [s[1] for s in rt.SEEN if s[0] == "event"] == [FAILS.id])


async def test_channels() -> None:
    rt.SEEN.clear()
    sent = []

    class FakeTelegram:
        async def send_text(self, chat_id, text, parse_mode=None):
            sent.append((chat_id, text, parse_mode))

        async def notify_whatsapp_voice(self, **kwargs):
            return False

    notify.register_telegram(FakeTelegram())
    try:
        users.update(ADMIN.id, telegram_chat_id=4242)
        admin = users.get(ADMIN.id)
        out = await notify.send(admin, notify.Message(text="*Due*", parse_mode="Markdown", kind="reminder",
                                                      data={"task_id": "t1"}))
        check("notify.send: Telegram gets the text, the addon's channel the structured message",
              out.get("telegram") is True and out.get("rt_beacon") is True and sent == [(4242, "*Due*", "Markdown")]
              and ("channel", ADMIN.id, "reminder", {"task_id": "t1"}) in rt.SEEN, (out, sent))
        out = await notify.send(admin, notify.Message(kind="other"))
        check("notify.send: no text, no Telegram; a channel that skips says False", out == {"rt_beacon": False}, out)
        out = await notify.send(NONE, notify.Message(kind="reminder"))
        check("notify.send: no channel of an addon the user lacks", "rt_beacon" not in out, out)
    finally:
        notify.register_telegram(None)


def test_slots() -> None:
    check("slots: each slot has its joiner",
          SLOTS["tools"].join(["a", "b", "c"]) == "a, b, and c" and SLOTS["routing_heading"].join(["A", "B"]) == "A vs B"
          and SLOTS["guides"].join(["x", "y"]) == "x\n\ny" and SLOTS["send_via"].join([" or a", " or b"]) == " or a or b")
    tools_text = slot_text(ADMIN, "tools")
    check("slots: parts are trimmed (no stray spaces) and ordered by rank",
          "  " not in tools_text and tools_text.index("waving") < tools_text.index("greeting people"), tools_text)
    check("slots: {user} becomes the name", slot_text(ADMIN, "guides", "Robin").endswith("Wave at Robin."),
          slot_text(ADMIN, "guides", "Robin"))
    check("slots: suffix slots keep their own leading space", slot_text(ADMIN, "send_via").endswith(" or a wave"),
          repr(slot_text(ADMIN, "send_via")))
    check("slots: nothing of the addon for a user without it",
          "waving" not in slot_text(NONE, "tools") and "Wave at" not in slot_text(NONE, "guides"))
    check("slots: the table is what the loader checks against", "guides" in SLOTS and "nowhere" not in SLOTS)


def test_purposes() -> None:
    p = purposes.get("rt_summary")
    check("purposes: an addon's llm_jobs are registered with its profile and a label",
          p.addon == "rt" and p.profile == "deep" and p.label == "Rt summary", p)
    os.environ.pop("DEEP_MODEL", None)
    check("purposes: the profile's model by default", llm.model_for("rt_summary") == llm.OPUS, llm.model_for("rt_summary"))
    os.environ["MODEL_RT_SUMMARY"] = "anthropic/some-other-model"
    try:
        check("purposes: MODEL_<PURPOSE> overrides that purpose alone",
              llm.model_for("rt_summary") == "anthropic/some-other-model" and llm.model_for("weekly_review") != "anthropic/some-other-model")
    finally:
        os.environ.pop("MODEL_RT_SUMMARY")
    try:
        llm.model_for("rt_sumary")
        refused = False
    except purposes.UnknownPurpose:
        refused = True
    check("purposes: an unknown purpose raises", refused)
    try:
        purposes.register("weekly_review", "fast", addon="rt")
        taken = False
    except ValueError:
        taken = True
    check("purposes: a purpose of core cannot be taken by an addon", taken)
    check("loader: rt loaded as a installed addon", "rt" in loader.non_official())


async def main() -> None:
    await test_jobs()
    await test_service()
    await test_events()
    await test_channels()


asyncio.run(main())
test_events_without_loop()
test_slots()
test_purposes()

print(f"\n{sum(results)}/{len(results)} passed")
if not all(results):
    sys.exit(1)
