"""Prompts are the acting user's (docs/access.md, "The user's profile").
Run: python test_prompts_users.py

Every prompt written for or about a user takes their name, pronoun and context
from their own row (``core/user_profile.py``). Checked here:
- the first admin's profile as the startup creates it (OWNER_NAME /
  OWNER_CONTEXT read once, "he", "the owner" in Dana's instructions);
- a member with only core + tasks: the agent's system message (as sent to the
  model) and the realtime instructions name them, and mention no other user
  and nothing of a kind they have not connected;
- a second user with a profile (she, her own context) and every kind a member
  may have: the agent's instructions, the shared phone screen, the on-screen
  line, the briefing, the weekly review, call analysis, notification
  extraction / triage / summaries, people briefs, the About learner and the
  call labels carry her name, pronoun and context and nothing of the first
  admin's (their name, context or pronoun);
- labels: the generic wording for a user whose context does not name the
  people a label is about, the personal one when it does;
- a profile change reaches the prompts without a restart.
The model is never called (``litellm.acompletion`` captured).
"""

import asyncio
import datetime
import os
import re
import sys
from typing import Any, Dict, List, Tuple

from cryptography.fernet import Fernet

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("prompts_users_test")
os.environ["OWNER_NAME"] = "Robin Levi"
os.environ["OWNER_CONTEXT"] = "Robin lives in Tel Aviv with Gal and the dog Pepper; he works in tech."
os.environ["TIMEZONE"] = "Asia/Jerusalem"
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway test key
os.environ["HOMEASSISTANT_HOST"] = "http://127.0.0.1:9"  # fake values: the startup import connects these
os.environ["HOMEASSISTANT_TOKEN"] = "prompts-ha-token"
os.environ["WAHA_URL"] = "http://127.0.0.1:9/api"
os.environ["WAHA_API_KEY"] = "prompts-waha-key"
os.environ["GOOGLE_REFRESH_TOKEN"] = "prompts-refresh-token"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import litellm  # noqa: E402
from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices  # noqa: E402

from core import db, user_rows, users  # noqa: E402

db.migrate()
users.bootstrap()
from integrations import bootstrap as integrations_bootstrap  # noqa: E402
integrations_bootstrap.bootstrap()

from core import briefing, device_screen, session_runner, user_profile, weekly_review  # noqa: E402
from core.agent import AgentOrchestrator, instructions  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from core.realtime import build_realtime_session_instructions  # noqa: E402
from integrations.about import facts as about_facts  # noqa: E402
from integrations.about import learner  # noqa: E402
from integrations.framework import registry, store  # noqa: E402
from integrations.homeassistant import home_map  # noqa: E402
from integrations.people import briefs  # noqa: E402
from integrations.people import store as people_store  # noqa: E402
from integrations.phone.calls import analysis, context, labels  # noqa: E402
from integrations.phone.notifications import processing, summary, triage  # noqa: E402

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    ok = bool(condition)
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {detail}" if detail and not ok else ""))


def words(text: str, *needles: str) -> List[str]:
    """The ``needles`` found in ``text`` as whole words (case-sensitive)."""
    return [n for n in needles if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", text)]


captured: List[Dict[str, Any]] = []


async def fake_acompletion(**kwargs):
    captured.append(kwargs)

    async def gen():
        yield ModelResponseStream(model="fake", choices=[StreamingChoices(
            index=0, delta=Delta(content="Done."), finish_reason="stop")])
    return gen()


litellm.acompletion = fake_acompletion
litellm.suppress_debug_info = True


def system_message(user: users.User) -> str:
    captured.clear()
    with users.acting_as(user):
        asyncio.run(AgentOrchestrator(model="fake/model").run("hello", [{"role": "user", "content": "hello"}]))
    return captured[0]["messages"][0]["content"]


ADMIN = users.first_admin()
# What only the first admin's prompts may carry: their name, the people in their context, their context itself.
ADMIN_MARKS = ("Robin", "Levi", "רובין", "Pepper", "the owner")

# --- 1. The first admin's profile -----------------------------------------------------------------

check("first admin: profile from the startup (OWNER_CONTEXT, he, other names, 'the owner')",
      ADMIN.name == "Robin Levi" and ADMIN.context == os.environ["OWNER_CONTEXT"] and ADMIN.pronoun == "he"
      and ADMIN.other_names == testenv.OWNER_OTHER_NAMES and ADMIN.prompt_name == "the owner",
      (ADMIN.name, ADMIN.context, ADMIN.pronoun, ADMIN.other_names, ADMIN.prompt_name))
who = user_profile.for_user(ADMIN)
check("first admin: Profile words", (who.first_name, who.called, who.referent, who.possessive, who.reflexive)
      == ("Robin", "Robin (רובין)", "the owner", "his", "himself"))
check("first admin: their instructions say 'the owner'", "act only on what the owner himself asks" in
      instructions(ADMIN))

# --- 2. A member with only core + tasks ------------------------------------------------------------

BOB = users.create("Bob Stone")
store.connect(BOB, "tasks")
check("member: only core + tasks", store.connected_kinds(BOB) == ("core", "tasks"), store.connected_kinds(BOB))
sm = system_message(BOB)
rt = build_realtime_session_instructions(BOB)
# ("call" / "notification" alone are generic here: "calls you Dana", "service calls", a reminder's notification.)
LACKING = ("Home Assistant", "HomeAssistant", "call_ha_service", "WhatsApp", "calendar", "Calendar", "phone call",
           "phone calls", "recording", "list_recent_calls", "Inbox", "shopping", "Shopping", "knowledge", "project",
           "projects", "remember_info", "memory", "kubectl", "Python", "camera", "Gal", "gal", "אמא")
for name, text in (("system message", sm), ("realtime instructions", rt)):
    check(f"member: {name} names Bob", "Bob" in text, text[:400])
    check(f"member: {name} names no other user", not words(text, *ADMIN_MARKS), words(text, *ADMIN_MARKS))
    check(f"member: {name} mentions no capability Bob lacks", not words(text, *LACKING), words(text, *LACKING))
    check(f"member: {name} has the tasks part", "TASKS" in text and "create a reminder" in text)
check("member: the system message is the instructions + the time line (no About, no home map)",
      sm.startswith(instructions(BOB)) and "About Bob" not in sm and "HOME MAP" not in sm.upper())
check("member: the tool list matches (core + tasks only)",
      {t["function"]["name"] for t in captured[0]["tools"]} == {t["function"]["name"]
                                                                for t in registry.tool_schemas(BOB)}
      and not registry.has_tool(BOB, "call_ha_service") and registry.has_tool(BOB, "add_task"))

# --- 3. Maya: a profile and every kind a member may have -------------------------------------------

MAYA_CONTEXT = "Maya is a nurse in Haifa. Noa is her sister; Lior is her husband."
MAYA = users.create("Maya Katz", profile=user_rows.Profile(context=MAYA_CONTEXT, pronoun="she"))
for kind in store.connected_kinds(ADMIN):
    if kind in ("core", "ops"):
        continue
    conn = store.get(ADMIN, kind)
    store.connect(MAYA, kind, credentials=dict(conn.credentials) if conn else None,
                  settings=dict(conn.settings) if conn else None)
for u in (ADMIN, MAYA):
    cache = home_map.cache_for(store.require(u, "homeassistant"))
    cache.text, cache.next_refresh = "", float("inf")  # Home Assistant is never called
about_facts.add_fact(MAYA, "Noa calls on Fridays", "people")
about_facts.refresh_prompt(MAYA)
check("Maya: every kind but ops", "ops" not in store.connected_kinds(MAYA)
      and {"homeassistant", "whatsapp", "calendar", "phone", "people", "about"} <= set(store.connected_kinds(MAYA)),
      store.connected_kinds(MAYA))


def own(name: str, text: str, *, needs: Tuple[str, ...] = ("Maya",), forms: Tuple[str, ...] = ()) -> None:
    """``text`` carries Maya's name (and her pronoun ``forms``) and nothing of the first admin's."""
    check(f"Maya: {name} — her name", all(n in text for n in needs), text[:600])
    if forms:
        check(f"Maya: {name} — her pronoun", words(text, *forms) and not words(text, "his", "him", "himself"),
              (words(text, *forms), words(text, "his", "him", "himself")))
    leaks = words(text, *ADMIN_MARKS, "Gal") + ([ADMIN.context] if ADMIN.context in text else [])
    check(f"Maya: {name} — nothing of the first admin's", not leaks, leaks)


maya_sm = system_message(MAYA)
check("Maya: system message names her (with her pronoun) and carries her facts",
      "act only on what Maya herself asks" in maya_sm and "Noa calls on Fridays" in maya_sm, maya_sm[-800:])
leaks = words(maya_sm, *ADMIN_MARKS)
check("Maya: system message — nothing of the first admin's (name, 'the owner')", not leaks, leaks)
check("Maya: system message — the kinds' parts are there", "HomeAssistant control" in maya_sm
      and "WhatsApp" in maya_sm and "calendar" in maya_sm)
check("Maya: the realtime instructions are hers", "Maya" in build_realtime_session_instructions(MAYA)
      and not words(build_realtime_session_instructions(MAYA), *ADMIN_MARKS))

screen = device_screen.prompt_block(MAYA, {"app": "Chrome", "text": "hello"}, nonce="abcd")
own("phone screen block", screen, forms=("she", "her"))
check("Maya: phone screen block — 'she was using'", "in the app she was using when she opened you" in screen
      and "Maya's phone screen" in screen)
check("first admin: phone screen block unchanged wording", "on the owner's phone screen right now, in the app he was"
      in device_screen.prompt_block(ADMIN, {"app": "Chrome", "text": "hi"}, nonce="abcd"))
noa = people_store.create_person(MAYA, "Noa", phones=["+972501112233"])
on_screen = session_runner.screen_context_prompt(MAYA, {"kind": "person", "id": noa["id"]})
check("Maya: on-screen line names her", on_screen and on_screen.split("\n")[1].startswith("Maya is looking")
      and not words(on_screen, *ADMIN_MARKS), on_screen)

day = {"weekday": "Tuesday", "events": [], "tasks": {"due": []}, "calendar_ok": True}
own("briefing", briefing.briefing_prompt(day, MAYA), needs=("Maya Katz", MAYA_CONTEXT), forms=("She", "her"))
cands = {"items": [], "stats": {"done": 1, "done_titles": ["x"], "created": 0, "open": 0}, "groups": ["General"]}
own("weekly review", weekly_review.review_prompt(cands, None, datetime.date(2026, 3, 14), MAYA),
    needs=("Maya Katz", MAYA_CONTEXT), forms=("her",))

ctx = asyncio.run(context.gather_context(MAYA))
call = {"when": datetime.datetime(2026, 3, 14, 10, 0), "duration_seconds": 75, "direction": "incoming",
        "phone_number": "+972501112233", "contact_name": "Noa"}
prompt = analysis.build_analysis_prompt(MAYA, "Noa: hi. Maya: hi.", call, ctx)
own("call analysis", prompt, needs=("Maya", MAYA_CONTEXT))
check("Maya: call analysis — her people, generic labels", "Noa" in prompt and "Partner" in prompt
      and "Pepper" not in prompt)
check("Maya: call feedback block", not words(context.feedback_prompt(MAYA, ctx, "call"), *ADMIN_MARKS))

note = {"id": "n1", "app_name": "WhatsApp", "title": "Noa", "sender": "Noa", "text": "Can you call mom?",
        "posted_at": "2026-03-14T10:00:00"}
extract = processing.build_extraction_prompt(MAYA, note, ctx)
own("notification extraction", extract, needs=("Maya Katz", MAYA_CONTEXT), forms=("her", "herself", "she"))
check("Maya: extraction — verbs agree ('she does', 'Maya herself has')", "work she does at work" in extract
      and "Maya herself has to do" in extract)
admin_extract = processing.build_extraction_prompt(ADMIN, note, ctx)
check("first admin: extraction wording unchanged", "a person asking him to do something" in admin_extract
      and "Messages Robin wrote himself" in admin_extract and "work he does at work" in admin_extract
      and "Only things Robin himself has to do" in admin_extract)
qs = str(triage.questions("Maya", "she"))
check("Maya: triage questions name her with her pronoun", "Maya" in qs and "Did Maya herself write" in qs
      and "a bill she pays herself" in qs and not words(qs, "his", "him", "himself", "Robin"),
      words(qs, "his", "him", "himself", "Robin"))
check("they: triage questions agree ('a bill they pay themselves')",
      "a bill they pay themselves" in str(triage.questions("Sam", "they")))
check("first admin: triage questions unchanged", "Did Robin himself write" in str(triage.questions("Robin", "he"))
      and "a bill he pays himself" in str(triage.questions("Robin", "he"))
      and "a delivery that needs him" in str(triage.questions("Robin", "he")))
state = triage.build_state(note, user_profile.for_user(MAYA).name, user_profile.for_user(MAYA).context)
check("Maya: triage state is hers", state["owner"] == {"name": "Maya Katz", "about": MAYA_CONTEXT})
items = [{"posted_at": "2026-03-14T10:00:00", "sender": "Noa", "title": "Noa", "text": "Dinner?"}]
own("notification app summary", summary.app_prompt(MAYA, items))
own("notification overall summary", summary.overall_prompt(MAYA, {"WhatsApp": items}, {"WhatsApp": "Noa: dinner"}))

with get_db_connection() as conn:
    row = conn.execute("SELECT * FROM people WHERE id = ?", (noa["id"],)).fetchone()
    own("people brief", briefs.brief_prompt(conn, MAYA, row))

learn = learner.build_learn_prompt(MAYA, [{"ref": "chat-1", "kind": "chat", "text": "hi"}], [], [])
check("Maya: About learner — her name, her pronoun", "durable facts about Maya Katz" in learn
      and "is her brother" in learn and not words(learn, "Robin", "his"), words(learn, "Robin", "his"))
check("first admin: About learner example unchanged", "\"Dan is his brother\"" in learner.build_learn_prompt(
    ADMIN, [], [], []))

# --- 4. Labels -------------------------------------------------------------------------------------

maya_labels = labels.catalog_for_prompt(MAYA)
admin_labels = labels.catalog_for_prompt(ADMIN)
check("labels: the same generic catalog for everyone, with their own first name",
      "- partner: Partner — calls with Maya's partner" in maya_labels
      and "- pets: Pets — pets: vet, vaccines, grooming" in maya_labels
      and "- wedding: Wedding — a wedding being planned" in maya_labels
      and "- tech_help: Tech help — Maya helping someone" in maya_labels
      and "- partner: Partner — calls with Robin's partner" in admin_labels
      and not words(maya_labels, "Gal", "Pepper", "Robin"), maya_labels)
check("labels: names on the call page are the catalog's",
      labels.label_details(MAYA, ["partner", "pets"])[0]["name"] == "Partner"
      and labels.label_details(ADMIN, ["partner"])[0]["name"] == "Partner")
check("labels: without a user, 'the user'", "calls with the user's partner" in labels.catalog_for_prompt(None))

# --- 5. A profile change reaches the prompts -------------------------------------------------------

users.update(MAYA.id, pronoun="they", context="Maya is a nurse.")
maya_now = users.get(MAYA.id)
check("profile change: the pronoun", "in the app they were using" in device_screen.prompt_block(
    maya_now, {"app": "Chrome", "text": "hi"}, nonce="abcd"))
check("profile change: the context", "About Maya:\nMaya is a nurse.\n\n" in briefing.briefing_prompt(day, maya_now))

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
