"""Shopping lists between users (docs/shopping.md "Lists", "Live updates";
docs/access.md "Sharing"): isolation, sharing, invitations, the stream and
the phone push across users.

A temporary database that starts as the 9.0 fixture (the first admin's
list), then Bob (a member with ``shopping`` and ``phone``), Eve (``shopping``,
no phone) and Carl (no ``shopping``). Through the real aiohttp handlers
(shopping, sharing, undo) behind the real auth middleware, the agent tools
(via the registry), search and the sharing helper. FCM is faked at httpx
(like test_phone_users.py); the aisle model is never reached (every name
added here is known).

Covers: separate lists in both directions (HTTP with another user's item id
and ``?list=`` → 404 like an unknown one, tools, search, suggestions, the
stream); a share never switches anyone's active list; invitations (pending,
the phone notification, ``use`` with and without moving items — no
duplicates, bought ones stay, one undo for the whole step — ``dismiss``, a
read-only share: looked at with ``?list=``, writes and ``use`` refused,
unsharing clears the invitation and a new share brings it back); the list's
people and payloads; both users change the same list and each sees the
other's change on their stream (another list's never); unsharing ends a
``?list=`` stream and switches the active-list stream back; undo after
unsharing refused; ``PUT /api/shopping/active`` and ``choose_shopping_list``
(own or ``edit`` only, undo); the push: one per user per window with the
last version (trailing), only people on the list with ``phone``; the web
panel's fixture (tests/fixtures/shopping_web.json: real payloads for
test_web_shopping.mjs, written with ``WRITE_WEB_FIXTURE=1``, else checked
for shape drift).

Run: bin/test test_shopping_users
"""

import asyncio
import json
import os
import sqlite3
import sys
from typing import Any, Dict, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
DB_PATH = testenv.isolate("shopping_users_test")
ROOT = str(testenv.SERVER)
os.environ["API_TOKEN"] = "shopping-users-test-env-token"  # a test value: with it set, DB tokens are honoured
os.environ["FCM_SERVICE_ACCOUNT_JSON"] = '{"project_id": "dana-test"}'
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, ROOT)

_conn = sqlite3.connect(DB_PATH)
with open(os.path.join(testenv.FIXTURES, "db_9_0.sql"), encoding="utf-8") as _f:
    _conn.executescript(_f.read())
# The fixture's bought item was bought "just now" when it was written; keep it that recent (bought items
# older than 12 hours are cleared from the list).
_conn.execute("UPDATE shopping_items SET checked_at = strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', '-1 hour') WHERE checked = 1")
_conn.commit()
_conn.close()

import httpx
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import db, users

db.migrate()
users.bootstrap()

from addons import bootstrap as addons_bootstrap

addons_bootstrap.bootstrap()

from core import actions_api, search, sharing_api, undo
from core.auth import setup_auth
from core.db import get_db_connection
from core.undoable import UndoConflict
from core.addons import registry, sharing, store
from addons.phone import push
from addons.shopping import api, events, lists

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: str = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


# --- FCM at httpx ----------------------------------------------------------------------------
PINGS: List[Dict[str, Any]] = []


class FakeResponse:
    def __init__(self, data: Dict[str, Any], status: int = 200) -> None:
        self._data, self.status_code = data, status

    def json(self) -> Dict[str, Any]:
        return self._data


async def fake_post(self, url, *args, **kwargs):
    if "fcm.googleapis.com" in url:
        msg = (kwargs.get("json") or {})["message"]
        with get_db_connection() as conn:
            ev = conn.execute("SELECT user_id, type, data FROM phone_events WHERE seq = ?",
                              (int(msg["data"]["seq"]),)).fetchone()
        PINGS.append({"token": msg["token"], "type": msg["data"]["type"], "user": ev["user_id"],
                      "data": json.loads(ev["data"])})
        return FakeResponse({"name": "projects/dana-test/messages/1"})
    raise AssertionError(f"unexpected POST {url}")


httpx.AsyncClient.post = fake_post


async def fake_fcm_token():
    return "fcm-test-token"

push._access_token = fake_fcm_token

ADMIN = users.first_admin()
assert ADMIN is not None
BOB = users.create("Bob Builder", telegram_ids=[222])
EVE = users.create("Eve", telegram_ids=[444])
CARL = users.create("Carl", telegram_ids=[333])  # no shopping
for who in (BOB, EVE):
    store.connect(who, "shopping")
store.connect(BOB, "phone")
push.register(ADMIN, "admin-phone", "tok-admin")
push.register(BOB, "bob-phone", "tok-bob")
TOKENS = {u.id: users.issue_token(u.id, f"test {u.name}")[0] for u in (ADMIN, BOB, EVE, CARL)}
A_LIST, B_LIST, E_LIST = (lists.own_list_id(u) for u in (ADMIN, BOB, EVE))


def h(user) -> Dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[user.id]}"}


def names_on(list_id: str, checked: Any = None) -> List[str]:
    with get_db_connection() as conn:
        rows = conn.execute("SELECT name, checked FROM shopping_items WHERE list_id = ? ORDER BY seq",
                            (list_id,)).fetchall()
    return [r["name"] for r in rows if checked is None or bool(r["checked"]) == checked]


def item_id(list_id: str, name: str) -> str:
    with get_db_connection() as conn:
        return conn.execute("SELECT id FROM shopping_items WHERE list_id = ? AND name = ?", (list_id, name)).fetchone()[0]


async def tool(who, name: str, **args) -> str:
    with users.acting_as(who):
        return str(await registry.execute(who, name, args))


def known(*names: str) -> None:
    """Give these names an aisle in the shared table (no model call in this script)."""
    with get_db_connection() as conn:
        for n in names:
            conn.execute("INSERT OR IGNORE INTO shopping_aisles (name_key, category, updated_at) VALUES (?, 'pantry', 'z')",
                         (n.lower(),))
        conn.commit()


known("eggs", "apples", "pears", "coffee", "tea", "milk", "bread", "rice", "chips", "olive oil", "soap", "plums",
      "jam", "salt", "flour", "honey", "nuts")


async def read_event(resp, timeout: float = 3.0) -> Tuple[str, Any]:
    event, data = "", None
    while True:
        line = (await asyncio.wait_for(resp.content.readline(), timeout)).decode().rstrip("\n")
        if line.startswith(":"):
            continue
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
        elif line == "" and event:
            return event, data


async def no_event(resp, wait: float = 0.4) -> bool:
    try:
        await read_event(resp, wait)
        return False
    except asyncio.TimeoutError:
        return True


def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    api.setup(app)
    sharing_api.setup(app)
    actions_api.setup(app)
    return app


# --- 1. Separate lists --------------------------------------------------------------------------

async def test_isolation(client: TestClient) -> None:
    await client.post("/api/shopping/items", headers=h(ADMIN), json={"items": ["eggs"]})
    resp = await client.post("/api/shopping/items", headers=h(BOB), json={"text": "apples, pears"})
    body = await resp.json()
    check("Bob's add goes to his own list", resp.status == 200 and body["list"]["id"] == B_LIST
          and names_on(B_LIST) == ["apples", "pears"] and "apples" not in names_on(A_LIST))
    resp = await client.get("/api/shopping", headers=h(ADMIN))
    check("the first admin's screen has only his items", [i["name"] for i in (await resp.json())["items"]] ==
          ["eggs", "Milk", "Bread"])
    eggs, apples = item_id(A_LIST, "eggs"), item_id(B_LIST, "apples")
    for who, other_item in ((BOB, eggs), (ADMIN, apples)):
        for method, body in (("PATCH", {"checked": True}), ("DELETE", None)):
            r1 = await client.request(method, f"/api/shopping/items/{other_item}", headers=h(who), json=body)
            r2 = await client.request(method, "/api/shopping/items/no-such-id", headers=h(who), json=body)
            b1, b2 = await r1.json(), await r2.json()
            check(f"{who.name.split()[0]} → another user's item ({method}): 404 exactly like an unknown id",
                  r1.status == r2.status == 404 and b1 == b2)
    check("…and both items are unchanged", "eggs" in names_on(A_LIST, False)
          and names_on(B_LIST, False) == ["apples", "pears"], f"{names_on(A_LIST)} {names_on(B_LIST)}")
    for who, other in ((BOB, A_LIST), (ADMIN, B_LIST)):
        for method, path in (("GET", "/api/shopping"), ("GET", "/api/shopping/items"),
                             ("POST", "/api/shopping/items"), ("GET", "/api/shopping/events"),
                             ("GET", "/api/shopping/suggestions")):
            r1 = await client.request(method, f"{path}?list={other}", headers=h(who), json={"items": ["x"]})
            r2 = await client.request(method, f"{path}?list=nope", headers=h(who), json={"items": ["x"]})
            check(f"{who.name.split()[0]}: {method} {path}?list=<another user's> → 404 like an unknown list",
                  r1.status == r2.status == 404 and await r1.json() == await r2.json())
    for who, ref in ((BOB, "eggs"), (ADMIN, "apples")):
        out = await tool(who, "update_shopping_items", action="bought", items=[ref])
        check(f"tools: {who.name.split()[0]} can't tick the other's item by name", f"'{ref}' is not on the list" in out, out)
    check("tools: each reads their own list", "apples" in await tool(BOB, "get_shopping_list")
          and "apples" not in await tool(ADMIN, "get_shopping_list"))
    with users.acting_as(BOB):
        hits = [x["title"] for x in search.search(BOB, "eggs") + search.search(BOB, "apples") if x["kind"] == "shopping"]
    check("search: Bob finds only his own items", hits == ["apples"], str(hits))
    await client.patch(f"/api/shopping/items/{eggs}", headers=h(ADMIN), json={"checked": True})
    resp = await client.get("/api/shopping/suggestions", headers=h(BOB))
    check("suggestions: the first admin's history is not Bob's", (await resp.json())["suggestions"] == [])
    resp = await client.get("/api/shopping/events", headers=h(BOB))
    await read_event(resp)
    await client.post("/api/shopping/items", headers=h(ADMIN), json={"items": ["coffee"]})
    check("stream: the first admin's change never reaches Bob's stream", await no_event(resp))
    resp.close()


# --- 2. A read-only share ---------------------------------------------------------------------

async def test_read_share(client: TestClient) -> None:
    PINGS.clear()
    out = await tool(ADMIN, "share_item", item_type="shopping_list", item="x", user="Bob", level="read")
    check("share_item: read-only", out == f"Shared the shopping list 'Shopping list' with {BOB.name} (read-only).", out)
    await asyncio.sleep(0.2)
    with get_db_connection() as conn:
        notes = [json.loads(r["data"]) for r in conn.execute(
            "SELECT data FROM phone_events WHERE user_id = ? AND type = 'notify'", (BOB.id,))]
    check("invitation: a phone notification for Bob", notes and notes[-1]["text"] ==
          f"{ADMIN.name} shared their shopping list with you" and notes[-1]["tag"] == f"shopping-invite-{A_LIST}", notes)
    resp = await client.get("/api/shopping", headers=h(BOB))
    body = await resp.json()
    check("read share: Bob's active list stays his own", body["list"]["id"] == B_LIST)
    check("read share: an invitation for Bob", body["invites"] == [{
        "list_id": A_LIST, "name": "Shopping list", "owner": {"id": ADMIN.id, "name": ADMIN.name}, "access": "read"}],
          body["invites"])
    check("read share: both lists in Bob's list menu", [(x["id"], x["access"], x["active"]) for x in body["lists"]]
          == [(B_LIST, "owner", True), (A_LIST, "read", False)])
    resp = await client.get(f"/api/shopping?list={A_LIST}", headers=h(BOB))
    body = await resp.json()
    check("read share: Bob looks at it with ?list=", body["list"]["id"] == A_LIST and body["list"]["access"] == "read"
          and "Milk" in [i["name"] for i in body["items"]] and body["list"]["people"] == [
              {"id": ADMIN.id, "name": ADMIN.name, "access": "owner"}, {"id": BOB.id, "name": BOB.name, "access": "read"}],
          str(body["list"]))
    for method, path, payload in (("POST", "/api/shopping/items", {"items": ["sneaky"]}),
                                  ("PATCH", f"/api/shopping/items/{item_id(A_LIST, 'Milk')}", {"checked": True}),
                                  ("DELETE", f"/api/shopping/items/{item_id(A_LIST, 'Milk')}", None),
                                  ("POST", "/api/shopping/clear-bought", None),
                                  ("DELETE", "/api/shopping/suggestions", {"name": "eggs"})):
        resp = await client.request(method, f"{path}?list={A_LIST}", headers=h(BOB), json=payload)
        check(f"read share: {method} {path} → 403 read_only", resp.status == 403
              and (await resp.json())["error"] == "read_only")
    check("…nothing changed", "sneaky" not in names_on(A_LIST) and "Milk" in names_on(A_LIST, False))
    resp = await client.post(f"/api/shopping/invites/{A_LIST}", headers=h(BOB), json={"action": "use"})
    check("read share: 'use' refused (403 read_only), still on his own list", resp.status == 403
          and (await resp.json())["error"] == "read_only" and lists.active_list(BOB).id == B_LIST)
    resp = await client.put("/api/shopping/active", headers=h(BOB), json={"list_id": A_LIST})
    check("read share: PUT active refused (403 read_only)", resp.status == 403)
    resp = await client.post(f"/api/shopping/invites/{A_LIST}", headers=h(BOB), json={"action": "dismiss"})
    body = await resp.json()
    check("dismiss: the invitation is gone, with an undo", resp.status == 200 and body["invites"] == []
          and body["undo"]["label"] == f"Hid {ADMIN.name}'s shopping list invitation")
    await client.post(f"/api/undo/{body['undo']['id']}", headers=h(BOB))
    resp = await client.get("/api/shopping", headers=h(BOB))
    check("dismiss: its undo brings the invitation back", len((await resp.json())["invites"]) == 1)
    await client.post(f"/api/shopping/invites/{A_LIST}", headers=h(BOB), json={"action": "dismiss"})
    sharing.set_level(ADMIN, lists.ITEM_TYPE, A_LIST, BOB.id, "none")
    sharing.set_level(ADMIN, lists.ITEM_TYPE, A_LIST, BOB.id, "read")
    resp = await client.get("/api/shopping", headers=h(BOB))
    check("unsharing forgets the answer: a new share is a new invitation", len((await resp.json())["invites"]) == 1)
    for action in ("use", "dismiss"):
        r1 = await client.post(f"/api/shopping/invites/{E_LIST}", headers=h(BOB), json={"action": action})
        r2 = await client.post("/api/shopping/invites/nope", headers=h(BOB), json={"action": action})
        r3 = await client.post(f"/api/shopping/invites/{B_LIST}", headers=h(BOB), json={"action": action})
        check(f"invite {action}: a list not shared with him (another's, unknown, his own) → 404 alike",
              r1.status == r2.status == r3.status == 404 and await r1.json() == await r2.json())
    resp = await client.post(f"/api/shopping/invites/{A_LIST}", headers=h(BOB), json={"action": "maybe"})
    check("invite: an unknown action → 400", resp.status == 400)


# --- 3. An edit share: use, move items, undo -------------------------------------------------

async def test_edit_share(client: TestClient) -> None:
    sharing.set_level(ADMIN, lists.ITEM_TYPE, A_LIST, BOB.id, "edit")
    check("edit share: still not Bob's active list (a share never switches it)", lists.active_list(BOB).id == B_LIST)
    resp = await client.get("/api/shopping", headers=h(BOB))
    check("edit share: the invitation now says edit", [i["access"] for i in (await resp.json())["invites"]] == ["edit"])
    await client.post("/api/shopping/items", headers=h(BOB), json={"items": ["eggs", {"name": "Tea", "quantity": "2"}]})
    await client.patch(f"/api/shopping/items/{item_id(B_LIST, 'pears')}", headers=h(BOB), json={"checked": True})
    a_before = names_on(A_LIST)
    b_before = names_on(B_LIST)
    eggs_admin = item_id(A_LIST, "eggs")
    resp = await client.post(f"/api/shopping/invites/{A_LIST}", headers=h(BOB), json={"action": "use", "move_items": True})
    body = await resp.json()
    check("use: the shared list is Bob's active list; the answer is its screen",
          resp.status == 200 and body["list"]["id"] == A_LIST and lists.active_list(BOB).id == A_LIST
          and body["invites"] == [], str(body.get("list")))
    check("use + move: his items to buy moved with the add rules (eggs restored, not added twice)",
          sorted(names_on(A_LIST, False)) == sorted(["Milk", "coffee", "apples", "eggs", "Tea"])
          and len([n for n in names_on(A_LIST) if n == "eggs"]) == 1 and item_id(A_LIST, "eggs") == eggs_admin,
          str(names_on(A_LIST)))
    check("use + move: his bought item stays on his own list; nothing else of his", names_on(B_LIST) == ["pears"])
    with get_db_connection() as conn:
        tea = dict(conn.execute("SELECT * FROM shopping_items WHERE list_id = ? AND name = 'Tea'", (A_LIST,)).fetchone())
    check("use + move: a moved item keeps its id, quantity and who added it", tea["quantity"] == "2"
          and tea["added_by"] == BOB.id)
    check("use + move: one undo for the whole step", body["undo"]["label"]
          == f"Switched to {ADMIN.name}'s shopping list and moved 3 items", body["undo"])
    resp = await client.post(f"/api/undo/{body['undo']['id']}", headers=h(BOB))
    check("use + move: the undo puts his items back and his own list active", resp.status == 200
          and names_on(A_LIST) == a_before and names_on(B_LIST) == b_before and lists.active_list(BOB).id == B_LIST,
          f"{names_on(A_LIST)} {names_on(B_LIST)}")
    resp = await client.get("/api/shopping", headers=h(BOB))
    check("…and the invitation is back", len((await resp.json())["invites"]) == 1)
    resp = await client.post(f"/api/shopping/invites/{A_LIST}", headers=h(BOB), json={"action": "use"})
    check("use without moving: active, his own items untouched", resp.status == 200
          and lists.active_list(BOB).id == A_LIST and names_on(B_LIST) == b_before)


# --- 4. Live: both change one list ------------------------------------------------------------

async def test_live(client: TestClient) -> None:
    events.PUSH_EVERY = 0.6
    PINGS.clear()
    a_stream = await client.get("/api/shopping/events", headers=h(ADMIN))
    b_stream = await client.get("/api/shopping/events", headers=h(BOB))
    e_stream = await client.get("/api/shopping/events", headers=h(EVE))
    first_a, first_b, first_e = await read_event(a_stream), await read_event(b_stream), await read_event(e_stream)
    check("stream: Bob's follows his active list (the shared one)", first_b[1]["list_id"] == A_LIST
          and first_a == first_b and first_e[1]["list_id"] == E_LIST)
    resp = await client.post("/api/shopping/items", headers=h(BOB), json={"items": ["jam"]})
    body = await resp.json()
    v = body["list"]["version"]
    check("Bob's add: on the shared list, added_by Bob", "jam" in names_on(A_LIST)
          and next(i for i in body["items"] if i["name"] == "jam")["added_by"] == {"id": BOB.id, "name": BOB.name})
    check("stream: the first admin sees Bob's change", await read_event(a_stream) == ("changed", {"list_id": A_LIST, "version": v}))
    check("stream: Bob's own stream too", await read_event(b_stream) == ("changed", {"list_id": A_LIST, "version": v}))
    check("stream: Eve's list is another: nothing", await no_event(e_stream, 0.2))
    for name in ("salt", "flour", "honey"):
        await client.post("/api/shopping/items", headers=h(ADMIN), json={"items": [name]})
    last = events.version_of(A_LIST)
    await events.drain_pushes()
    shop = [p for p in PINGS if p["type"] == "shopping"]
    per_user = {u: [p["data"]["version"] for p in shop if p["user"] == u] for u in (ADMIN.id, BOB.id)}
    check("push: a burst of four changes → at most two pushes per user (one now, one when the window ends)",
          all(1 <= len(v_) <= 2 for v_ in per_user.values()), str(per_user))
    check("push: the last change is never lost (trailing push has the newest version)",
          all(v_[-1] == last for v_ in per_user.values()) and all(p["data"]["list_id"] == A_LIST for p in shop),
          str(per_user))
    check("push: only to people on the list with phone (not Eve, not Carl), to their own devices",
          {p["user"] for p in shop} == {ADMIN.id, BOB.id}
          and {p["token"] for p in shop if p["user"] == BOB.id} == {"tok-bob"}
          and "tok-admin" in {p["token"] for p in shop if p["user"] == ADMIN.id}, str(shop))
    resp = await client.patch(f"/api/shopping/items/{item_id(A_LIST, 'jam')}", headers=h(ADMIN), json={"checked": True})
    resp = await client.get("/api/shopping/suggestions?q=ja", headers=h(BOB))
    check("history belongs to the list: Bob sees what the first admin bought on it",
          "jam" in [s["name"] for s in (await resp.json())["suggestions"]])
    for s in (a_stream, b_stream, e_stream):
        s.close()
    events.PUSH_EVERY = 10.0


# --- 5. Unsharing --------------------------------------------------------------------------

async def test_unshare(client: TestClient) -> None:
    events.PING_EVERY = 0.3
    fixed = await client.get(f"/api/shopping/events?list={A_LIST}", headers=h(BOB))
    follow = await client.get("/api/shopping/events", headers=h(BOB))
    await read_event(fixed)
    await read_event(follow)
    with undo.collect() as ids:
        await tool(BOB, "add_shopping_items", items=["chips"])
    sharing.set_level(ADMIN, lists.ITEM_TYPE, A_LIST, BOB.id, "none")
    got = await read_event(fixed)
    while got[0] == "changed":
        got = await read_event(fixed)
    check("unshare: the ?list= stream ends with 'end'", got == ("end", {}), str(got))
    seen = [await read_event(follow) for _ in range(2)]
    while seen[-1][0] == "changed":
        seen.append(await read_event(follow))
    check("unshare: the active-list stream switches to his own list and says the lists changed",
          ("changed", {"list_id": B_LIST, "version": events.version_of(B_LIST)}) in seen and ("lists", {}) in seen,
          str(seen))
    follow.close()
    check("unshare: Bob is back on his own list, the choice cleared",
          lists.active_list(BOB).id == B_LIST and not store.get(BOB, "shopping").settings.get("active_list"))
    with users.acting_as(BOB):
        try:
            await undo.run(ids[0]["id"])
            refused = ""
        except UndoConflict as e:
            refused = str(e)
    check("unshare: Bob's undo of his add on that list is refused, the item stays",
          refused == "You can no longer change that shopping list." and "chips" in names_on(A_LIST), refused)
    resp = await client.get(f"/api/shopping?list={A_LIST}", headers=h(BOB))
    check("unshare: the list is unknown to him now (404)", resp.status == 404)
    resp = await client.get("/api/shopping", headers=h(BOB))
    check("unshare: no invitation left", (await resp.json())["invites"] == [])
    events.PING_EVERY = 25.0


# --- 6. Choosing the active list -----------------------------------------------------------

async def test_choose(client: TestClient) -> None:
    names = [s["function"]["name"] for s in registry.tool_schemas(ADMIN)]
    check("several users: share_item and choose_shopping_list are offered", {"share_item", "choose_shopping_list"} <= set(names))
    sharing.set_level(EVE, lists.ITEM_TYPE, E_LIST, BOB.id, "edit")
    sharing.set_level(ADMIN, lists.ITEM_TYPE, A_LIST, BOB.id, "edit")
    check("a later edit share does not take over", lists.active_list(BOB).id == B_LIST)
    resp = await client.put("/api/shopping/active", headers=h(BOB), json={"list_id": E_LIST})
    body = await resp.json()
    check("PUT active: Eve's list, the answer is its screen with an undo", resp.status == 200
          and body["list"]["id"] == E_LIST and body["undo"]["label"] == "Switched to Eve's shopping list")
    await client.post(f"/api/undo/{body['undo']['id']}", headers=h(BOB))
    check("PUT active: its undo puts his own list back", lists.active_list(BOB).id == B_LIST)
    for payload, status in (({"list_id": "nope"}, 404), ({}, 400), ({"list_id": B_LIST}, 200)):
        resp = await client.put("/api/shopping/active", headers=h(BOB), json=payload)
        check(f"PUT active {payload} → {status}", resp.status == status)
    out = await tool(BOB, "choose_shopping_list")
    check("choose_shopping_list: lists his own (active) and the shared ones",
          f"(yours) — active [id: {B_LIST}]" in out and f"(shared by {ADMIN.name}, edit) [id: {A_LIST}]" in out
          and f"(shared by Eve, edit) [id: {E_LIST}]" in out, out)
    with undo.collect() as ids:
        out = await tool(BOB, "choose_shopping_list", list_name="Eve")
    check("choose_shopping_list: by owner name, with an undo", lists.active_list(BOB).id == E_LIST and len(ids) == 1
          and out == "Your shopping changes now go to: Shopping list (shared by Eve).", out)
    out = await tool(BOB, "add_shopping_items", items=["soap"])
    check("tools: Bob's add goes to the chosen list", "soap" in names_on(E_LIST) and out.endswith("(shared by Eve)"), out)
    sharing.set_level(EVE, lists.ITEM_TYPE, E_LIST, BOB.id, "read")
    check("downgraded to read: back on his own list", lists.active_list(BOB).id == B_LIST)
    out = await tool(BOB, "choose_shopping_list", list_name="Eve")
    check("choose_shopping_list: a read-only list can't be chosen", out.startswith("Error:") and "read-only" in out
          and lists.active_list(BOB).id == B_LIST, out)
    resp = await client.get(f"/api/sharing/shopping_list/{A_LIST}", headers=h(ADMIN))
    body = await resp.json()
    check("Share sheet: the owner gets who has access and the people to share with",
          body["shared_with"] == [{"user_id": BOB.id, "name": BOB.name, "level": "edit"}]
          and {u["name"] for u in body["users"]} == {BOB.name, "Eve", "Carl"}, body)
    resp = await client.get(f"/api/sharing/shopping_list/{A_LIST}", headers=h(BOB))
    check("Share sheet: not the owner → 403 owner_only", resp.status == 403)
    resp = await client.get(f"/api/sharing/shopping_list/{A_LIST}", headers=h(EVE))
    check("Share sheet: a list she can't see → 404", resp.status == 404)


# --- 7. The web panel's fixture: real payloads for test_web_shopping.mjs --------------------

WEB_FIXTURE = os.path.join(testenv.FIXTURES, "shopping_web.json")
AISLES = {"חלב": "dairy", "bread": "bakery", "עגבניות שרי": "produce", "bananas": "produce", "dish soap": "household",
          "coffee beans": "pantry", "eggs": "dairy", "hummus": "pantry", "avocado": "produce", "pasta": "pantry",
          "olive oil": "pantry", "toilet paper": "household", "יוגורט": "dairy"}


def shape(value: Any) -> Any:
    """The keys of a payload, recursively (lists by their items' shapes, values dropped)."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [shape(v) for v in value]
    return type(value).__name__ if value is not None else None


async def test_web_fixture(client: TestClient) -> None:
    noa, tom, gal = (users.create(n, telegram_ids=[t]) for n, t in (("Noa Peretz", 555), ("Tom Hadad", 666), ("Gal", 777)))
    for who in (noa, tom, gal):
        store.connect(who, "shopping")
        TOKENS[who.id] = users.issue_token(who.id, f"test {who.name}")[0]
    with get_db_connection() as conn:
        for name, aisle in AISLES.items():
            conn.execute("INSERT OR REPLACE INTO shopping_aisles (name_key, category, updated_at) VALUES (?, ?, 'z')", (name, aisle))
        conn.commit()
    n_list, t_list, g_list = (lists.own_list_id(u) for u in (noa, tom, gal))
    captured: Dict[str, Any] = {}

    async def cap(key: str, method: str, path: str, who: Any, body: Any = None) -> Any:
        resp = await client.request(method, path, headers=h(who), json=body)
        payload = await resp.json()
        if key:
            captured[key] = {"status": resp.status, "body": payload}
        return payload

    def mine(screen: Dict[str, Any], name: str) -> str:
        return next(i["id"] for i in screen["items"] if i["name"] == name)

    # Noa alone: groceries in several aisles, two bought, two usuals, one name no aisle knows.
    await cap("add", "POST", "/api/shopping/items", noa,
              {"text": "2 חלב, Bread, עגבניות שרי, 1 kg bananas, Dish soap, Birthday candles"})
    await cap("", "POST", "/api/shopping/items", noa, {"items": [{"name": "Coffee beans", "note": "dark roast"},
                                                                  {"name": "יוגורט", "quantity": "4", "note": "בלי סוכר"}]})
    screen = await cap("", "POST", "/api/shopping/items", noa, {"text": "Eggs, Hummus"})
    await cap("check", "PATCH", f"/api/shopping/items/{mine(screen, 'Eggs')}", noa, {"checked": True})
    await cap("", "PATCH", f"/api/shopping/items/{mine(screen, 'Hummus')}", noa, {"checked": True})
    screen = await cap("add_mixed", "POST", "/api/shopping/items", noa, {"text": "חלב, Eggs, Avocado"})
    await cap("", "PATCH", f"/api/shopping/items/{mine(screen, 'Eggs')}", noa, {"checked": True})
    screen = await cap("", "POST", "/api/shopping/items", noa, {"text": "Oat milk, Pita"})
    for name in ("Oat milk", "Pita"):  # bought on an earlier trip and gone from the list: the two usuals
        await cap("", "PATCH", f"/api/shopping/items/{mine(screen, name)}", noa, {"checked": True})
        await cap("", "DELETE", f"/api/shopping/items/{mine(screen, name)}", noa)
    screen = await cap("owner_alone", "GET", "/api/shopping", noa)
    await cap("suggestions", "GET", "/api/shopping/suggestions?q=hu", noa)
    avocado = mine(screen, "Avocado")
    await cap("edit", "PATCH", f"/api/shopping/items/{avocado}", noa, {"quantity": "3", "note": "ripe", "category": "produce"})
    await cap("error_duplicate", "PATCH", f"/api/shopping/items/{avocado}", noa, {"name": "bread"})
    removed = await cap("remove", "DELETE", f"/api/shopping/items/{avocado}", noa)
    await cap("undo_ok", "POST", f"/api/undo/{removed['undo']['id']}", noa)
    await cap("undo_gone", "POST", f"/api/undo/{removed['undo']['id']}", noa)
    first = await cap("", "PATCH", f"/api/shopping/items/{avocado}", noa, {"note": "very ripe"})
    await cap("", "PATCH", f"/api/shopping/items/{avocado}", noa, {"note": "for guacamole"})
    await cap("undo_conflict", "POST", f"/api/undo/{first['undo']['id']}", noa)
    await cap("error_too_many", "POST", "/api/shopping/items", noa, {"text": ", ".join(f"thing {i}" for i in range(31))})
    await cap("error_invalid", "POST", "/api/shopping/items", noa, {})
    await cap("error_not_found", "PATCH", "/api/shopping/items/no-such-item", noa, {"checked": True})
    await cap("error_not_connected", "GET", "/api/shopping", CARL)

    # Tom has his own three; Noa shares hers with Tom (edit) and Gal (read).
    await cap("", "POST", "/api/shopping/items", tom, {"text": "Pasta, Olive oil, Toilet paper"})
    await cap("share_put", "PUT", f"/api/sharing/shopping_list/{n_list}", noa, {"user_id": tom.id, "level": "edit"})
    await cap("", "PUT", f"/api/sharing/shopping_list/{n_list}", noa, {"user_id": gal.id, "level": "read"})
    await cap("share_get", "GET", f"/api/sharing/shopping_list/{n_list}", noa)
    await cap("member_invite", "GET", "/api/shopping", tom)
    await cap("lists", "GET", "/api/shopping/lists", tom)
    await cap("invite_use", "POST", f"/api/shopping/invites/{n_list}", tom, {"action": "use", "move_items": True})
    await cap("member_shared", "GET", "/api/shopping", tom)
    await cap("owner_shared", "GET", "/api/shopping", noa)
    await cap("viewer_invite", "GET", "/api/shopping", gal)
    await cap("viewer", "GET", f"/api/shopping?list={n_list}", gal)
    await cap("error_read_only", "POST", f"/api/shopping/items?list={n_list}", gal, {"text": "Chips"})
    await cap("invite_dismiss", "POST", f"/api/shopping/invites/{n_list}", gal, {"action": "dismiss"})
    await cap("active", "PUT", "/api/shopping/active", tom, {"list_id": t_list})
    await cap("clear_bought", "POST", "/api/shopping/clear-bought", noa)
    await cap("forget", "DELETE", "/api/shopping/suggestions", noa, {"name": "Hummus"})

    c = captured
    check("fixture: owner alone — six aisles to buy, two bought, two usuals, one under other",
          c["owner_alone"]["body"]["counts"] == {"to_buy": 9, "bought": 2}
          and {u["name"] for u in c["owner_alone"]["body"]["usuals"]} == {"Oat milk", "Pita"}
          and [i["category"] for i in c["owner_alone"]["body"]["items"] if i["name"] == "Birthday candles"] == ["other"],
          json.dumps(c["owner_alone"]["body"]["counts"]))
    check("fixture: the mixed add says already / restored / added",
          [r["status"] for r in c["add_mixed"]["body"]["results"]] == ["already", "restored", "added"], c["add_mixed"]["body"].get("results"))
    check("fixture: each error captured with its status",
          [c[k]["status"] for k in ("error_duplicate", "error_too_many", "error_invalid", "error_not_found",
                                    "error_not_connected", "error_read_only", "undo_conflict", "undo_gone")]
          == [409, 400, 400, 404, 403, 403, 409, 404], [c[k]["status"] for k in c if k.startswith(("error", "undo"))])
    check("fixture: the member's invitation, the viewer's read-only look, the shared list's people",
          c["member_invite"]["body"]["invites"][0]["access"] == "edit" and c["viewer"]["body"]["list"]["access"] == "read"
          and c["viewer_invite"]["body"]["invites"][0]["access"] == "read"
          and [p["name"] for p in c["owner_shared"]["body"]["list"]["people"]] == ["Noa Peretz", "Tom Hadad", "Gal"])
    text = json.dumps(captured, indent=1, ensure_ascii=False, sort_keys=True)
    if os.getenv("WRITE_WEB_FIXTURE"):
        with open(WEB_FIXTURE, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"wrote {WEB_FIXTURE}")
    else:
        with open(WEB_FIXTURE, encoding="utf-8") as f:
            stored = json.load(f)
        drift = [k for k in captured if shape(captured[k]) != shape(stored.get(k))] + [k for k in stored if k not in captured]
        check("fixture: tests/fixtures/shopping_web.json has the payloads' current shape "
              "(regenerate with WRITE_WEB_FIXTURE=1 bin/test test_shopping_users)", not drift, drift)


async def main() -> None:
    async with TestClient(TestServer(build_app())) as client:
        await test_isolation(client)
        await test_read_share(client)
        await test_edit_share(client)
        await test_live(client)
        await test_unshare(client)
        await test_choose(client)
        await test_web_fixture(client)
        await events.drain_pushes()


asyncio.run(main())

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
if passed != len(results):
    sys.exit(1)
