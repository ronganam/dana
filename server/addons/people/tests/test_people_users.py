"""People per user (docs/people.md, docs/access.md "Addons").

A temporary database that starts as the 9.0 fixture (its person, number and
the person links of its call / notification must become the first admin's),
then users: the first admin Robin (every official addon), Bob and Eve (``people``
+ ``tasks``), Carl (``tasks``, no ``people``). Through the agent tools (via the
registry, as the model would call them), the real aiohttp handlers
(``/api/people*``, ``/api/tasks/{id}/links``), search, the composers' people
functions and the brief loop (LiteLLM's offline ``mock_response``, so the real
usage callback records each call).

Covers: the migration (owners, ids, links kept); two users with separate
people in both directions (tools, HTTP, search, lookup card, merge,
cadence / drifting) including the same phone number on both sides; another
user's person id answers like an unknown one; 403 ``not_connected`` and tools
not offered without the addon; calls and notifications never linked into the
second user's people, briefs or card while they are unscoped (they are the
first admin's); briefs per user (each as their user, usage rows carrying the
right user, cached per user, a failing user not stopping the next, users
without the addon skipped); person task links resolving only for the person's
owner (also on a shared list); the call pipeline's people context and context
chats per user.

Run: bin/test test_people_users
"""

import asyncio
import datetime
import json
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
DB_PATH = testenv.isolate("people_users_test")
os.environ["DEFAULT_COUNTRY_CODE"] = "972"  # local numbers (0…) in these tests are Israeli
ROOT = str(testenv.SERVER)
os.environ["API_TOKEN"] = "people-users-test-env-token"  # a test value: with it set, DB tokens are honoured
os.environ["ANTHROPIC_API_KEY"] = "test-key"  # a test value: LiteLLM only ever runs its offline mock here
os.environ["OWNER_NAME"] = "Robin"
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, ROOT)

# A 9.0 production database (converted at start): its person and number are the first admin's, Robin.
_conn = sqlite3.connect(DB_PATH)
with open(os.path.join(testenv.FIXTURES, "db_9_0.sql"), encoding="utf-8") as _f:
    _conn.executescript(_f.read())
_conn.execute("UPDATE users SET name = 'Robin'")
_conn.commit()
_conn.close()

import litellm
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import db, users

db.migrate()
users.bootstrap()

from addons import bootstrap as addons_bootstrap

addons_bootstrap.bootstrap()

from core import sessions_api
from addons.tasks import api as tasks_api
from core import briefing, clock, context_chats, search, usage, weekly_review
from core.addons import sharing
from addons.phone.calls import context as call_context
from core.auth import setup_auth
from core.db import get_db_connection
from core.addons import registry, store
from core.addons.store import NotConnected
from addons.people import api as people_api, briefs, cadence, detail, linking, merge
from addons.people import store as people_store
from addons.tasks import groups as task_groups, links as task_links, service as tasks_service
from addons.tasks import titles as task_titles

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: str = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


ADMIN = users.first_admin()
assert ADMIN is not None
LOOP = asyncio.new_event_loop()
NOW = clock.now()
PEOPLE_TOOLS = {"list_people", "get_person", "update_person", "link_task_to_person"}


def run(coro):
    return LOOP.run_until_complete(coro)


def tool(user, name: str, args: Dict[str, Any]) -> str:
    with users.acting_as(user):
        return run(registry.execute(user, name, args))


def iso(days_ago: float = 0) -> str:
    return (NOW - datetime.timedelta(days=days_ago)).isoformat(timespec="seconds")


def add_call(cid: str, caller: str, number: str, days_ago: float, summary: str = "We talked.",
             owner: Optional[users.User] = None) -> None:
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO phone_calls (id, user_id, filename, caller, receiver, call_timestamp, transcript, summary, title, "
            "status, phone_number, direction) VALUES (?, ?, ?, ?, 'Me', ?, 'hello there', ?, ?, 'ready', ?, 'incoming')",
            (cid, (owner or ADMIN).id, f"{cid}.m4a", caller, iso(days_ago), summary, f"Call {cid}", number))
        conn.commit()


def add_note(nid: str, sender: str, text: str, days_ago: float = 0, user_id: str = "") -> None:
    """A notification of ``user_id`` (default: the first admin's)."""
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO notifications (id, user_id, package, app_name, title, text, sender, conversation, is_group, "
            "posted_at, content_hash) VALUES (?, ?, 'com.whatsapp', 'WhatsApp', ?, ?, ?, ?, 0, ?, ?)",
            (nid, user_id or ADMIN.id, sender, text, sender, sender, iso(days_ago), f"h-{nid}"))
        conn.commit()


def person_of(table: str, rid: str) -> str:
    with get_db_connection() as conn:
        return conn.execute(f"SELECT person_id FROM {table} WHERE id = ?", (rid,)).fetchone()["person_id"]


def ids(items) -> set:
    return {p["id"] for p in items}


# --- 1. Migration ----------------------------------------------------------------------------------

def test_migration() -> None:
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute("SELECT id, user_id, name, aliases, pinned FROM people")]
        phones = [dict(r) for r in conn.execute("SELECT user_id, phone, person_id FROM person_phones")]
    check("migration: the fixture's person is the first admin's, id and fields kept",
          rows == [{"id": "pe1", "user_id": ADMIN.id, "name": "Sam Example", "aliases": '["Sammy"]', "pinned": 1}],
          str(rows))
    check("migration: the fixture's number is the first admin's",
          phones == [{"user_id": ADMIN.id, "phone": "15550100001", "person_id": "pe1"}], str(phones))
    check("migration: the call and notification still link to the person",
          person_of("phone_calls", "c1") == "pe1" and person_of("notifications", "n1") == "pe1")
    p = detail.get_person(ADMIN, "pe1", schedule_brief=False)
    check("migration: the first admin sees the person with their call and messages",
          p and p["name"] == "Sam Example" and p["stats"]["calls"] == 1 and p["stats"]["messages"] == 1, str(p and p["stats"]))


test_migration()

BOB = users.create("Bob")
EVE = users.create("Eve")
CARL = users.create("Carl")
for _u in (BOB, EVE):
    store.connect(_u, "people")
    store.connect(_u, "tasks")
store.connect(CARL, "tasks")
task_groups.ensure_default_groups_for_everyone()
TOKENS = {u.id: users.issue_token(u.id, "test")[0] for u in (ADMIN, BOB, EVE, CARL)}


def auth(user) -> Dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[user.id]}"}


async def plain_title(t):
    return t

task_titles.sanitize_task_title = plain_title


# --- 2. Two users, the same number ------------------------------------------------------------------

P: Dict[str, str] = {}


def test_same_number() -> None:
    P["robin_gal"] = people_store.create_person(ADMIN, "Gal Robin", phones=["050-111-2222"], relation="partner")["id"]
    P["bob_gal"] = people_store.create_person(BOB, "Galit Bob", phones=["0501112222"], relation="coworker")["id"]
    P["robin_only"] = people_store.create_person(ADMIN, "Zehava Admin", phones=["0523334444"], birthday="03-14")["id"]
    P["bob_only"] = people_store.create_person(BOB, "Yarden Bob", birthday="05-20")["id"]
    check("same number: both users have a person with it",
          people_store.find_by_phone(ADMIN, "+972501112222") == P["robin_gal"]
          and people_store.find_by_phone(BOB, "+972501112222") == P["bob_gal"])
    check("same number: a third user with people has nobody with it", people_store.find_by_phone(EVE, "0501112222") is None)
    try:
        people_store.create_person(BOB, "Another", phones=["0501112222"])
        dup = False
    except ValueError:
        dup = True
    check("same number: still unique within one user", dup)
    check("same number: the last-9-digits match is per user (unique within Bob's numbers)",
          people_store.find_by_phone(BOB, "501112222") == P["bob_gal"])
    try:
        people_store.update_person(BOB, P["bob_only"], {"add_phone": "0523334444"})
        added = True
    except ValueError:
        added = False
    check("same number: Bob can give his person a number only the first admin had", added
          and people_store.find_by_phone(ADMIN, "0523334444") == P["robin_only"]
          and people_store.find_by_phone(BOB, "0523334444") == P["bob_only"])


test_same_number()


# --- 3. Tools -----------------------------------------------------------------------------------------

def test_tools() -> None:
    admin_list, bob_list = tool(ADMIN, "list_people", {}), tool(BOB, "list_people", {})
    check("tools: list_people shows the caller's own people",
          "Gal Robin" in admin_list and "Zehava" in admin_list and "Galit Bob" not in admin_list
          and "Galit Bob" in bob_list and "Yarden" in bob_list and "Gal Robin" not in bob_list
          and "Sam Example" not in bob_list, f"{admin_list}\n--\n{bob_list}")
    check("tools: list_people for a user with the addon but no people", tool(EVE, "list_people", {}) == "No people found.")
    check("tools: get_person by the same number gives each their own",
          tool(ADMIN, "get_person", {"person": "0501112222"}).startswith("Gal Robin")
          and tool(BOB, "get_person", {"person": "0501112222"}).startswith("Galit Bob"))
    out = tool(BOB, "get_person", {"person": P["robin_only"]})
    check("tools: another user's person id is unknown", out.startswith("No person matching"), out)
    check("tools: another user's person name is unknown", tool(BOB, "get_person", {"person": "Zehava"}).startswith("No person"))
    check("tools: Sam (the first admin's old person) is not Bob's", tool(BOB, "get_person", {"person": "Sam Example"})
          .startswith("No person"))
    out = tool(BOB, "update_person", {"person": "Galit Bob", "relation": "friend"})
    with get_db_connection() as conn:
        rel = {r["id"]: r["relation"] for r in conn.execute("SELECT id, relation FROM people")}
    check("tools: update_person changes only the caller's person", out.startswith("Updated Galit Bob")
          and rel[P["bob_gal"]] == "friend" and rel[P["robin_gal"]] == "partner", out)
    out = tool(BOB, "update_person", {"person": "Zehava Admin", "relation": "aunt"})
    with get_db_connection() as conn:
        z = conn.execute("SELECT user_id, relation FROM people WHERE name = 'Zehava Admin' ORDER BY user_id").fetchall()
    check("tools: update_person with another user's person's name makes Bob his own, theirs untouched",
          out.startswith("Created Zehava Admin") and sorted((r["user_id"], r["relation"]) for r in z)
          == sorted([(ADMIN.id, ""), (BOB.id, "aunt")]), str([tuple(r) for r in z]))
    P["bob_zehava"] = people_store.resolve(BOB, "Zehava Admin")
    check("tools: no people tools offered without the addon",
          not PEOPLE_TOOLS & {t["function"]["name"] for t in registry.tool_schemas(CARL)}
          and PEOPLE_TOOLS <= {t["function"]["name"] for t in registry.tool_schemas(BOB)})
    try:
        tool(CARL, "list_people", {})
        out = ""
    except registry.ToolNotAvailable as e:
        out = str(e)
    check("tools: a people tool from a user without the addon is refused like an unknown one",
          out == "Tool 'list_people' is not registered.", out)


test_tools()


# --- 4. Merge ------------------------------------------------------------------------------------------

def test_merge() -> None:
    dup = people_store.create_person(BOB, "Galit", phones=["0549998888"])["id"]
    bob_pairs = merge.merge_candidates(BOB)
    admin_pairs = merge.merge_candidates(ADMIN)
    bob_ids = {i for p in bob_pairs for i in (p["a"]["id"], p["b"]["id"])}
    admin_ids = {i for p in admin_pairs for i in (p["a"]["id"], p["b"]["id"])}
    check("merge: candidates are pairs of the caller's own people",
          any({p["a"]["id"], p["b"]["id"]} == {P["bob_gal"], dup} for p in bob_pairs)
          and P["robin_gal"] not in bob_ids and not (admin_ids & {P["bob_gal"], dup, P["bob_zehava"]}),
          f"{bob_pairs} / {admin_pairs}")
    for keep, other in ((P["bob_gal"], P["robin_gal"]), (P["robin_gal"], P["bob_gal"])):
        try:
            merge.merge_people(BOB, keep, other)
            refused = False
        except LookupError:
            refused = True
        check(f"merge: Bob can't merge with the first admin's person ({'other' if keep == P['bob_gal'] else 'keep'})",
              refused)
    merged = merge.merge_people(BOB, P["bob_gal"], dup)
    check("merge: within Bob's people it works", "972549998888" in merged["phones"] and "Galit" in merged["aliases"])
    check("merge: the first admin's person is untouched",
          detail.get_person(ADMIN, P["robin_gal"], schedule_brief=False)["phones"] == ["972501112222"])


test_merge()


# --- 5. The first admin's calls and notifications are not linked for others -------------------------

def test_activity_not_linked_for_others() -> None:
    # Calls from the shared number and from Bob's people's names; a message from "Galit Bob".
    add_call("u-1", "Gal Robin", "050-111-2222", 2, summary="About the wedding venue")
    add_call("u-2", "Yarden Bob", None, 1)
    add_call("u-3", "Stranger", "0547770000", 1)
    add_note("un-1", "Galit Bob", "see you tomorrow", 0)
    for i, d in enumerate(range(90, 60, -4)):  # Noa: every ~4 days, then nothing for 2 months
        add_call(f"noa-{i}", "Noa", "0546665555", d)
    people_store.create_person(BOB, "Noa Bob", phones=["0546665555"])
    bob_before = ids(cadence.list_people(BOB, include_hidden=True))
    linking.link_pending(BOB)
    check("activity: linking for Bob touches no call or notification",
          all(person_of("phone_calls", c) is None for c in ("u-1", "u-2", "u-3"))
          and person_of("notifications", "un-1") is None)
    linking.link_pending(ADMIN)
    with get_db_connection() as conn:
        linked = {r["person_id"] for r in conn.execute("SELECT person_id FROM phone_calls UNION "
                                                       "SELECT person_id FROM notifications")}
        bob_people = {r["id"] for r in conn.execute("SELECT id FROM people WHERE user_id = ?", (BOB.id,))}
    check("activity: no call or notification is linked to any of Bob's people", not (linked & bob_people))
    check("activity: calls never create people for Bob",
          ids(cadence.list_people(BOB, include_hidden=True)) == bob_before)
    check("activity: the first admin's call from the shared number is theirs",
          person_of("phone_calls", "u-1") == P["robin_gal"])
    bp = detail.get_person(BOB, P["bob_gal"], schedule_brief=False)
    check("activity: Bob's person with the shared number has no calls, messages or timeline",
          bp["stats"]["calls"] == 0 and bp["stats"]["messages"] == 0 and bp["timeline"] == [] and not bp["brief_stale"],
          str(bp["stats"]))
    lk_bob, lk_robin = detail.lookup(BOB, "0501112222"), detail.lookup(ADMIN, "0501112222")
    check("lookup: the same number answers each user with their own person",
          lk_bob["found"] and lk_bob["card"]["name"] == "Galit Bob" and lk_robin["found"]
          and lk_robin["card"]["name"] == "Gal Robin", f"{lk_bob} / {lk_robin}")
    check("lookup: Bob's card has none of the first admin's calls",
          lk_bob["card"]["last_call"] is None and lk_bob["card"]["headline"] == "First time you're talking"
          and lk_robin["card"]["last_call"]["id"] == "u-1")
    check("lookup: Eve (no person with it) gets nothing", detail.lookup(EVE, "0501112222") == {"found": False})
    check("lookup: Bob's card for a number only the first admin's person has… is his own person's",
          detail.lookup(BOB, "0546665555")["card"]["name"] == "Noa Bob")
    drift_robin = [p["name"] for p in cadence.list_people(ADMIN, sort="drifting")]
    drift_bob = cadence.list_people(BOB, sort="drifting")
    check("cadence: the first admin's Noa is drifting", "Noa" in drift_robin, str(drift_robin))
    check("cadence: Bob's Noa (same number) is not, he has no calls with her", drift_bob == [], str(drift_bob))
    check("cadence: Bob's stale briefs: none (nothing linked)", briefs.stale_briefs(BOB) == [])
    check("call pipeline: after a call, Bob's people are not linked or briefed",
          (briefs.after_call(BOB, "u-2") is None) and person_of("phone_calls", "u-2") is not None
          and person_of("phone_calls", "u-2") not in bob_people)


test_activity_not_linked_for_others()


# --- 6. Search, composers, call pipeline ---------------------------------------------------------------

def test_composers() -> None:
    with users.acting_as(BOB):
        bob_hits = [h for h in search.search(BOB, "Gal", limit=10) if h["kind"] == "person"]
    with users.acting_as(ADMIN):
        robin_hits = [h for h in search.search(ADMIN, "Gal", limit=10) if h["kind"] == "person"]
    with users.acting_as(CARL):
        carl_hits = [h for h in search.search(CARL, "Gal", limit=10) if h["kind"] == "person"]
    check("search: each user finds only their own people", {h["id"] for h in bob_hits} == {P["bob_gal"]}
          and {h["id"] for h in robin_hits} == {P["robin_gal"]} and carl_hits == [], f"{bob_hits} / {robin_hits}")
    with users.acting_as(BOB):
        phone_hits = [h["id"] for h in search.search(BOB, "0501112222", limit=10) if h["kind"] == "person"]
    check("search: by the shared number, Bob finds his own", phone_hits == [P["bob_gal"]], str(phone_hits))
    check("mentions: names in an event title resolve among the caller's people",
          people_store.mentioned_in(BOB, "Lunch with Galit Bob and Gal Robin") == [P["bob_gal"]]
          and people_store.mentioned_in(ADMIN, "Lunch with Galit Bob and Gal Robin") == [P["robin_gal"]]
          and people_store.mentioned_in(CARL, "Lunch with Gal Robin") == [])
    with users.acting_as(BOB):
        bob_people = briefing._people(BOB)
        bob_review = [i for i in weekly_review.candidates(BOB)["items"] if i["type"] == "person"]
    with users.acting_as(ADMIN):
        robin_review = [i["title"] for i in weekly_review.candidates(ADMIN)["items"] if i["type"] == "person"]
    with users.acting_as(CARL):
        carl_people = briefing._people(CARL)
    check("briefing: people part from the caller's people only (birthdays), none without the addon",
          all(b["id"] in (P["bob_only"], P["bob_gal"], P["bob_zehava"]) for b in bob_people["birthdays"])
          and carl_people == {"birthdays": [], "reach_out": []}, f"{bob_people} / {carl_people}")
    check("weekly review: drifting people from the caller's people only",
          bob_review == [] and "Noa" in robin_review, f"{bob_review} / {robin_review}")
    check("call pipeline: known people (old memories) only for the first admin with people",
          call_context.known_people(BOB) == [] and call_context.known_people(CARL) == [])
    with users.acting_as(BOB):
        try:
            context_chats.build_chat_context(BOB, "person", P["robin_gal"])
            hidden = False
        except LookupError:
            hidden = True
        ctx = context_chats.build_chat_context(BOB, "person", P["bob_gal"])
        prompt_other = context_chats.context_prompt(BOB, {"kind": "person", "ref_id": P["robin_gal"]})
    check("context chat: another user's person is not found; one's own works",
          hidden and ctx["title"] == "Galit Bob" and prompt_other is None)
    with users.acting_as(CARL):
        try:
            context_chats.build_chat_context(CARL, "person", P["bob_gal"])
            refused = False
        except NotConnected:
            refused = True
    check("context chat: without the addon → not connected", refused)


test_composers()


# --- 7. HTTP ------------------------------------------------------------------------------------------

async def test_http() -> None:
    app = web.Application()
    setup_auth(app)
    people_api.setup(app)
    app.router.add_post("/api/tasks/{id}/links", tasks_api.add_task_link_api)
    app.router.add_post("/api/chat/context", sessions_api.start_context_chat_api)
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/api/people", headers=auth(BOB))
        bob = ids((await r.json())["people"])
        r = await client.get("/api/people", headers=auth(ADMIN))
        robin = ids((await r.json())["people"])
        check("http: GET /api/people lists the caller's own people", P["bob_gal"] in bob and P["robin_gal"] not in bob
              and P["robin_gal"] in robin and "pe1" in robin and not (robin & bob), f"{bob} / {robin}")
        r = await client.get("/api/people", params={"q": "0501112222"}, headers=auth(BOB))
        check("http: search by the shared number lists Bob's own", ids((await r.json())["people"]) == {P["bob_gal"]})
        for method, path, body in (("GET", f"/api/people/{P['robin_gal']}", None),
                                   ("PUT", f"/api/people/{P['robin_gal']}", {"notes": "x"}),
                                   ("DELETE", f"/api/people/{P['robin_gal']}", None),
                                   ("POST", f"/api/people/{P['robin_gal']}/brief", None),
                                   ("GET", f"/api/people/{P['robin_gal']}/card", None),
                                   ("POST", f"/api/people/{P['bob_gal']}/merge", {"other_id": P["robin_gal"]}),
                                   ("POST", f"/api/people/{P['robin_gal']}/merge", {"other_id": P["bob_gal"]})):
            r = await client.request(method, path, json=body, headers=auth(BOB))
            check(f"http: {method} {path.split('/')[-1] if path.count('/') > 3 else ''} on another user's person → 404",
                  r.status == 404, f"{method} {path} {r.status}")
        p = detail.get_person(ADMIN, P["robin_gal"], schedule_brief=False)
        check("http: the first admin's person survived Bob's attempts", p and p["notes"] == "")
        r = await client.get("/api/people/lookup", params={"phone": "+972501112222"}, headers=auth(BOB))
        bl = await r.json()
        r = await client.get("/api/people/lookup", params={"phone": "+972501112222"}, headers=auth(ADMIN))
        rl = await r.json()
        check("http: lookup answers from the caller's people", bl["card"]["name"] == "Galit Bob"
              and rl["card"]["name"] == "Gal Robin" and bl["card"]["last_call"] is None and rl["card"]["last_call"])
        r = await client.get("/api/people/merge-candidates", headers=auth(ADMIN))
        pairs = (await r.json())["pairs"]
        check("http: merge candidates are the caller's own",
              not ({i for p in pairs for i in (p["a"]["id"], p["b"]["id"])} & bob))
        r = await client.post("/api/people", json={"name": "Bob's Plumber", "phones": ["0501112222"]}, headers=auth(EVE))
        check("http: Eve can create a person with a number Bob and the first admin have", r.status == 201)
        for method, path in (("GET", "/api/people"), ("GET", "/api/people/lookup?phone=0501112222"),
                             ("GET", f"/api/people/{P['bob_gal']}"), ("POST", "/api/people"),
                             ("GET", "/api/people/merge-candidates")):
            r = await client.request(method, path, json={"name": "X"}, headers=auth(CARL))
            body = await r.json()
            check(f"http: {method} {path.split('?')[0]} without people → 403 not_connected",
                  r.status == 403 and body == {"error": "not_connected", "addon": "people"}, f"{r.status} {body}")
        # Task links to a person.
        bob_task = tasks_service.create_task(BOB, title="Call Galit about the offsite")
        r = await client.post(f"/api/tasks/{bob_task}/links", json={"ref_type": "person", "ref_id": P["robin_gal"]},
                              headers=auth(BOB))
        check("task links: Bob can't link his task to the first admin's person (404)", r.status == 404
              and not task_links.is_linked(bob_task, "person", P["robin_gal"]))
        r = await client.post(f"/api/tasks/{bob_task}/links", json={"ref_type": "person", "ref_id": P["bob_gal"]},
                              headers=auth(BOB))
        links = (await r.json()).get("links") or []
        check("task links: Bob links his task to his own person",
              r.status == 200 and any(l["ref_type"] == "person" and l["title"] == "Galit Bob" for l in links))
        carl_task = tasks_service.create_task(CARL, title="Something")
        r = await client.post(f"/api/tasks/{carl_task}/links", json={"ref_type": "person", "ref_id": P["bob_gal"]},
                              headers=auth(CARL))
        check("task links: without people → 403 not_connected", r.status == 403)
        r = await client.post("/api/chat/context", json={"kind": "person", "id": P["robin_gal"]}, headers=auth(BOB))
        check("context chat: another user's person → 404", r.status == 404)
        r = await client.post("/api/chat/context", json={"kind": "person", "id": P["bob_gal"]}, headers=auth(CARL))
        check("context chat: without people → 403", r.status == 403)


run(test_http())


def test_task_links_shared_list() -> None:
    shared = task_groups.create_group(ADMIN, "Wedding")
    sharing.set_level(ADMIN, "task_list", shared, BOB.id, "edit")
    tid = tasks_service.create_task(ADMIN, title="Book the venue", group_id=shared)
    out = tool(ADMIN, "link_task_to_person", {"task_id": tid, "person": "Gal Robin"})
    check("task links: the first admin links a shared-list task to their person", out == "Linked.", out)
    robin_links = task_links.links_for_tasks(ADMIN, [tid]).get(tid, [])
    bob_links = task_links.links_for_tasks(BOB, [tid]).get(tid, [])
    check("task links: the person link resolves for its owner only (Bob sees the task, not the link)",
          [l["title"] for l in robin_links if l["ref_type"] == "person"] == ["Gal Robin"]
          and not [l for l in bob_links if l["ref_type"] == "person"], f"{robin_links} / {bob_links}")
    bob_task = next(t for t in tasks_service.list_tasks(BOB) if t["id"] == tid)
    check("task links: Bob's task list payload has no link to the first admin's person",
          not [l for l in bob_task.get("links") or [] if l["ref_type"] == "person"], str(bob_task.get("links")))
    check("task links: the task counts on the owner's person, not on Bob's people",
          any(t["id"] == tid for t in detail.get_person(ADMIN, P["robin_gal"], schedule_brief=False)["tasks"])
          and not any(t["id"] == tid for t in detail.get_person(BOB, P["bob_gal"], schedule_brief=False)["tasks"]))
    out = tool(BOB, "link_task_to_person", {"task_id": tid, "person": "Gal Robin"})
    check("task links: Bob's tool can't find the first admin's person", out.startswith("No person matching"), out)
    out = tool(BOB, "link_task_to_person", {"task_id": tid, "person": P["robin_gal"]})
    check("task links: …not by id either", out.startswith("No person matching"), out)
    check("task links: owns() is per user", people_store.owns(ADMIN, P["robin_gal"])
          and not people_store.owns(BOB, P["robin_gal"]) and not people_store.owns(CARL, P["bob_gal"]))


test_task_links_shared_list()


# --- 8. Briefs per user -----------------------------------------------------------------------------

MODEL_USERS: List[str] = []
BRIEF = {"fail_for": None, "prompts": {}}
_real_acompletion = litellm.acompletion


async def fake_acompletion(**kwargs):
    """LiteLLM's offline mock, so the real usage callback records the call."""
    current = users.current()
    uid = current.id if current else ""
    MODEL_USERS.append(uid)
    BRIEF["prompts"].setdefault(uid, []).append(kwargs["messages"][0]["content"])
    if BRIEF["fail_for"] == uid:
        kwargs["mock_response"] = Exception("model down")
    else:
        kwargs["mock_response"] = json.dumps({"points": [f"Brief for {current.name if current else '?'}"], "open": []})
    return await _real_acompletion(**kwargs)


litellm.acompletion = fake_acompletion
litellm.suppress_debug_info = True


def brief_usage() -> List[Dict[str, Any]]:
    for _ in range(20):  # the usage callback records after the call returns
        run(asyncio.sleep(0.05))
    usage.flush_sync()
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT user_id, ok, ref FROM usage_events WHERE purpose = 'people_brief' ORDER BY id")]


def test_briefs() -> None:
    # Bob has calls of his own too (his "bob-" ids); everything else is the first admin's.
    add_call("bob-1", "Galit Bob", "0501112222", 3, summary="Planning the offsite with Bob", owner=BOB)
    add_call("bob-2", "Galit Bob", "0501112222", 1, summary="The offsite is booked", owner=BOB)
    linking.link_pending(BOB)
    check("briefs: Bob's calls link to Bob's person, not to the first admin's",
          person_of("phone_calls", "bob-1") == P["bob_gal"] and person_of("phone_calls", "bob-2") == P["bob_gal"])
    with get_db_connection() as conn:
        conn.execute("DELETE FROM usage_events")
        conn.commit()
    MODEL_USERS.clear()
    written = run(briefs.run_briefs())
    rows = brief_usage()
    check("briefs: the loop wrote briefs for both users with people, Carl skipped",
          written.get(ADMIN.id, 0) >= 1 and written.get(BOB.id) == 1 and CARL.id not in written
          and written.get(EVE.id) == 0, str(written))
    bob_rows = [r for r in rows if r["ref"] == f"person:{P['bob_gal']}"]
    robin_rows = [r for r in rows if r["ref"] == f"person:{P['robin_gal']}"]
    check("briefs: usage rows carry the person's owner",
          bob_rows and all(r["user_id"] == BOB.id for r in bob_rows) and robin_rows
          and all(r["user_id"] == ADMIN.id for r in robin_rows) and {r["user_id"] for r in rows} == {ADMIN.id, BOB.id},
          str(rows))
    bob_prompt = BRIEF["prompts"][BOB.id][-1]
    check("briefs: Bob's prompt is written for Bob from his calls only",
          "for Bob," in bob_prompt and "offsite" in bob_prompt and "wedding" not in bob_prompt.lower()
          and "About Robin" not in bob_prompt and "Robin's" not in bob_prompt, bob_prompt[:600])
    robin_prompt = next(p for p in BRIEF["prompts"][ADMIN.id] if "Gal Robin" in p.split("\n")[0])
    check("briefs: the first admin's prompt keeps the owner context and has none of Bob's calls",
          "for Robin," in robin_prompt and "About Robin:\n" in robin_prompt and "offsite" not in robin_prompt, robin_prompt[:400])
    bp = detail.get_person(BOB, P["bob_gal"], schedule_brief=False)
    rp = detail.get_person(ADMIN, P["robin_gal"], schedule_brief=False)
    check("briefs: each person carries their owner's brief",
          bp["brief"]["points"] == ["Brief for Bob"] and rp["brief"]["points"] == ["Brief for Robin"])
    check("briefs: cached by signature per user (nothing rewritten on the next pass)",
          run(briefs.run_briefs()) == {ADMIN.id: 0, BOB.id: 0, EVE.id: 0})
    check("briefs: Bob's card shows his own brief and call",
          detail.card(BOB, P["bob_gal"])["points"] == ["Brief for Bob"]
          and detail.card(BOB, P["bob_gal"])["last_call"]["id"] == "bob-2")

    # A failing user does not stop the next: the model fails for the first admin, then their pass breaks.
    add_call("u-9", "Gal Robin", "0501112222", 0, summary="New call")
    add_call("bob-3", "Galit Bob", "0501112222", 0, summary="Another call", owner=BOB)
    BRIEF["fail_for"] = ADMIN.id
    written = run(briefs.run_briefs())
    check("briefs: the model failing for the first admin keeps their old brief, Bob's still rewritten",
          written.get(ADMIN.id) == 0 and written.get(BOB.id) == 1
          and detail.get_person(ADMIN, P["robin_gal"], schedule_brief=False)["brief"]["points"] == ["Brief for Robin"],
          str(written))
    BRIEF["fail_for"] = None
    add_call("bob-4", "Galit Bob", "0501112222", 0, summary="Yet another call", owner=BOB)
    real_link = briefs.linking.link_pending

    def broken_for_admin(user):
        if user.id == ADMIN.id:
            raise RuntimeError("broken")
        return real_link(user)

    briefs.linking.link_pending = broken_for_admin
    try:
        written = run(briefs.run_briefs())
    finally:
        briefs.linking.link_pending = real_link
    check("briefs: a user whose pass breaks is left out, the next still runs",
          ADMIN.id not in written and written.get(BOB.id) == 1, str(written))
    rows = brief_usage()
    check("briefs: failed calls are recorded for their user too",
          any(r["user_id"] == ADMIN.id and not r["ok"] for r in rows)
          and all(r["user_id"] in (ADMIN.id, BOB.id) for r in rows))


test_briefs()


def test_delete_isolated() -> None:
    check("delete: another user's person is not found", people_store.delete_person(BOB, P["robin_gal"]) is False
          and people_store.owns(ADMIN, P["robin_gal"]))
    check("delete: one's own person goes, the other user's with the same number stays",
          people_store.delete_person(BOB, P["bob_gal"])
          and people_store.find_by_phone(BOB, "0501112222") is None
          and people_store.find_by_phone(ADMIN, "0501112222") == P["robin_gal"])


test_delete_isolated()

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
