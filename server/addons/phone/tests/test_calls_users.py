"""Phone calls per user: uploads, processing, suggestions, context chats.

Two users with the ``phone`` addon (the first admin, migrated from the 8.1.0
fixture, and Bob), one with ``phone`` but no ``tasks`` (Dee) and one without
``phone`` (Carl), on the real handlers: the 8.1.0 call / suggestions / FTS rows
become the first admin's and the recording is still served to them; uploads
belong to the uploader (dedupe per user) and are transcribed and analysed as
their owner (usage rows theirs; the analysis prompt built only from the
owner's people, projects, tasks, lists, zones and name, never the first
admin's ``OWNER_CONTEXT`` or personal labels); the pop-up goes to the owner's
phone only; calls, recordings, transcripts, labels, search (FTS, tools, the
search box), reanalysis and deletion are the owner's (another user's id →
404); suggestions are listed / accepted (into the owner's tasks / calendar) /
dismissed / edited / undone only by their owner, 403 ``not_connected`` when
the addon they need is missing; ``PUT /api/calls/{id}`` refuses a project the
caller cannot see; task links to calls only for their owner; context chats
are the owner's sessions; people linking and the About learner read only the
owner's calls; startup resume and audio retention per user with a failing
user not stopping the next; no ``phone`` → 403 and no call tools. The first
admin's prompts are compared byte for byte with ``tests/fixtures/calls/``,
written by the code before calls were per user.

The model is LiteLLM's offline ``mock_response`` (so the real usage callback
records each call); Home Assistant zones, the calendar's ``add_event`` and the
phone push are faked where they are called.

Run: bin/test test_calls_users
"""

import asyncio
import datetime
import json
import os
import sqlite3
import sys
from typing import Any, Dict, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
DB_PATH = testenv.isolate("calls_users_test")
ROOT = str(testenv.SERVER)
os.environ["API_TOKEN"] = "calls-users-test-env-token"  # a test value: with it set, DB tokens are honoured
os.environ["GEMINI_API_KEY"] = "test-key"  # a test value: LiteLLM only ever runs its offline mock here
os.environ["GOOGLE_REFRESH_TOKEN"] = "test-refresh-token"  # the first admin's calendar (add_event is faked)
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, ROOT)

# The production database before users existed: its call, suggestions and FTS rows become the first admin's.
_conn = sqlite3.connect(DB_PATH)
with open(os.path.join(testenv.FIXTURES, "db_8_1_0.sql"), encoding="utf-8") as _f:
    _conn.executescript(_f.read())
_conn.close()

import litellm
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from core import db, users

db.migrate()
users.bootstrap()
from addons import bootstrap as addons_bootstrap

addons_bootstrap.bootstrap()

import core.server as server
from core import sessions_api
from addons.phone import api as phone_api
from addons.phone.calls import api as calls_api
from addons.tasks import api as tasks_api
from core import clock, context_chats, now_api, search, undo, usage, user_rows
from core.auth import setup_auth
from core.db import get_db_connection
from addons.about import learner
from addons.calendar import tools as calendar_tools
from core.addons import registry, store
from core.addons.registry import ToolNotAvailable
from addons.homeassistant import client as ha_client
from addons.knowledge import projects as knowledge_projects
from addons.people import briefs as people_briefs, store as people_store
from addons.phone import popups, push, suggestions as phone_suggestions
from addons.phone.calls import analysis, context as call_context, processing, retention
from addons.phone.calls.store import RECORDINGS_DIR
from addons.tasks import groups as task_groups, links as task_links, service as tasks_service
from addons.tasks import titles as task_titles

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


ADMIN = users.first_admin()
assert ADMIN is not None
CALL_TOOLS = {"list_recent_calls", "search_calls", "get_call_transcript", "list_call_suggestions",
              "accept_call_suggestion", "dismiss_call_suggestion"}
FIXTURES = os.path.join(testenv.FIXTURES, "calls")
OLD_RECORDING = "11111111-2222-3333-4444-555555555555_call.m4a"


# --- 1. Migration ---------------------------------------------------------------------------------

def test_migration() -> None:
    with get_db_connection() as conn:
        calls = {r[0]: r[1] for r in conn.execute("SELECT id, user_id FROM phone_calls")}
        sugg = {r[0]: r[1] for r in conn.execute("SELECT id, user_id FROM call_suggestions")}
        fts = [r[0] for r in conn.execute("SELECT id FROM phone_calls_fts WHERE phone_calls_fts MATCH '\"ladder\"*'")]
    check("migration: the 8.1.0 call is the first admin's", calls == {"c1": ADMIN.id}, calls)
    check("migration: both suggestions (call and notification) are the first admin's",
          sugg == {"cs1": ADMIN.id, "cs2": ADMIN.id}, sugg)
    check("migration: the FTS index still has the call", fts == ["c1"], fts)


test_migration()

BOB = users.create("Bob Stone")
CARL = users.create("Carl")
DEE = users.create("Dee")
store.connect(ADMIN, "homeassistant", credentials={"host": "http://ha.invalid", "token": "test-ha"})
for _kind in ("phone", "tasks", "people", "knowledge"):
    store.connect(BOB, _kind)
store.connect(BOB, "homeassistant", credentials={"host": "http://bob-ha.invalid", "token": "bob-ha"})
store.connect(BOB, "calendar", credentials={"refresh_token": "bob-refresh"})
store.connect(CARL, "tasks")
store.connect(DEE, "phone")
task_groups.ensure_default_groups_for_everyone()
TOKENS = {u.id: users.issue_token(u.id, "test")[0] for u in (ADMIN, BOB, CARL, DEE)}
NAMES = {ADMIN.id: "Robin", BOB.id: "Bob", DEE.id: "Dee"}


def auth(user) -> Dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[user.id]}"}


# --- Fakes ----------------------------------------------------------------------------------------

SEEN: Dict[str, Any] = {"transcribe": [], "prompts": [], "pushes": [], "events": []}
TRANSCRIPTS = {"admin": "אמא: המזגן קופץ. רובין: אני אחפש טכנאי מחר.",
               "bob": "Bob: let's book the offsite. Gilad: I'll send the venue list tonight."}
TOMORROW = (clock.now() + datetime.timedelta(days=1)).date().isoformat()
NEXT_WEEK = (clock.now() + datetime.timedelta(days=7)).date().isoformat()
_real_acompletion = litellm.acompletion


def analysis_for(name: str) -> Dict[str, Any]:
    return {
        "title": f"{name}'s call", "summary": [f"{name} talked"], "labels": ["home"], "importance": "normal",
        "is_automated": False, "participants": [], "key_facts": [],
        "action_items": [
            {"text": f"{name} follow-up", "owner": "me", "kind": "task", "due_date": TOMORROW, "priority": "medium",
             "group": "Home", "confidence": 0.9, "evidence": "said so"},
            {"text": f"{name} meeting", "owner": "me", "kind": "event", "event_start": f"{NEXT_WEEK}T16:15",
             "event_end": None, "priority": "medium", "confidence": 0.95, "evidence": "Thursday"}],
        "project_id": None, "security_alert": None}


async def fake_acompletion(**kwargs):
    """LiteLLM's offline mock, so the real usage callback records the call."""
    content = kwargs["messages"][0]["content"]
    uid = users.current_id() or ""
    if isinstance(content, list):  # audio → transcription (core.llm.transcribe)
        SEEN["transcribe"].append(uid)
        kwargs["mock_response"] = TRANSCRIPTS["bob" if uid == BOB.id else "admin"]
    else:
        SEEN["prompts"].append((uid, content))
        kwargs["mock_response"] = json.dumps(analysis_for(NAMES.get(uid, "?")), ensure_ascii=False)
    return await _real_acompletion(**kwargs)


litellm.acompletion = fake_acompletion
litellm.suppress_debug_info = True


async def fake_zones(conn):
    if conn.user.id == BOB.id:
        return [{"name": "Home", "entity_id": "zone.home"}, {"name": "Bob Gym", "entity_id": "zone.bob_gym"}]
    return [{"name": "Home", "entity_id": "zone.home"}, {"name": "Acme", "entity_id": "zone.acme"}]

ha_client.get_ha_zones_list = fake_zones


def fake_add_event(conn, title, start, end=None, description=None, location=None):
    SEEN["events"].append({"user": conn.user.id, "title": title})
    return f"Event created. ID: ev-{len(SEEN['events'])}"

calendar_tools.add_event = fake_add_event


async def fake_send(user, kind, data):
    SEEN["pushes"].append({"user": user.id, "kind": kind, "data": data})
    return True

push.send = fake_send
people_briefs.schedule_briefs = lambda user, ids: None  # briefs are test_people_users' subject


async def plain_title(t):
    return t


async def plain_group(user, group, title):
    return task_groups.default_group_id(user)

task_titles.sanitize_task_title = plain_title
task_titles.resolve_task_group = plain_group


def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    r = app.router
    r.add_post("/api/calls/upload", calls_api.upload_call_api)
    r.add_get("/api/calls", calls_api.get_calls_api)
    r.add_get("/api/calls/labels", calls_api.get_call_labels_api)
    r.add_get("/api/calls/{id}", calls_api.get_call_detail_api)
    r.add_put("/api/calls/{id}", calls_api.update_call_api)
    r.add_get("/api/calls/{id}/audio", calls_api.get_call_audio_api)
    r.add_delete("/api/calls/{id}", calls_api.delete_call_api)
    r.add_post("/api/calls/{id}/reanalyze", calls_api.reanalyze_call_api)
    r.add_get("/api/suggestions", phone_api.get_suggestions_api)
    r.add_post("/api/suggestions/bulk", phone_api.bulk_suggestions_api)
    r.add_put("/api/suggestions/{id}", phone_api.update_suggestion_api)
    r.add_post("/api/suggestions/{id}/accept", phone_api.accept_suggestion_api)
    r.add_post("/api/suggestions/{id}/dismiss", phone_api.dismiss_suggestion_api)
    r.add_post("/api/chat/context", sessions_api.start_context_chat_api)
    r.add_post("/api/tasks/{id}/links", tasks_api.add_task_link_api)
    r.add_post("/api/now/act", now_api.now_act_api)
    return app


async def settle() -> None:
    for _ in range(500):
        if not processing._tasks:
            return
        await asyncio.sleep(0.02)


async def usage_rows(purpose: str) -> List[Dict[str, Any]]:
    for _ in range(20):  # the usage callback records after the call returns
        await asyncio.sleep(0.05)
    await usage.flush()
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT user_id, ok, ref FROM usage_events WHERE purpose = ? ORDER BY id",
                                              (purpose,))]


def crow(call_id: str) -> Dict[str, Any]:
    with get_db_connection() as conn:
        r = conn.execute("SELECT * FROM phone_calls WHERE id = ?", (call_id,)).fetchone()
    return dict(r) if r else {}


def suggestions_of(call_id: str) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM call_suggestions WHERE call_id = ? ORDER BY created_at",
                                              (call_id,))]


async def tool(user, name: str, args: Dict[str, Any]) -> Tuple[str, List[str]]:
    with users.acting_as(user), undo.collect() as ids:
        out = await registry.execute(user, name, args)
    return str(out), [i["id"] for i in ids]


async def upload(client: TestClient, user, audio: bytes, filename: str, **fields) -> Tuple[int, Dict[str, Any]]:
    form = FormData()
    form.add_field("audio", audio, filename=filename, content_type="audio/mp4")
    for k, v in fields.items():
        form.add_field(k, v)
    r = await client.post("/api/calls/upload", data=form, headers=auth(user))
    return r.status, await r.json()


IDS: Dict[str, str] = {}


# --- 2. Recordings of the migrated call -----------------------------------------------------------

async def test_old_recording(client: TestClient) -> None:
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    with open(os.path.join(RECORDINGS_DIR, OLD_RECORDING), "wb") as f:
        f.write(b"OLD AUDIO")
    r = await client.get("/api/calls/c1/audio", headers=auth(ADMIN))
    check("migration: the first admin's recording is still served to them",
          r.status == 200 and await r.read() == b"OLD AUDIO", r.status)
    r = await client.get("/api/calls/c1/audio", headers=auth(BOB))
    check("recording: another user's → 404", r.status == 404)
    r = await client.get("/api/calls/c1", headers=auth(ADMIN))
    body = await r.json()
    check("migration: the call's detail has its suggestion and transcript for the first admin",
          r.status == 200 and [s["id"] for s in body["suggestions"]] == ["cs1"] and "ladder" in body["transcript"],
          body)


# --- 3. Upload and processing as the owner --------------------------------------------------------

async def test_upload_and_processing(client: TestClient) -> None:
    bob_person = people_store.create_person(BOB, "Gilad Bob", phones=["0501112222"])
    IDS["bob_person"] = bob_person["id"]
    when = clock.now().isoformat(timespec="seconds")
    status, a = await upload(client, ADMIN, b"SAME AUDIO", "rec.m4a", caller="אמא", call_timestamp=when,
                             phone_number="0501112222", direction="incoming")
    check("upload: the first admin's upload is accepted", status == 202 and a.get("status") == "processing", a)
    status, b = await upload(client, BOB, b"SAME AUDIO", "rec.m4a", caller="Gilad Bob", call_timestamp=when,
                             phone_number="0501112222", direction="incoming")
    check("upload: the same bytes and file name from Bob are a new call of his (dedupe per user)",
          status == 202 and b.get("id") != a.get("id") and not b.get("duplicate"), b)
    status, again = await upload(client, BOB, b"SAME AUDIO", "rec.m4a", caller="Gilad Bob")
    check("upload: Bob's own re-upload is a duplicate of his call", again.get("duplicate") and again.get("id") == b["id"],
          again)
    status, body = await upload(client, CARL, b"CARL AUDIO", "c.m4a")
    check("upload: no phone → 403 not_connected", status == 403 and body.get("error") == "not_connected", body)
    IDS["admin_call"], IDS["bob_call"] = a["id"], b["id"]
    check("upload: each call belongs to its uploader",
          crow(a["id"])["user_id"] == ADMIN.id and crow(b["id"])["user_id"] == BOB.id)
    await settle()

    ac, bc = crow(a["id"]), crow(b["id"])
    check("processing: both calls are ready with their own transcripts",
          ac["status"] == "ready" and bc["status"] == "ready" and "טכנאי" in ac["transcript"]
          and "offsite" in bc["transcript"], (ac["status"], ac["error"], bc["status"], bc["error"]))
    check("processing: transcribed as each owner", sorted(SEEN["transcribe"]) == sorted([ADMIN.id, BOB.id]),
          SEEN["transcribe"])
    trans = await usage_rows("call_transcript")
    anal = await usage_rows("call_analysis")
    by_ref = {r["ref"]: r["user_id"] for r in trans + anal}
    check("usage: transcription and analysis rows carry the call's owner",
          by_ref.get(f"call:{a['id']}") == ADMIN.id and by_ref.get(f"call:{b['id']}") == BOB.id
          and {r["user_id"] for r in anal} == {ADMIN.id, BOB.id}, trans + anal)

    prompts = dict(SEEN["prompts"])
    bob_prompt, admin_prompt = prompts.get(BOB.id, ""), prompts.get(ADMIN.id, "")
    check("prompt: Bob's analysis prompt names Bob, his zones, people and lists",
          "Bob" in bob_prompt and "Bob Gym" in bob_prompt and "Gilad Bob" in bob_prompt, bob_prompt[:800])
    leaks = [w for w in ("Robin", "רובין", "Gal ", "Gal,", "Gal'", "Gal)", "גל", "Pepper", "פפר", "Acme", testenv.OWNER_CONTEXT[:30])
             if w in bob_prompt]
    check("prompt: nothing of the first admin in Bob's prompt (name, OWNER_CONTEXT, labels, zones)", not leaks, leaks)
    check("prompt: the first admin's prompt keeps their context and has none of Bob's",
          testenv.OWNER_CONTEXT in admin_prompt and "Acme" in admin_prompt and "Bob Gym" not in admin_prompt
          and "Gilad Bob" not in admin_prompt)

    sa, sb = suggestions_of(a["id"]), suggestions_of(b["id"])
    check("suggestions: each call's suggestions belong to its owner",
          sa and sb and {s["user_id"] for s in sa} == {ADMIN.id} and {s["user_id"] for s in sb} == {BOB.id},
          (sa, sb))
    IDS["admin_task_sug"] = next(s["id"] for s in sa if s["kind"] == "task")
    IDS["bob_task_sug"] = next(s["id"] for s in sb if s["kind"] == "task")
    IDS["bob_event_sug"] = next(s["id"] for s in sb if s["kind"] == "event")
    pops = [p for p in SEEN["pushes"] if p["kind"] == "suggestions"]
    check("pop-up: each owner's phone gets only their own call's suggestions",
          sorted((p["user"], p["data"]["source_id"]) for p in pops) == sorted([(ADMIN.id, a["id"]), (BOB.id, b["id"])])
          and all(set(p["data"]["ids"]) <= {s["id"] for s in (sa if p["user"] == ADMIN.id else sb)} for p in pops), pops)
    n = len(SEEN["pushes"])
    check("pop-up: announcing another user's call for Bob sends nothing",
          not await popups.announce_suggestions(BOB, "call", a["id"], "x") and len(SEEN["pushes"]) == n)

    check("people: Bob's call is linked to Bob's person, the first admin's call (same number) is not",
          bc["person_id"] == IDS["bob_person"] and ac["person_id"] != IDS["bob_person"], (bc["person_id"], ac["person_id"]))
    with get_db_connection() as conn:
        bob_learned = [c["id"] for c in learner._calls_of(conn, BOB, "")]
        admin_learned = [c["id"] for c in learner._calls_of(conn, ADMIN, "")]
    check("learner: each user's pass reads only their own calls",
          bob_learned == [b["id"]] and b["id"] not in admin_learned and a["id"] in admin_learned
          and "c1" in admin_learned, (bob_learned, admin_learned))


# --- 4. Calls API, labels, search and tools --------------------------------------------------------

async def test_calls_api(client: TestClient) -> None:
    a, b = IDS["admin_call"], IDS["bob_call"]
    r = await client.get("/api/calls", headers=auth(BOB))
    bob_list = [c["id"] for c in await r.json()]
    r = await client.get("/api/calls", headers=auth(ADMIN))
    admin_list = [c["id"] for c in await r.json()]
    check("list: each user sees only their own calls", bob_list == [b] and set(admin_list) == {a, "c1"},
          (bob_list, admin_list))
    for method, path, kw in (("GET", f"/api/calls/{a}", {}), ("GET", f"/api/calls/{a}/audio", {}),
                             ("PUT", f"/api/calls/{a}", {"json": {"title": "Mine now"}}),
                             ("POST", f"/api/calls/{a}/reanalyze", {"json": {}}),
                             ("DELETE", f"/api/calls/{a}", {})):
        r = await client.request(method, path, headers=auth(BOB), **kw)
        check(f"calls: Bob's {method} {path.replace(a, '<admin call>')} → 404", r.status == 404, r.status)
    check("calls: the first admin's call untouched", crow(a)["title"] != "Mine now" and crow(a)["status"] == "ready")
    r = await client.get(f"/api/calls/{b}/audio", headers=auth(BOB))
    check("recording: the owner gets it", r.status == 200 and await r.read() == b"SAME AUDIO")

    r = await client.get("/api/calls/labels", headers=auth(BOB))
    bob_labels = json.dumps(await r.json(), ensure_ascii=False)
    r = await client.get("/api/calls/labels", headers=auth(ADMIN))
    admin_labels = json.dumps(await r.json(), ensure_ascii=False)
    check("labels: Bob's catalog has nothing of the first admin's",
          not any(w in bob_labels for w in ("Gal", "Pepper", "Robin")), bob_labels[:400])
    check("labels: the first admin's catalog is the same generic one", admin_labels == bob_labels and '"Partner"' in admin_labels,
          admin_labels[:400])

    # A project the caller cannot see.
    bob_project = knowledge_projects.create_project(BOB, "Offsite")
    r = await client.put(f"/api/calls/{b}", json={"project_id": "p1"}, headers=auth(BOB))
    check("PUT: another user's project → 404, call unchanged",
          r.status == 404 and (await r.json()).get("error") == "Project not found" and not crow(b)["project_id"])
    r = await client.put(f"/api/calls/{b}", json={"project_id": bob_project}, headers=auth(BOB))
    check("PUT: one's own project is set", r.status == 200 and crow(b)["project_id"] == bob_project)
    r = await client.put(f"/api/calls/{a}", json={"project_id": bob_project}, headers=auth(ADMIN))
    check("PUT: the first admin can't set Bob's project either", r.status == 404 and not crow(a)["project_id"])
    r = await client.put(f"/api/calls/{a}", json={"project_id": "p1", "labels": ["home"]}, headers=auth(ADMIN))
    check("PUT: the first admin's own project and labels", r.status == 200 and crow(a)["project_id"] == "p1")

    # Search: FTS tool, list tool, transcript tool, search box.
    out, _ = await tool(BOB, "search_calls", {"query": "טכנאי"})
    check("search_calls: Bob doesn't find the first admin's transcript", a not in out and "ID: c1" not in out, out[:300])
    out, _ = await tool(ADMIN, "search_calls", {"query": "טכנאי"})
    check("search_calls: the first admin does", a in out, out[:300])
    out, _ = await tool(BOB, "search_calls", {"query": "ladder"})
    check("search_calls: the migrated call isn't Bob's", "ID: c1" not in out, out[:300])
    out, _ = await tool(BOB, "list_recent_calls", {"limit": 10})
    check("list_recent_calls: Bob's only", b in out and a not in out and "ID: c1" not in out, out[:600])
    out, _ = await tool(BOB, "get_call_transcript", {"call_id": a})
    check("get_call_transcript: another user's call is not found", "טכנאי" not in out, out[:200])
    with users.acting_as(BOB):
        bob_hits = [x["id"] for x in search.search(BOB, "offsite", 10) if x["kind"] == "call"]
        bob_miss = [x["id"] for x in search.search(BOB, "ladder", 10) if x["kind"] == "call"]
    with users.acting_as(ADMIN):
        admin_hits = [x["id"] for x in search.search(ADMIN, "ladder", 10) if x["kind"] == "call"]
        admin_miss = [x["id"] for x in search.search(ADMIN, "offsite", 10) if x["kind"] == "call"]
    check("search box: calls are the caller's own (FTS)",
          bob_hits == [b] and not bob_miss and admin_hits == ["c1"] and not admin_miss,
          (bob_hits, bob_miss, admin_hits, admin_miss))


# --- 5. Suggestions --------------------------------------------------------------------------------

async def test_suggestions(client: TestClient) -> None:
    a_sug, b_task, b_event = IDS["admin_task_sug"], IDS["bob_task_sug"], IDS["bob_event_sug"]
    r = await client.get("/api/suggestions?source=all&status=all", headers=auth(BOB))
    bob_ids = {s["id"] for s in await r.json()}
    r = await client.get("/api/suggestions?source=all&status=all", headers=auth(ADMIN))
    admin_ids = {s["id"] for s in await r.json()}
    check("list: each user sees only their own suggestions (both sources)",
          {b_task, b_event} <= bob_ids and not bob_ids & admin_ids and {"cs1", "cs2", a_sug} <= admin_ids,
          (bob_ids, admin_ids))

    for method, path, kw in (("POST", f"/api/suggestions/{a_sug}/accept", {"json": {}}),
                             ("POST", f"/api/suggestions/{a_sug}/dismiss", {}),
                             ("PUT", f"/api/suggestions/{a_sug}", {"json": {"title": "Bob's now"}})):
        r = await client.request(method, path, headers=auth(BOB), **kw)
        check(f"suggestion: Bob's {method} {path.replace(a_sug, '<admin sug>')} → 404", r.status == 404, r.status)
    r = await client.post("/api/suggestions/bulk", json={"ids": [a_sug, "cs1"], "action": "dismiss"}, headers=auth(BOB))
    check("suggestion: Bob's bulk dismiss of the first admin's ids dismisses nothing",
          (await r.json()).get("dismissed") == 0)
    r = await client.post("/api/now/act", json={"id": f"sug:{a_sug}", "action": "accept"}, headers=auth(BOB))
    check("suggestion: Bob's Now accept of it fails", r.status != 200, r.status)
    out, _ = await tool(BOB, "dismiss_call_suggestion", {"suggestion_id": a_sug})
    check("suggestion: Bob's dismiss tool doesn't find it", out.startswith("Error"), out)
    out, _ = await tool(BOB, "accept_call_suggestion", {"suggestion_id": "cs1"})
    check("suggestion: Bob's accept tool doesn't find the migrated one", out.startswith("Error"), out)
    out, _ = await tool(BOB, "list_call_suggestions", {})
    check("list_call_suggestions: Bob's only", "Bob follow-up" in out and "Robin follow-up" not in out
          and "ladder" not in out, out[:300])
    check("suggestion: the first admin's are untouched",
          phone_suggestions.get_suggestion(ADMIN, a_sug)["status"] == "pending"
          and phone_suggestions.get_suggestion(ADMIN, a_sug)["payload"]["title"] == "Robin follow-up"
          and phone_suggestions.get_suggestion(ADMIN, "cs1")["status"] == "pending")
    check("suggestion: another user's id is not visible to the reads",
          phone_suggestions.get_suggestion(BOB, a_sug) is None and phone_suggestions.get_suggestion(ADMIN, b_task) is None)

    # Accept into the owner's tasks / calendar.
    r = await client.put(f"/api/suggestions/{b_task}", json={"title": "Bob follow-up (edited)"}, headers=auth(BOB))
    check("suggestion: Bob edits his own", r.status == 200
          and phone_suggestions.get_suggestion(BOB, b_task)["payload"]["title"] == "Bob follow-up (edited)")
    r = await client.post(f"/api/suggestions/{b_task}/accept", json={}, headers=auth(BOB))
    body = await r.json()
    task = tasks_service.get_task(BOB, body.get("task_id") or "") if body.get("task_id") else None
    check("accept: Bob's task suggestion becomes Bob's task, linked to his call",
          r.status == 200 and task and tasks_service.get_task(ADMIN, body["task_id"]) is None
          and any(l["ref_type"] == "call" and l["ref_id"] == IDS["bob_call"]
                  for l in task_links.links_for_tasks(BOB, [body["task_id"]]).get(body["task_id"], [])), body)
    IDS["bob_task"] = body.get("task_id")
    r = await client.post(f"/api/suggestions/{b_event}/accept", json={}, headers=auth(BOB))
    check("accept: Bob's event suggestion goes into Bob's calendar",
          r.status == 200 and SEEN["events"] and SEEN["events"][-1] == {"user": BOB.id, "title": "Bob meeting"},
          SEEN["events"])

    # Undo / links only for the owner.
    with get_db_connection() as conn:
        conn.execute("UPDATE call_suggestions SET status = 'pending', result_ref = NULL WHERE id = ?", (b_task,))
        conn.commit()
    out, undo_ids = await tool(BOB, "accept_call_suggestion", {"suggestion_id": b_task})
    new_task = out.split("ID: ")[1].split(")")[0] if "ID: " in out else ""
    check("undo: Bob's accept tool records an undo", undo_ids and new_task, out)
    try:
        with users.acting_as(ADMIN):
            await undo.run(undo_ids[0])
        refused = False
    except KeyError:
        refused = True
    check("undo: the first admin can't run Bob's undo", refused and tasks_service.get_task(BOB, new_task))
    with users.acting_as(BOB):
        await undo.run(undo_ids[0])
    check("undo: Bob's undo removes his task and reopens his suggestion",
          tasks_service.get_task(BOB, new_task) is None
          and phone_suggestions.get_suggestion(BOB, b_task)["status"] == "pending")
    out, undo_ids = await tool(BOB, "dismiss_call_suggestion", {"suggestion_id": b_task})
    try:
        with users.acting_as(ADMIN):
            await undo.run(undo_ids[0])
        refused = False
    except KeyError:
        refused = True
    check("undo: a dismiss's undo is the owner's too",
          refused and phone_suggestions.get_suggestion(BOB, b_task)["status"] == "dismissed", out)

    r = await client.post(f"/api/tasks/{IDS['bob_task']}/links", json={"ref_type": "call", "ref_id": IDS["admin_call"]},
                          headers=auth(BOB))
    check("links: linking another user's call to a task → 404", r.status == 404
          and (await r.json()).get("error") == "Call not found")
    r = await client.post(f"/api/tasks/{IDS['bob_task']}/links", json={"ref_type": "call", "ref_id": IDS["bob_call"]},
                          headers=auth(BOB))
    check("links: one's own call links", r.status == 200)
    out, _ = await tool(BOB, "add_task", {"title": "From the admin's call", "call_id": IDS["admin_call"]})
    with get_db_connection() as conn:
        linked = conn.execute("SELECT COUNT(*) FROM task_links WHERE ref_type = 'call' AND ref_id = ? AND task_id IN "
                              "(SELECT id FROM tasks WHERE user_id = ?)", (IDS["admin_call"], BOB.id)).fetchone()[0]
    check("links: add_task with another user's call id creates no link", linked == 0, out)
    admin_task = tasks_service.create_task(ADMIN, title="Admin's task",
                                           task_links=[("call", IDS["bob_call"], "related")])
    check("links: a link to another user's call is left out of the task's links",
          task_links.links_for_tasks(ADMIN, [admin_task]).get(admin_task, []) == [],
          task_links.links_for_tasks(ADMIN, [admin_task]))

    # A needed kind missing.
    with get_db_connection() as conn:
        conn.execute("INSERT INTO call_suggestions (id, user_id, call_id, source_type, kind, payload, status) "
                     "VALUES ('dee-sug', ?, 'dee-call', 'call', 'task', '{\"title\": \"Dee thing\"}', 'pending')",
                     (DEE.id,))
        conn.commit()
    r = await client.post("/api/suggestions/dee-sug/accept", json={}, headers=auth(DEE))
    check("accept: no tasks → 403 not_connected, still pending",
          r.status == 403 and await r.json() == {"error": "not_connected", "addon": "tasks"}
          and phone_suggestions.get_suggestion(DEE, "dee-sug")["status"] == "pending")
    check("tools: accept_call_suggestion not offered without tasks",
          "accept_call_suggestion" not in {t.name for t in registry.tools_for(DEE)}
          and "list_call_suggestions" in {t.name for t in registry.tools_for(DEE)})


# --- 6. Context chats -----------------------------------------------------------------------------

async def test_context_chats(client: TestClient) -> None:
    r = await client.post("/api/chat/context", json={"kind": "call", "id": IDS["bob_call"]}, headers=auth(BOB))
    body = await r.json()
    with get_db_connection() as conn:
        owner = conn.execute("SELECT user_id FROM sessions WHERE session_id = ?", (body.get("session_id"),)).fetchone()
    check("context chat: Bob's call opens a session of Bob's", r.status == 200 and owner and owner[0] == BOB.id, body)
    prompt = context_chats.session_context_prompt(BOB, body["session_id"]) or ""
    check("context chat: Bob's prompt is about his call and written for him",
          "offsite" in prompt and "Robin" not in prompt and "him" not in prompt.split("Transcript")[0], prompt[:600])
    check("context chat: the session isn't the first admin's", context_chats.session_context_prompt(ADMIN, body["session_id"])
          is None)
    for kind, ref in (("call", IDS["bob_call"]), ("suggestion", IDS["bob_event_sug"])):
        r = await client.post("/api/chat/context", json={"kind": kind, "id": ref}, headers=auth(ADMIN))
        check(f"context chat: the first admin on Bob's {kind} → 404", r.status == 404, r.status)
    for kind, ref in (("call", IDS["admin_call"]), ("suggestion", IDS["admin_task_sug"])):
        r = await client.post("/api/chat/context", json={"kind": kind, "id": ref}, headers=auth(BOB))
        check(f"context chat: Bob on the first admin's {kind} → 404", r.status == 404, r.status)


# --- 7. Reanalysis and deletion --------------------------------------------------------------------

async def test_reanalyze_delete(client: TestClient) -> None:
    before = len(await usage_rows("call_analysis"))
    r = await client.post(f"/api/calls/{IDS['bob_call']}/reanalyze", json={"custom_instructions": "dates"},
                          headers=auth(BOB))
    check("reanalyze: the owner's → 202", r.status == 202)
    await settle()
    rows = await usage_rows("call_analysis")
    check("reanalyze: run as the owner (usage row Bob's)",
          len(rows) == before + 1 and rows[-1]["user_id"] == BOB.id and rows[-1]["ref"] == f"call:{IDS['bob_call']}",
          rows[-2:])
    path = os.path.join(RECORDINGS_DIR, crow(IDS["bob_call"])["filename"])
    r = await client.delete(f"/api/calls/{IDS['bob_call']}", headers=auth(ADMIN))
    check("delete: another user's call → 404, kept", r.status == 404 and crow(IDS["bob_call"]) and os.path.exists(path))
    r = await client.delete(f"/api/calls/{IDS['bob_call']}", headers=auth(BOB))
    check("delete: the owner's call, its suggestions and recording go",
          r.status == 200 and not crow(IDS["bob_call"]) and not suggestions_of(IDS["bob_call"]) and not os.path.exists(path))


# --- 8. Resume and retention per user -------------------------------------------------------------

async def test_loops(client: TestClient) -> None:
    status, b = await upload(client, BOB, b"BOB AGAIN", "again.m4a", caller="Gilad Bob")
    await settle()
    IDS["bob_call2"] = b["id"]
    with get_db_connection() as conn:
        conn.execute("UPDATE phone_calls SET status = 'processing' WHERE id IN (?, ?)", (IDS["admin_call"], b["id"]))
        conn.commit()
    scheduled: List[str] = []
    real_schedule, real_resume = processing.schedule_processing, processing.resume_unfinished
    processing.schedule_processing = lambda cid, custom_instructions=None: scheduled.append(cid) or True

    def resume(user):
        if user.id == ADMIN.id:
            raise RuntimeError("broken")
        return real_resume(user)

    processing.resume_unfinished = resume
    app: Dict[str, Any] = {}
    try:
        server._start_phone_background(app)
    finally:
        processing.schedule_processing, processing.resume_unfinished = real_schedule, real_resume
        for t in app.values():
            t.cancel()
    check("resume: per user, the first admin's failure doesn't stop Bob's", scheduled == [b["id"]], scheduled)
    scheduled.clear()
    processing.schedule_processing = lambda cid, custom_instructions=None: scheduled.append(cid) or True
    try:
        n = processing.resume_unfinished(ADMIN)
    finally:
        processing.schedule_processing = real_schedule
    check("resume: a user's resume queues only their calls", n == 1 and scheduled == [IDS["admin_call"]], scheduled)
    with get_db_connection() as conn:
        conn.execute("UPDATE phone_calls SET status = 'ready', created_at = '2020-01-01 00:00:00' WHERE id IN (?, ?)",
                     (IDS["admin_call"], b["id"]))
        conn.commit()

    real_purge = retention.purge_old_audio

    def purge(user, days=90):
        if user.id == ADMIN.id:
            raise RuntimeError("broken")
        return real_purge(user, days)

    retention.purge_old_audio = purge
    try:
        out = await retention.purge_all()
    finally:
        retention.purge_old_audio = real_purge
    admin_file = os.path.join(RECORDINGS_DIR, crow(IDS["admin_call"])["filename"])
    check("retention: per user, the first admin's failure doesn't stop Bob's",
          out == {BOB.id: 1, DEE.id: 0} and crow(b["id"])["audio_deleted_at"] and not crow(IDS["admin_call"])["audio_deleted_at"]
          and os.path.exists(admin_file), out)
    out = await retention.purge_all()
    check("retention: then the first admin's own", out.get(ADMIN.id) == 1 and crow(IDS["admin_call"])["audio_deleted_at"],
          out)
    r = await client.get(f"/api/calls/{IDS['admin_call']}/audio", headers=auth(ADMIN))
    check("retention: an expired recording answers 410 to its owner", r.status == 410)
    r = await client.get(f"/api/calls/{IDS['admin_call']}/audio", headers=auth(BOB))
    check("retention: and 404 to another user", r.status == 404)


# --- 9. No phone ----------------------------------------------------------------------------------

async def test_no_kind(client: TestClient) -> None:
    names = {t.name for t in registry.tools_for(CARL)}
    check("no phone: no call tools", not names & CALL_TOOLS, names & CALL_TOOLS)
    try:
        await registry.execute(CARL, "search_calls", {"query": "x"})
        refused = False
    except ToolNotAvailable:
        refused = True
    check("no phone: a call tool is refused like an unknown one", refused)
    for method, path in (("GET", "/api/calls"), ("GET", "/api/calls/labels"), ("GET", f"/api/calls/{IDS['admin_call']}"),
                         ("GET", "/api/calls/c1/audio"), ("GET", "/api/suggestions"),
                         ("POST", f"/api/suggestions/{IDS['admin_task_sug']}/dismiss")):
        r = await client.request(method, path, headers=auth(CARL))
        check(f"no phone: {method} {path.split('/')[2]}… → 403 not_connected",
              r.status == 403 and (await r.json()) == {"error": "not_connected", "addon": "phone"}, r.status)
    with users.acting_as(CARL):
        hits = [x for x in search.search(CARL, "ladder", 10) if x["kind"] == "call"]
    check("no phone: the search box has no calls", not hits)


# --- 10. The first admin's prompts, byte for byte ---------------------------------------------------

CTX = {
    "people": ["mom: 972500000077@c.us", "gal_contact: 0501112222"],
    "projects": [{"id": "p1", "title": "Wedding"}],
    "open_tasks": [{"id": "t1", "title": "Buy milk"}, {"id": "t2", "title": "Fix the gate"}],
    "groups": ["General", "Home", "Work"],
    "zones": ["Home", "Acme"],
    "feedback": {"call": ["- accepted: “Call the plumber” (From the call with Sam)",
                          "- dismissed: “Maybe buy a car” (From the call with Noa)"],
                 "notification": ["- accepted, rewritten: “Pay bill” → “Pay the electricity bill” (From a Bank notification)"]},
}
FX_CALL = {"id": "call-fx", "contact": "אמא", "phone_number": "0501234567", "direction": "incoming",
           "when": datetime.datetime(2026, 11, 8, 11, 41, 29), "duration_seconds": 95}
FX_TRANSCRIPT = "אמא: המזגן קופץ. רובין: אני אחפש טכנאי מחר."


def fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def test_prompt_fixtures() -> None:
    """The first admin's prompts as the code before this phase wrote them (tests/fixtures/calls/)."""
    got = {
        "analysis_prompt.txt": analysis.build_analysis_prompt(ADMIN, FX_TRANSCRIPT, FX_CALL, CTX, "focus on money"),
        "analysis_prompt_empty.txt": analysis.build_analysis_prompt(
            ADMIN, FX_TRANSCRIPT, {"contact": "Sam", "direction": "outgoing", "call_timestamp": "2026-11-08T09:00:00"}, {}),
        "analysis_prompt_missed.txt": analysis.build_analysis_prompt(ADMIN, FX_TRANSCRIPT,
                                                                     {"contact": "Sam", "direction": "missed"}, {}),
        "feedback_notification.txt": call_context.feedback_prompt(ADMIN, CTX, "notification"),
    }
    with get_db_connection() as conn:
        conn.execute("INSERT INTO phone_calls (id, user_id, filename, caller, receiver, call_timestamp, transcript, "
                     "summary, title, labels, analysis, direction, status) VALUES ('call-fx', ?, 'call-fx_a.m4a', 'אמא', "
                     "'Me', '2026-11-08T11:41:29', ?, '- המזגן קופץ\n- רובין יחפש טכנאי', 'תיאום טכנאי', "
                     "'[\"home\", \"partner\"]', ?, 'incoming', 'ready')",
                     (ADMIN.id, FX_TRANSCRIPT,
                      json.dumps({"key_facts": [{"kind": "money", "text": "ביקור טכנאי 250"}]}, ensure_ascii=False)))
        conn.execute("INSERT INTO notifications (id, user_id, package, app_name, title, text, sender, conversation, "
                     "is_group, posted_at, content_hash, category, priority) VALUES ('notif-fx', ?, 'com.whatsapp', "
                     "'WhatsApp', 'Gal', 'can you buy milk', 'Gal', 'Family', 1, '2026-11-08T10:00:00', 'h-fx', "
                     "'personal_message', 'high')", (ADMIN.id,))
        conn.execute("INSERT INTO call_suggestions (id, user_id, call_id, source_type, kind, payload, status) VALUES "
                     "('sug-call-fx', ?, 'call-fx', 'call', 'task', ?, 'pending')",
                     (ADMIN.id, json.dumps({"title": "למצוא טכנאי", "due_date": "2026-11-09", "priority": "high",
                                            "group": "Home", "triggers": {
                                                "time": "2026-11-09 09:00",
                                                "location": {"location": "home", "event": "arrive"},
                                                "car": "enter", "wake": True}}, ensure_ascii=False)))
        conn.execute("INSERT INTO call_suggestions (id, user_id, call_id, source_type, kind, payload, status) VALUES "
                     "('sug-ev-fx', ?, 'call-fx', 'call', 'event', ?, 'pending')",
                     (ADMIN.id, json.dumps({"title": "Vet", "start": "2026-11-12T16:15:00", "end": "2026-11-12T17:15:00",
                                            "location": "Clinic"})))
        conn.execute("INSERT INTO call_suggestions (id, user_id, call_id, source_type, kind, payload, status, result_ref) "
                     "VALUES ('sug-notif-fx', ?, 'notif-fx', 'notification', 'task', ?, 'accepted', 'task:x')",
                     (ADMIN.id, json.dumps({"title": "Buy milk", "priority": "medium"})))
        conn.commit()
    task_id = tasks_service.create_task(ADMIN, title="Call the technician", description="from the call",
                                        priority="medium", due_date="2026-11-10", triggers="{}", group_id=None,
                                        task_links=[("call", "call-fx", "origin")])
    with users.acting_as(ADMIN):
        for kind, ref, name in (("suggestion", "sug-call-fx", "context_suggestion_call.txt"),
                                ("suggestion", "sug-notif-fx", "context_suggestion_notification.txt"),
                                ("call", "call-fx", "context_call.txt"),
                                ("notification", "notif-fx", "context_notification.txt"),
                                ("task", task_id, "context_task.txt")):
            got[name] = (context_chats.context_prompt(ADMIN, {"kind": kind, "ref_id": ref}) or "").replace(task_id,
                                                                                                        "{TASK_ID}")
    for name in sorted(os.listdir(FIXTURES)):
        want = fixture(name)
        check(f"first admin's prompt unchanged: {name}", got.get(name) == want,
              next((f"first difference at {i}: {got.get(name, '')[i:i + 80]!r} vs {want[i:i + 80]!r}"
                    for i in range(min(len(got.get(name, '')), len(want))) if got[name][i] != want[i]),
                   f"length {len(got.get(name, ''))} vs {len(want)}"))

    # The same prompts written for Bob: nothing of the first admin.
    bob_texts = [analysis.build_analysis_prompt(BOB, TRANSCRIPTS["bob"], {"contact": "Gilad Bob", "direction": "missed"},
                                                {"zones": ["Home"]}),
                 call_context.feedback_prompt(BOB, {"feedback": {"notification": ["- accepted: “x”"]}}, "notification")]
    leaks = [(w, i) for i, t in enumerate(bob_texts)
             for w in ("Robin", "רובין", "Gal (", "Gal'", "Pepper", " he ", " him", testenv.OWNER_CONTEXT[:30]) if w in t]
    check("Bob's analysis / feedback prompts: his name, nothing of the first admin", not leaks
          and all("Bob" in t for t in bob_texts), leaks)


async def main() -> None:
    client = TestClient(TestServer(build_app()))
    await client.start_server()
    try:
        await test_old_recording(client)
        await test_upload_and_processing(client)
        await test_calls_api(client)
        await test_suggestions(client)
        await test_context_chats(client)
        await test_reanalyze_delete(client)
        await test_loops(client)
        await test_no_kind(client)
        test_prompt_fixtures()
    finally:
        await client.close()


asyncio.run(main())

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
