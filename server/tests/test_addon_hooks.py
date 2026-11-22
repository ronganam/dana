"""The hook API (core/addons/registry.py hooks / hook_for / active_addons): hooks only of addons a user has active (connected, with the addon's own needs), the briefing / review split with declared section and count keys, keyed hooks refusing a user without the addon, tool describe(user, args) with a fallback when it raises, channels and event handlers scoped the same way.

Run: bin/test test_addon_hooks

Two site addons (through DANA_ADDON_DIRS): ``hk`` declares every keyed hook,
a briefing and a review section, and two confirm tools with describe=; ``hkx``
needs ``hk`` and has a Now card hook, a channel and an event handler. Robin
has both, Gal has only ``hkx`` (its need is missing), Maya has neither.
"""

import asyncio
import os
import sys
import textwrap
import threading
from pathlib import Path

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("addon_hooks_test")

ROOT = Path(testenv.scratch_path("addon_dirs"))
(ROOT / "hk").mkdir(parents=True)
(ROOT / "hkx").mkdir(parents=True)
(ROOT / "hk" / "addon.py").write_text("from core.addons import Addon\naddon = Addon('hk', description='Hooks test')\n")
(ROOT / "hk" / "hooks.py").write_text(textwrap.dedent('''\
    from core.addons import Approval, Card, Check, Connection, Section
    from addons.hk.addon import addon

    @addon.briefing("hk", counts=("hk_due",))
    def briefing(user, ctx):
        return Section({"for": user.name, "day": ctx.today}, points=["2 hk things"], counts={"hk_due": 2, "sneaky": 9})

    @addon.review("hk")
    def review(user, ctx):
        return Section({"hk_stats": {"open": 1}}, items=[{"ref": "h1", "type": "hk_item", "title": "Old hk thing",
                                                         "why": "stale", "facts": []}])

    @addon.review_action("hk_item", ("complete", "keep"))
    async def apply(user, item, action, decision, today):
        return f"{action} by {user.name}"

    @addon.now_action("hk")
    async def act(user, ref, action, args, now):
        return {"ok": True, "message": f"{action} {ref} for {user.name}"}

    @addon.selfcheck("hk_check", "Hk check")
    def check(user, now):
        return Check(True, "fine")

    @addon.context("hk")
    def context(user, ref_id, req):
        return f"About hk {ref_id} for {user.name}."

    @addon.context_card("hk")
    def card(user, ref_id):
        return {"kind": "hk", "ref_id": ref_id, "title": f"Hk {ref_id}"}

    def describe_named(user, args):
        return Approval(title=f"Send {args.get('text')} as {user.name}", heading="Hk", emoji="*", question="Send?")

    def describe_broken(user, args):
        raise RuntimeError("describe bug")

    @addon.tool(confirm=True, describe=describe_named)
    def hk_send(conn: Connection, text: str) -> str:
        """Send it.

        Args:
            text: What to send.
        """
        return text

    @addon.tool(confirm=True, describe=describe_broken)
    def hk_burn(conn: Connection, text: str) -> str:
        """Burn it.

        Args:
            text: What to burn.
        """
        return text
    '''))
(ROOT / "hkx" / "addon.py").write_text(
    "from core.addons import Addon, Need\naddon = Addon('hkx', description='Needs hk', needs=(Need('addon', 'hk', 'Its hooks'),))\n")
(ROOT / "hkx" / "hooks.py").write_text(textwrap.dedent('''\
    import asyncio
    import threading
    from core.addons import Card
    from addons.hkx.addon import addon

    SEEN = []

    @addon.now_cards
    def cards(user, ctx):
        return [Card(id="hk:1", kind="hk", source="hkx", title=f"For {user.name}")]

    @addon.channel("hk_beep")
    async def beep(user, message):
        SEEN.append(("beep", user.id))
        return True

    @addon.channel("hk_boom")
    async def boom(user, message):
        raise RuntimeError("channel down")

    @addon.on("tasks.task.created")
    async def on_task(user, data):
        SEEN.append(("event", user.id, threading.get_ident()))

    @addon.on("hkx.thing.slow")
    async def slow(user, data):
        SEEN.append(("slow started", user.id))
        await asyncio.sleep(3600)
        SEEN.append(("slow finished", user.id))
    '''))
os.environ["DANA_ADDON_DIRS"] = str(ROOT)

from core import approval_text, briefing, checks, clock, context_chats, db, events, notify, now as now_screen, users  # noqa: E402
from core import weekly_review  # noqa: E402
from core.addons import registry, store  # noqa: E402

db.migrate()
users.bootstrap()
from addons.hkx import hooks as hkx  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


ROBIN = users.first_admin()
assert ROBIN is not None
GAL = users.create("Gal")
MAYA = users.create("Maya")
store.connect(ROBIN, "hk")
store.connect(ROBIN, "hkx")
store.connect(GAL, "hkx")
NOW = clock.now()


def test_scope() -> None:
    check("active: an addon whose own needs the user lacks is not active",
          "hkx" in registry.active_addons(ROBIN) and "hkx" not in registry.active_addons(GAL)
          and "hkx" in store.connected_addons(GAL))
    check("hooks: only from active addons (Robin has hkx's card, Gal does not, Maya neither)",
          [h.addon for h in registry.hooks("now_cards", ROBIN) if h.addon.startswith("hk")] == ["hkx"]
          and not [h for h in registry.hooks("now_cards", GAL) if h.addon.startswith("hk")]
          and not [h for h in registry.hooks("now_cards", MAYA) if h.addon.startswith("hk")])
    check("hooks: None is the server's view (every enabled addon)",
          "hkx" in [h.addon for h in registry.hooks("now_cards", None)])
    check("hook_for: the user's hook", registry.hook_for("now_action", "hk", ROBIN).addon == "hk")
    try:
        registry.hook_for("now_action", "hk", MAYA)
        refused = False
    except store.NotConnected as e:
        refused = e.addon == "hk"
    check("hook_for: a key of an addon the user lacks raises NotConnected (403 not_connected)", refused)
    check("hook_for: an unknown key is None", registry.hook_for("now_action", "nobody", ROBIN) is None)
    check("channels / handlers: scoped to active addons",
          [c.name for c in registry.channels(ROBIN) if c.addon == "hkx"] == ["hk_beep", "hk_boom"]
          and not [c for c in registry.channels(GAL) if c.addon == "hkx"]
          and not [h for h in registry.handlers("tasks.task.created", GAL) if h.addon == "hkx"])


def test_composers() -> None:
    parts = briefing.sections(ROBIN, NOW)
    hk = parts.get("hk")
    check("briefing: the section is stored under its declared key, for that user",
          hk is not None and hk.data == {"for": ROBIN.name, "day": NOW.date().isoformat()}, parts)
    check("briefing: undeclared counts are dropped, declared ones kept", hk is not None and hk.counts == {"hk_due": 2},
          hk.counts if hk else None)
    check("briefing: the counts reach the push", briefing.counts(ROBIN).get("hk_due") == 2
          and "sneaky" not in briefing.counts(ROBIN))
    check("briefing: none for a user without the addon", "hk" not in briefing.sections(MAYA, NOW))
    review = briefing.collect("review", ROBIN, NOW)
    check("review: its own hook, not the briefing's", list(review) == [k for k in review if k in ("hk", "tasks")]
          and review["hk"].items[0]["ref"] == "h1" and "for" not in (review["hk"].data or {}), review)
    check("review: its items join the candidates", any(i["ref"] == "h1" for i in weekly_review.candidates(ROBIN, NOW)["items"]))
    check("review action: actions for the type", weekly_review.actions_for("hk_item") == ("complete", "keep"))
    msg = asyncio.run(weekly_review._apply_one({"ref": "h1", "type": "hk_item"}, "complete", {}, NOW.date(), ROBIN))
    check("review action: applied as the user", msg == f"complete by {ROBIN.name}", msg)
    try:
        asyncio.run(weekly_review._apply_one({"ref": "h1", "type": "hk_item"}, "complete", {}, NOW.date(), MAYA))
        refused = False
    except store.NotConnected:
        refused = True
    check("review action: refused for a user without the addon", refused)

    out = asyncio.run(now_screen.act(ROBIN, "hk:7", "done"))
    check("now action: routed by prefix, as the user", out.get("message") == f"done 7 for {ROBIN.name}", out)
    try:
        asyncio.run(now_screen.act(MAYA, "hk:7", "done"))
        refused = False
    except store.NotConnected:
        refused = True
    check("now action: NotConnected for a user without the addon", refused)

    check("context card / prompt: the user's", context_chats.build_chat_context(ROBIN, "hk", "9")["title"] == "Hk 9"
          and context_chats.context_prompt(ROBIN, {"kind": "hk", "ref_id": "9"}) == f"About hk 9 for {ROBIN.name}.")
    try:
        context_chats.build_chat_context(MAYA, "hk", "9")
        refused = False
    except store.NotConnected:
        refused = True
    check("context card: NotConnected for a user without the addon; no prompt either",
          refused and context_chats.context_prompt(MAYA, {"kind": "hk", "ref_id": "9"}) is None)
    check("self-check: listed with its label, applies only to users who have it",
          "hk_check" in checks.keys_for(ROBIN) and "hk_check" not in checks.keys_for(MAYA)
          and checks.label_of("hk_check") == "Hk check")


def test_describe() -> None:
    with users.acting_as(ROBIN):
        named = approval_text.describe("hk_send", {"text": "hello"})
        broken = approval_text.describe("hk_burn", {"text": "hello"})
    check("describe: called with the current user first", named["title"] == f"Send hello as {ROBIN.name}", named)
    check("describe: one that raises falls back to the default card",
          broken["title"] and "hello" in str(broken["facts"]) + broken["title"], broken)


async def test_delivery() -> None:
    hkx.SEEN.clear()
    robin = await notify.send(ROBIN, notify.Message(kind="hk", data={}))
    gal = await notify.send(GAL, notify.Message(kind="hk", data={}))
    check("notify: a channel only for users with its addon active", robin.get("hk_beep") is True and "hk_beep" not in gal,
          f"{robin} {gal}")
    check("notify: a raising channel is reported as not delivered and does not stop the others",
          robin.get("hk_boom") is False and robin.get("hk_beep") is True, robin)
    loop_thread = threading.get_ident()
    events.emit("tasks.task.created", ROBIN, task_id="t")
    events.emit("tasks.task.created", GAL, task_id="t")
    await events.drain()
    check("events: handlers only for users with the addon active",
          [s for s in hkx.SEEN if s[0] == "event"] == [("event", ROBIN.id, loop_thread)], hkx.SEEN)
    hkx.SEEN.clear()
    # From a worker thread while the loop runs: handed to the loop, run there.
    events.bind(asyncio.get_running_loop())
    started = await asyncio.to_thread(events.emit, "tasks.task.created", ROBIN, task_id="t2")
    await events.drain()
    check("events: emitted from another thread, the handler runs on the server's loop",
          started == 1 and hkx.SEEN == [("event", ROBIN.id, loop_thread)], hkx.SEEN)
    hkx.SEEN.clear()
    events.emit("hkx.thing.slow", ROBIN)
    await asyncio.sleep(0.05)
    running = list(events.pending())
    await events.stop()
    check("events: stop() cancels the handlers still running",
          running and all(t.cancelled() for t in running) and not events.pending()
          and hkx.SEEN == [("slow started", ROBIN.id)], hkx.SEEN)


def test_core_imports_no_addons() -> None:
    import ast
    core_dir = Path(__file__).resolve().parent.parent / "core"
    offenders = []
    for path in sorted(core_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "addons" or alias.name.startswith("addons."):
                        offenders.append(f"{path.relative_to(core_dir)}:{node.lineno}:{alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod == "addons" or mod.startswith("addons."):
                    offenders.append(f"{path.relative_to(core_dir)}:{node.lineno}:{mod}")
    check("core imports zero addons", not offenders, offenders)


test_scope()
test_composers()
test_core_imports_no_addons()
test_describe()
asyncio.run(test_delivery())

passed = sum(results)
print(f"\n{passed}/{len(results)} passed")
if passed != len(results):
    sys.exit(1)
