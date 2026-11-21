"""The shopping list for one user (docs/shopping.md): every rule of one list.

A temporary database that starts as the 9.0 fixture (its two items must
become the first admin's list), the first admin with ``shopping`` and Carl
without it. Through the real aiohttp handlers (shopping, undo) behind the
real auth middleware, the agent tools (via the registry, as the model calls
them), the service and search. The aisle model is LiteLLM's offline mock
(``mock_response``), so the real usage callback records each call; the
clock of the 12-hour rule is controlled. Sharing, invitations, the push and
the stream between users are in test_shopping_users.py.

Covers: the migrated list (ids, order, the screen payload); typed-text
parsing (Hebrew and English) and the name key (case, spaces, niqqud,
punctuation); adding (added / already / restored, quantity and note rules, a
repeated client id, 30 at most, empty); aisles (the fixed order, one model
call per add for the unknown names, usage row for the acting user, the late
answer moving the item and raising the version, the shared table and the
list's own choice beating it, failure and no-key paths); ticking, history
and suggestions (typing, usuals, forget, a deleted typo never suggested);
editing (rename clash, aisle remembered); remove and clear-bought; the
12-hour rule; undo (every write has one, conflicts, an older undo after a
later change); the event stream (first event, change, keep-alive, cleanup);
the installed widget's two calls; Dana's tools (names resolved without
reading the list, candidates); search; a user without ``shopping``.

Run: bin/test test_shopping
"""

import asyncio
import datetime
import json
import os
import sqlite3
import sys
from typing import Any, Dict, List, Tuple
from unittest.mock import patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
DB_PATH = testenv.isolate("shopping_test")
ROOT = str(testenv.SERVER)
os.environ["API_TOKEN"] = "shopping-test-env-token"  # a test value: with it set, DB tokens are honoured
os.environ["ANTHROPIC_API_KEY"] = "test-key"  # the aisle model's provider key (calls are LiteLLM's offline mock)
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, ROOT)

_conn = sqlite3.connect(DB_PATH)
with open(os.path.join(testenv.FIXTURES, "db_9_0.sql"), encoding="utf-8") as _f:
    _conn.executescript(_f.read())
_conn.close()

import litellm
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import db, users

db.migrate()
users.bootstrap()

from addons import bootstrap as addons_bootstrap

addons_bootstrap.bootstrap()

from core import actions_api, search, undo, usage
from core.auth import setup_auth
from core.db import get_db_connection
from core.addons import registry
from addons.shopping import aisles, api, events, items, lists, names, values

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: str = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


ADMIN = users.first_admin()
assert ADMIN is not None
CARL = users.create("Carl", telegram_ids=[333])  # no shopping
ADMIN_TOKEN, _ = users.issue_token(ADMIN.id, "test admin")
CARL_TOKEN, _ = users.issue_token(CARL.id, "test carl")
ADMIN_LIST = lists.own_list_id(ADMIN)
H = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

# --- The aisle model: LiteLLM's offline mock ----------------------------------------------------
MODEL: Dict[str, Any] = {"calls": [], "fail": False, "answer": {}}
_real_acompletion = litellm.acompletion


async def fake_acompletion(**kwargs):
    prompt = kwargs["messages"][0]["content"]
    current = users.current()
    MODEL["calls"].append({"user": current.id if current else "", "prompt": prompt, "model": kwargs["model"]})
    if MODEL["fail"]:
        kwargs["mock_response"] = Exception("model down")
    else:
        listed = [line[2:] for line in prompt.split("Items:\n", 1)[1].splitlines() if line.startswith("- ")]
        kwargs["mock_response"] = json.dumps({"items": [{"name": n, "aisle": MODEL["answer"].get(n, "other")}
                                                        for n in listed]}, ensure_ascii=False)
    return await _real_acompletion(**kwargs)


litellm.acompletion = fake_acompletion
litellm.suppress_debug_info = True


async def usage_rows(failed: int = 0) -> List[tuple]:
    """The shopping_category usage rows, once LiteLLM's logging callbacks (which run after
    the reply) have written at least one row and `failed` failed ones (up to 5 s)."""
    rows: List[tuple] = []
    for _ in range(50):
        await asyncio.sleep(0.1)
        usage.flush_sync()
        with get_db_connection() as conn:
            rows = [tuple(r) for r in conn.execute("SELECT user_id, purpose, ok FROM usage_events "
                                                   "WHERE purpose = 'shopping_category' ORDER BY id")]
        if rows and sum(1 for r in rows if not r[2]) >= failed:
            break
    return rows


def db_items(list_id: str = None) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM shopping_items WHERE list_id = ? ORDER BY seq",
                                              (list_id or ADMIN_LIST,))]


def by_name(name: str) -> Dict[str, Any]:
    return next(i for i in db_items() if i["name"] == name)


def version() -> int:
    return events.version_of(ADMIN_LIST)


async def tool(name: str, **args) -> str:
    with users.acting_as(ADMIN):
        return str(await registry.execute(ADMIN, name, args))


def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    api.setup(app)
    actions_api.setup(app)
    return app


SCREEN_KEYS = {"list", "categories", "items", "counts", "usuals", "invites", "lists"}
ITEM_KEYS = {"id", "name", "quantity", "note", "category", "checked", "checked_at", "checked_by", "added_by",
             "created_at"}


# --- 1. Names --------------------------------------------------------------------------------

def test_names() -> None:
    table = [
        ("2 חלב", [("חלב", "2")]), ("3x eggs", [("eggs", "3")]), ("3 x eggs", [("eggs", "3")]),
        ("1 kg tomatoes", [("tomatoes", "1 kg")]), ("1kg tomatoes", [("tomatoes", "1 kg")]),
        ("2 ליטר חלב", [("חלב", "2 ליטר")]), ("500 גרם גבינה צהובה", [("גבינה צהובה", "500 גרם")]),
        ("eggs x2", [("eggs", "2")]), ("ביצים ×12", [("ביצים", "12")]), ("milk ×2", [("milk", "2")]),
        ("1% milk", [("1% milk", "")]), ("2 gala apples", [("gala apples", "2")]),
        ("milk, bread\n- butter\n\n", [("milk", ""), ("bread", ""), ("butter", "")]),
        ("1. milk\n2) bread", [("milk", ""), ("bread", "")]), ("חלב, לחם,  ביצים", [("חלב", ""), ("לחם", ""), ("ביצים", "")]),
        ("3 בקבוקים מים", [("מים", "3 בקבוקים")]), ("x2", [("x2", "")]),
    ]
    for text, want in table:
        got = [(i.name, i.quantity) for i in names.parse_text(text)]
        check(f"parse: {text!r}", got == want, str(got))
    keys = [("Milk", "milk"), ("  MILK  ", "milk"), ("milk   3%", "milk 3"), ("חָלָב", "חלב"), ("שׁוֹקוֹ", "שוקו"),
            ("Coca-Cola", "coca cola"), ("coca cola", "coca cola"), ('ק"ג', "קג"), ("tea.", "tea"),
            ("קוטג'", "קוטג")]
    for name, want in keys:
        check(f"key: {name!r} → {want!r}", names.name_key(name) == want, names.name_key(name))


# --- 2. The migrated list and the screen --------------------------------------------------------

async def test_screen(client: TestClient) -> None:
    resp = await client.get("/api/shopping", headers=H)
    body = await resp.json()
    check("screen: 200 with exactly the screen fields", resp.status == 200 and set(body) == SCREEN_KEYS, str(body)[:200])
    check("screen: list info", body["list"] == {"id": ADMIN_LIST, "name": "Shopping list", "access": "owner",
                                                "owner": {"id": ADMIN.id, "name": ADMIN.name},
                                                "people": [{"id": ADMIN.id, "name": ADMIN.name, "access": "owner"}],
                                                "version": 0}, str(body["list"]))
    check("screen: the categories, in aisle order, from the server",
          [c["key"] for c in body["categories"]] == ["produce", "bakery", "dairy", "meat", "pantry", "snacks", "drinks",
                                                     "frozen", "household", "care", "baby", "pets", "other"]
          and body["categories"][0] == {"key": "produce", "name": "Fruit & vegetables"})
    check("screen: the migrated items (to buy, then bought) with their ids",
          [(i["id"], i["name"], i["checked"]) for i in body["items"]] == [("s1", "Milk", False), ("s2", "Bread", True)])
    check("screen: item payloads have exactly the item fields, added_by the owner",
          all(set(i) == ITEM_KEYS for i in body["items"])
          and body["items"][0]["added_by"] == {"id": ADMIN.id, "name": ADMIN.name}
          and body["items"][1]["checked_at"], str(body["items"]))
    check("screen: counts, no usuals, no invitations, one list",
          body["counts"] == {"to_buy": 1, "bought": 1} and body["usuals"] == [] and body["invites"] == []
          and body["lists"] == [{"id": ADMIN_LIST, "name": "Shopping list",
                                 "owner": {"id": ADMIN.id, "name": ADMIN.name}, "access": "owner", "active": True}])
    resp = await client.get("/api/shopping/lists", headers=H)
    check("lists: {active, lists}", await resp.json() == {"active": ADMIN_LIST, "lists": body["lists"]})


# --- 3. Adding -----------------------------------------------------------------------------------

async def test_add(client: TestClient) -> None:
    MODEL["calls"].clear()
    MODEL["answer"] = {"חלב": "dairy", "eggs": "dairy", "tomatoes": "produce"}
    v0 = version()
    resp = await client.post("/api/shopping/items", headers=H, json={"text": "2 חלב\neggs x12, 1 kg tomatoes, milk"})
    body = await resp.json()
    res = body.get("results", [])
    check("add text: 200, results per item with their statuses",
          resp.status == 200 and [(r["name"], r["status"]) for r in res]
          == [("חלב", "added"), ("eggs", "added"), ("tomatoes", "added"), ("Milk", "already")], str(res))
    check("add text: the existing item keeps its id", res[3]["id"] == "s1")
    check("add: the answer carries the screen fields, an undo and the new version",
          SCREEN_KEYS <= set(body) and body["undo"]["label"] == "Added 'חלב', 'eggs' and 'tomatoes' to the shopping list"
          and body["list"]["version"] > v0, str(body.get("undo")))
    check("add: quantities parsed", (by_name("חלב")["quantity"], by_name("eggs")["quantity"],
                                     by_name("tomatoes")["quantity"]) == ("2", "12", "1 kg"))
    check("add: new names wait under 'other' at first", {i["category"] for i in body["items"]
                                                        if i["name"] in ("חלב", "eggs", "tomatoes")} == {"other"})
    await aisles.drain()
    asked = MODEL["calls"][0]["prompt"].split("Items:\n")[1].splitlines() if MODEL["calls"] else []
    check("aisles: one model call for the three new names and the migrated item still under other "
          "(Milk; Bread is bought), as the first admin",
          len(MODEL["calls"]) == 1 and MODEL["calls"][0]["user"] == ADMIN.id
          and asked == ["- חלב", "- eggs", "- tomatoes", "- Milk"], str(MODEL["calls"]))
    check("aisles: the usage row is the first admin's, purpose shopping_category",
          await usage_rows() == [(ADMIN.id, "shopping_category", 1)])
    v1 = version()
    check("aisles: the late answer moved the items and raised the version",
          (by_name("חלב")["category"], by_name("eggs")["category"], by_name("tomatoes")["category"])
          == ("dairy", "dairy", "produce") and v1 > body["list"]["version"])
    resp = await client.get("/api/shopping", headers=H)
    order = [i["name"] for i in (await resp.json())["items"]]
    check("order: to buy by aisle, then by when added; bought last", order == ["tomatoes", "חלב", "eggs", "Milk", "Bread"],
          str(order))

    MODEL["calls"].clear()
    resp = await client.post("/api/shopping/items", headers=H,
                             json={"items": ["Eggs", {"name": "חָלָב", "quantity": "3", "note": "3%"}]})
    body = await resp.json()
    check("already: names compared by key (case, niqqud); nothing added",
          [(r["name"], r["status"]) for r in body["results"]] == [("eggs", "already"), ("חלב", "already")]
          and len(db_items()) == 5)
    check("already: a quantity or note given now replaces the stored one; none given keeps it",
          (by_name("חלב")["quantity"], by_name("חלב")["note"], by_name("eggs")["quantity"]) == ("3", "3%", "12"))
    check("already: no model call for known names", MODEL["calls"] == [])
    resp = await client.post("/api/shopping/items", headers=H, json={"items": ["eggs"]})
    body = await resp.json()
    check("already with nothing new: no undo, the version unchanged",
          body["undo"] is None and body["list"]["version"] == version(), str(body["undo"]))

    v = version()
    resp = await client.post("/api/shopping/items", headers=H, json={"items": [{"name": "bread", "quantity": "2"}]})
    body = await resp.json()
    bread = by_name("Bread")
    check("restored: a bought item comes back to buy with the quantity given now",
          body["results"] == [{"id": "s2", "name": "Bread", "status": "restored"}] and not bread["checked"]
          and bread["checked_at"] is None and bread["quantity"] == "2" and version() > v
          and body["undo"]["label"] == "Put 'Bread' back on the shopping list", str(body["results"]))

    cid = "9f2a7c1e-0b4d-4c55-9e3a-7f1d2b3c4d5e"
    for attempt in (1, 2):
        resp = await client.post("/api/shopping/items", headers=H, json={"items": [{"name": "Butter", "id": cid}]})
        body = await resp.json()
        status = body["results"][0]["status"]
        check(f"client id: attempt {attempt} → {'added' if attempt == 1 else 'already (a repeat adds nothing)'}",
              body["results"][0]["id"] == cid and status == ("added" if attempt == 1 else "already")
              and len([i for i in db_items() if i["name"] == "Butter"]) == 1, str(body["results"]))
    resp = await client.post("/api/shopping/items", headers=H, json={"items": [{"name": "x", "id": "bad id!"}]})
    check("client id: not an id → 400 invalid", resp.status == 400 and (await resp.json())["error"] == "invalid")

    n = len(db_items())
    resp = await client.post("/api/shopping/items", headers=H, json={"text": ",".join(f"item{i}" for i in range(31))})
    body = await resp.json()
    check("31 items → 400 too_many, nothing added", resp.status == 400 and body == {
        "error": "too_many", "max": 30, "message": "At most 30 items at a time."} and len(db_items()) == n, str(body))
    for payload in ({"text": " , \n"}, {"items": []}, {"name": "milk"}, {"items": [3]}):
        resp = await client.post("/api/shopping/items", headers=H, json=payload)
        check(f"add {payload}: 400", resp.status == 400 and len(db_items()) == n)
    resp = await client.post("/api/shopping/items", headers=H, json={"text": ",".join(f"item{i}" for i in range(30))})
    check("30 items: added", resp.status == 200 and len(db_items()) == n + 30)
    await aisles.drain()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM shopping_items WHERE name LIKE 'item%'")
        conn.commit()


# --- 4. Aisles: shared table, the list's choice, failure, no key -------------------------------

async def test_aisles(client: TestClient) -> None:
    MODEL["calls"].clear()
    with get_db_connection() as conn:
        conn.execute("INSERT INTO shopping_aisles (name_key, category, updated_at) VALUES ('shampoo', 'care', 'z')")
        conn.commit()
    await client.post("/api/shopping/items", headers=H, json={"items": ["Shampoo"]})
    await aisles.drain()
    check("aisles: a name in the shared table gets its aisle at once, no model call",
          by_name("Shampoo")["category"] == "care" and MODEL["calls"] == [])
    resp = await client.patch(f"/api/shopping/items/{by_name('Shampoo')['id']}", headers=H, json={"category": "household"})
    check("aisles: a person changes an item's aisle", resp.status == 200 and by_name("Shampoo")["category"] == "household")
    resp = await client.patch(f"/api/shopping/items/{by_name('Shampoo')['id']}", headers=H, json={"category": "garden"})
    check("aisles: an unknown aisle → 400", resp.status == 400 and by_name("Shampoo")["category"] == "household")
    await client.delete(f"/api/shopping/items/{by_name('Shampoo')['id']}", headers=H)
    await client.post("/api/shopping/items", headers=H, json={"items": ["shampoo"]})
    check("aisles: the list remembers its choice, before the shared table",
          by_name("shampoo")["category"] == "household" and MODEL["calls"] == [])

    MODEL["fail"] = True
    await client.post("/api/shopping/items", headers=H, json={"items": ["Hummus"]})
    await aisles.drain()
    MODEL["fail"] = False
    check("aisles: a failed call leaves the item under other; the add itself succeeded",
          by_name("Hummus")["category"] == "other" and len(MODEL["calls"]) >= 1)
    with get_db_connection() as conn:
        stored = conn.execute("SELECT 1 FROM shopping_aisles WHERE name_key = 'hummus'").fetchone()
    check("aisles: …and nothing is stored for it", stored is None)
    rows = await usage_rows(failed=3)
    check("aisles: the failed call's usage rows (one per attempt) are the first admin's, failed",
          rows[-3:] == [(ADMIN.id, "shopping_category", 0)] * 3 and all(r[2] == 1 for r in rows[:-3]), str(rows))
    await client.delete(f"/api/shopping/items/{by_name('Hummus')['id']}", headers=H)
    MODEL["calls"].clear()
    MODEL["answer"]["Hummus"] = "pantry"
    await client.post("/api/shopping/items", headers=H, json={"items": ["Hummus"]})
    await aisles.drain()
    check("aisles: the next add of that name asks again", len(MODEL["calls"]) == 1 and by_name("Hummus")["category"] == "pantry")
    MODEL["calls"].clear()
    with patch.dict(os.environ):
        os.environ.pop("ANTHROPIC_API_KEY")
        await client.post("/api/shopping/items", headers=H, json={"items": ["Batteries"]})
        await aisles.drain()
    check("aisles: without a model key no call; the item stays under other",
          MODEL["calls"] == [] and by_name("Batteries")["category"] == "other")

    # Items waiting under "other" with no known aisle are asked about along with the next add.
    with get_db_connection() as conn:
        conn.execute("INSERT INTO shopping_aisles (name_key, category, updated_at) VALUES ('soap', 'household', 'z')")
        conn.commit()
    MODEL["answer"]["Batteries"] = "household"
    v0 = version()
    resp = await client.post("/api/shopping/items", headers=H, json={"items": ["Soap"]})
    body = await resp.json()
    check("aisles: an add of a known name answers at once (Soap in its aisle, Batteries still under other)",
          resp.status == 200 and by_name("Soap")["category"] == "household" and by_name("Batteries")["category"] == "other")
    await aisles.drain()
    asked = MODEL["calls"][0]["prompt"].split("Items:\n")[1].splitlines() if MODEL["calls"] else []
    check("aisles: …and its one call asks about the waiting item only", len(MODEL["calls"]) == 1 and asked == ["- Batteries"],
          str(MODEL["calls"]))
    check("aisles: the answer moves the waiting item and raises the version",
          by_name("Batteries")["category"] == "household" and version() > body["list"]["version"] > v0)
    MODEL["calls"].clear()
    await client.post("/api/shopping/items", headers=H, json={"items": ["soap"]})
    await aisles.drain()
    check("aisles: nothing waiting, every name known → no call", MODEL["calls"] == [])
    lst = lists.active_list(ADMIN)
    with get_db_connection() as conn:
        seq = conn.execute("SELECT MAX(seq) FROM shopping_items WHERE list_id = ?", (lst.id,)).fetchone()[0]
        for i in range(35):
            conn.execute("INSERT INTO shopping_items (id, list_id, name, name_key, quantity, note, category, added_by, "
                         "created_at, seq) VALUES (?, ?, ?, ?, '', '', 'other', ?, 'z', ?)",
                         (f"wait{i:02d}", lst.id, f"Thing {i:02d}", f"thing {i:02d}", ADMIN.id, seq + 1 + i))
        conn.execute("UPDATE shopping_items SET checked = 1, checked_at = ? WHERE id = 'wait00'",
                     (values.iso(values.utc_now()),))
        conn.execute("INSERT INTO shopping_aisle_choices (list_id, name_key, category) VALUES (?, 'thing 01', 'other')",
                     (lst.id,))
        conn.commit()
    await client.post("/api/shopping/items", headers=H, json={"items": ["Kiwi"]})
    await aisles.drain()
    asked = MODEL["calls"][0]["prompt"].split("Items:\n")[1].splitlines() if MODEL["calls"] else []
    check("aisles: the new name plus at most 30 waiting items, oldest first; not bought ones, not the list's own 'other'",
          len(MODEL["calls"]) == 1 and asked == ["- Kiwi"] + [f"- Thing {i:02d}" for i in range(2, 32)], str(asked))
    with get_db_connection() as conn:
        conn.execute("DELETE FROM shopping_items WHERE id LIKE 'wait%'")
        conn.execute("DELETE FROM shopping_aisle_choices WHERE name_key = 'thing 01'")
        conn.commit()
    for name in ("Soap", "Kiwi"):
        await client.delete(f"/api/shopping/items/{by_name(name)['id']}", headers=H)


# --- 5. Changing, ticking, history ------------------------------------------------------------

async def test_change(client: TestClient) -> None:
    eggs = by_name("eggs")
    v = version()
    resp = await client.patch(f"/api/shopping/items/{eggs['id']}", headers=H, json={"checked": True})
    body = await resp.json()
    eggs2 = by_name("eggs")
    check("tick: checked, when and by whom; the version rises; the undo label",
          resp.status == 200 and eggs2["checked"] == 1 and eggs2["checked_at"] and eggs2["checked_by"] == ADMIN.id
          and version() > v and body["undo"]["label"] == "Bought 'eggs'", str(body.get("undo")))
    bought = [i for i in body["items"] if i["checked"]]
    check("tick: the bought item is under Bought (after every item to buy), checked_by in the payload",
          body["items"][-len(bought):] == bought and bought[0]["name"] == "eggs"
          and bought[0]["checked_by"] == {"id": ADMIN.id, "name": ADMIN.name})
    with get_db_connection() as conn:
        hist = [tuple(r) for r in conn.execute("SELECT name_key, name, category, times FROM shopping_history")]
    check("history: a tick enters the name (aisle, how often)", hist == [("eggs", "eggs", "dairy", 1)], str(hist))
    resp = await client.patch(f"/api/shopping/items/{eggs['id']}", headers=H, json={"checked": False})
    with get_db_connection() as conn:
        hist = conn.execute("SELECT COUNT(*) FROM shopping_history").fetchone()[0]
    check("untick: back to buy, the purchase taken back from the history",
          resp.status == 200 and by_name("eggs")["checked"] == 0 and by_name("eggs")["checked_by"] is None and hist == 0)
    await client.patch(f"/api/shopping/items/{eggs['id']}", headers=H, json={"checked": True})

    resp = await client.patch(f"/api/shopping/items/{by_name('tomatoes')['id']}", headers=H, json={"name": "Milk"})
    check("rename: to a name already to buy → 409 duplicate", resp.status == 409
          and (await resp.json())["error"] == "duplicate" and by_name("tomatoes"))
    resp = await client.patch(f"/api/shopping/items/{by_name('tomatoes')['id']}", headers=H,
                              json={"name": "Cherry tomatoes", "quantity": "", "note": "ripe"})
    t = by_name("Cherry tomatoes")
    check("edit: name, quantity cleared, note; aisle kept", resp.status == 200 and t["quantity"] == "" and t["note"] == "ripe"
          and t["category"] == "produce" and t["name_key"] == "cherry tomatoes")
    for method, body in (("PATCH", {"checked": True}), ("DELETE", None)):
        resp = await client.request(method, "/api/shopping/items/no-such-item", headers=H, json=body)
        check(f"{method} an unknown item → 404 not_found", resp.status == 404 and (await resp.json())["error"] == "not_found")
    resp = await client.patch(f"/api/shopping/items/{t['id']}", headers=H, json={"name": "   "})
    check("edit: an empty name → 400", resp.status == 400)
    resp = await client.patch(f"/api/shopping/items/{t['id']}", headers=H, json={})
    check("edit: nothing to change → 400", resp.status == 400)

    # Suggestions: what was bought.
    for name in ("Milk", "Bread"):
        await client.patch(f"/api/shopping/items/{by_name(name)['id']}", headers=H, json={"checked": True})
    await client.post("/api/shopping/items", headers=H, json={"items": ["Bread"]})  # restored: to buy again
    await client.patch(f"/api/shopping/items/{by_name('Bread')['id']}", headers=H, json={"checked": True})
    resp = await client.post("/api/shopping/items", headers=H, json={"items": ["Mlik"]})  # a typo, deleted
    await client.delete(f"/api/shopping/items/{by_name('Mlik')['id']}", headers=H)
    resp = await client.get("/api/shopping/suggestions?q=", headers=H)
    usuals = [s["name"] for s in (await resp.json())["suggestions"]]
    check("usuals: a name still on the list as bought is not offered", usuals == [], str(usuals))
    resp = await client.get("/api/shopping/suggestions?q=MI", headers=H)
    check("suggestions while typing: matching names (case-insensitive), also one bought and still on the list",
          [s["name"] for s in (await resp.json())["suggestions"]] == ["Milk"])
    # With the bought items off the list (cleared, and put back by the undo at the end).
    cleared = await (await client.post("/api/shopping/clear-bought", headers=H)).json()
    check("usuals: most bought first, then the latest bought (Milk after eggs), never a deleted typo",
          [u["name"] for u in cleared["usuals"]] == ["Bread", "Milk", "eggs"], str(cleared["usuals"]))
    await client.post("/api/shopping/items", headers=H, json={"items": ["milk"]})
    resp = await client.get("/api/shopping", headers=H)
    body = await resp.json()
    check("usuals in the screen: not the names on the list to buy now", [u["name"] for u in body["usuals"]] == ["Bread", "eggs"]
          and set(body["usuals"][0]) == {"name", "category", "times"}, str(body["usuals"]))
    await client.delete(f"/api/shopping/items/{by_name('milk')['id']}", headers=H)
    resp = await client.request("DELETE", "/api/shopping/suggestions", headers=H, json={"name": "BREAD"})
    body = await resp.json()
    check("forget a suggestion: gone from the usuals, with an undo",
          resp.status == 200 and [u["name"] for u in body["usuals"]] == ["Milk", "eggs"] and body["undo"]["label"])
    resp = await client.post(f"/api/undo/{body['undo']['id']}", headers=H)
    resp = await client.get("/api/shopping/suggestions", headers=H)
    check("forget: its undo brings the name back",
          [s["name"] for s in (await resp.json())["suggestions"]] == ["Bread", "Milk", "eggs"])
    resp = await client.post(f"/api/undo/{cleared['undo']['id']}", headers=H)
    check("the cleared items are back", resp.status == 200 and by_name("Bread")["checked"] and by_name("Milk")["checked"])
    await client.post("/api/shopping/items", headers=H, json={"items": ["milk"]})
    resp = await client.request("DELETE", "/api/shopping/suggestions", headers=H, json={})
    check("forget without a name → 400", resp.status == 400)


# --- 6. Remove, clear bought, the 12-hour rule -------------------------------------------------

async def test_remove_and_clear(client: TestClient) -> None:
    butter = by_name("Butter")
    resp = await client.delete(f"/api/shopping/items/{butter['id']}", headers=H)
    body = await resp.json()
    check("remove: gone, with an undo", resp.status == 200 and "Butter" not in [i["name"] for i in db_items()]
          and body["undo"]["label"] == "Removed 'Butter' from the shopping list")
    resp = await client.post(f"/api/undo/{body['undo']['id']}", headers=H)
    check("remove: its undo puts it back with the same id", resp.status == 200 and by_name("Butter")["id"] == butter["id"])

    bought = [i["name"] for i in db_items() if i["checked"]]
    resp = await client.post("/api/shopping/clear-bought", headers=H)
    body = await resp.json()
    check("clear-bought: every bought item removed at once, with an undo",
          resp.status == 200 and not [i for i in db_items() if i["checked"]] and body["counts"]["bought"] == 0
          and body["undo"]["label"] == f"Cleared {len(bought)} bought items", str(body["undo"]))
    resp = await client.post(f"/api/undo/{body['undo']['id']}", headers=H)
    check("clear-bought: its undo brings them back", sorted(i["name"] for i in db_items() if i["checked"]) == sorted(bought))
    resp = await client.post("/api/shopping/clear-bought", headers=H)
    resp = await client.post("/api/shopping/clear-bought", headers=H)
    check("clear-bought with nothing bought: no undo", (await resp.json())["undo"] is None)

    await client.post("/api/shopping/items", headers=H, json={"items": ["eggs"]})
    await client.patch(f"/api/shopping/items/{by_name('eggs')['id']}", headers=H, json={"checked": True})
    real = values.utc_now
    eggs_at = values.parse_iso(by_name("eggs")["checked_at"])
    with patch.object(values, "utc_now", lambda: eggs_at + datetime.timedelta(hours=11, minutes=59)):
        resp = await client.get("/api/shopping", headers=H)
        check("12 hours: a bought item is still there after 11:59", "eggs" in [i["name"] for i in (await resp.json())["items"]])
        await client.patch(f"/api/shopping/items/{by_name('Milk')['id']}", headers=H, json={"checked": True})
    v = version()
    with patch.object(values, "utc_now", lambda: eggs_at + datetime.timedelta(hours=12, minutes=1)):
        resp = await client.get("/api/shopping/items", headers=H)
        names_now = [i["name"] for i in await resp.json()]
    check("12 hours: gone when the list is next read; the one ticked later stays; the version rose",
          "eggs" not in names_now and "Milk" in names_now and version() > v, str(names_now))
    with patch.object(values, "utc_now", lambda: real() + datetime.timedelta(hours=25)):
        await client.post("/api/shopping/items", headers=H, json={"items": ["Coffee"]})
    check("12 hours: also when the list is next changed", "Milk" not in [i["name"] for i in db_items()])


# --- 7. Undo -----------------------------------------------------------------------------------

async def test_undo(client: TestClient) -> None:
    resp = await client.post("/api/shopping/items", headers=H, json={"items": ["Olives"]})
    add_undo = (await resp.json())["undo"]
    olives = by_name("Olives")
    resp = await client.patch(f"/api/shopping/items/{olives['id']}", headers=H, json={"note": "green"})
    note_undo = (await resp.json())["undo"]
    resp = await client.post(f"/api/undo/{add_undo['id']}", headers=H)
    body = await resp.json()
    check("undo: an older undo after a later change of the same item → 409, the item kept",
          resp.status == 409 and "changed again since" in body["error"] and by_name("Olives")["note"] == "green", body)
    resp = await client.post(f"/api/undo/{note_undo['id']}", headers=H)
    check("undo: the latest one works", resp.status == 200 and by_name("Olives")["note"] == "")
    resp = await client.post(f"/api/undo/{note_undo['id']}", headers=H)
    check("undo: used once only (404 after)", resp.status == 404)

    resp = await client.post("/api/shopping/items", headers=H, json={"items": ["Rice"]})
    rice_undo = (await resp.json())["undo"]
    resp = await client.patch(f"/api/shopping/items/{by_name('Olives')['id']}", headers=H, json={"checked": True})
    resp = await client.post(f"/api/undo/{rice_undo['id']}", headers=H)
    check("undo: an older undo of another item still works after a later change elsewhere",
          resp.status == 200 and "Rice" not in [i["name"] for i in db_items()] and by_name("Olives")["checked"] == 1)

    resp = await client.post("/api/shopping/items", headers=H, json={"items": ["Salt"]})
    salt_undo = (await resp.json())["undo"]
    await client.delete(f"/api/shopping/items/{by_name('Salt')['id']}", headers=H)
    resp = await client.post(f"/api/undo/{salt_undo['id']}", headers=H)
    check("undo of an add whose item is already gone: done, nothing to remove", resp.status == 200)
    resp = await client.post("/api/shopping/items", headers=H, json={"items": ["Sugar"]})
    resp = await client.delete(f"/api/shopping/items/{by_name('Sugar')['id']}", headers=H)
    del_undo = (await resp.json())["undo"]
    await client.post("/api/shopping/items", headers=H, json={"items": ["sugar"]})
    resp = await client.post(f"/api/undo/{del_undo['id']}", headers=H)
    check("undo of a remove when the name is on the list again → 409, no duplicate",
          resp.status == 409 and len([i for i in db_items() if i["name_key"] == "sugar"]) == 1)
    with get_db_connection() as conn:
        resp = await client.post("/api/shopping/items", headers=H, json={"items": ["Pepper"]})
        body = await resp.json()
    v = version()
    await client.post(f"/api/undo/{body['undo']['id']}", headers=H)
    check("undo: raises the version", version() > v)


# --- 8. The event stream ---------------------------------------------------------------------

async def read_event(resp, timeout: float = 3.0) -> Tuple[str, Any]:
    """The next SSE frame: (event, data) or ('comment', text)."""
    event, data = "", None
    while True:
        line = (await asyncio.wait_for(resp.content.readline(), timeout)).decode().rstrip("\n")
        if line.startswith(":"):
            return "comment", line[1:].strip()
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
        elif line == "" and event:
            return event, data


async def test_stream(client: TestClient) -> None:
    events.PING_EVERY = 0.3
    resp = await client.get("/api/shopping/events", headers=H)
    check("stream: 200 text/event-stream", resp.status == 200 and resp.headers["Content-Type"].startswith("text/event-stream"))
    first = await read_event(resp)
    check("stream: starts with the current version", first == ("changed", {"list_id": ADMIN_LIST, "version": version()}),
          str(first))
    check("stream: one listener on the list", events.listeners(ADMIN_LIST) == 1)
    await client.post("/api/shopping/items", headers=H, json={"items": ["Lemons"]})
    got = await read_event(resp)
    while got[0] == "comment":
        got = await read_event(resp)
    check("stream: a change sends the new version", got == ("changed", {"list_id": ADMIN_LIST, "version": version()}),
          str(got))
    got = await read_event(resp)
    check("stream: a keep-alive comment while nothing changes", got == ("comment", "ping"), str(got))
    resp.close()
    for _ in range(30):
        if events.listeners() == 0:
            break
        await asyncio.sleep(0.1)
    check("stream: a client that goes away is cleaned up", events.listeners() == 0, str(events.listeners()))
    resp = await client.get("/api/shopping/events?list=nope", headers=H)
    check("stream: a list the caller cannot see → 404", resp.status == 404)
    events.PING_EVERY = 25.0


# --- 9. The installed widget's two calls, tools, search ----------------------------------------

async def test_widget(client: TestClient) -> None:
    resp = await client.get("/api/shopping/items", headers=H)
    body = await resp.json()
    check("widget: GET /api/shopping/items is a plain array of {id, name, checked, …}",
          resp.status == 200 and isinstance(body, list) and body and all(
              isinstance(i["id"], str) and isinstance(i["name"], str) and isinstance(i["checked"], bool) for i in body))
    lemons = next(i for i in body if i["name"] == "Lemons")
    resp = await client.patch(f"/api/shopping/items/{lemons['id']}", headers=H, json={"checked": True})
    check("widget: PATCH {checked: true} → 2xx and the item is bought", 200 <= resp.status < 300
          and by_name("Lemons")["checked"] == 1)
    resp = await client.patch(f"/api/shopping/items/{lemons['id']}", headers=H, json={"checked": False})
    check("widget: PATCH {checked: false} → 2xx and back to buy", 200 <= resp.status < 300 and by_name("Lemons")["checked"] == 0)


async def test_tools() -> None:
    names_ = {s["function"]["name"] for s in registry.tool_schemas(ADMIN)}
    check("tools: the shopping tools of the doc are offered (choose_shopping_list only with several users)",
          {"get_shopping_list", "add_shopping_items", "update_shopping_items", "edit_shopping_item"} <= names_)
    schema = next(s for s in registry.tool_schemas(ADMIN) if s["function"]["name"] == "add_shopping_items")
    items_schema = schema["function"]["parameters"]["properties"]["items"]
    check("tools: add_shopping_items takes objects with name / quantity / note, inline (no $ref)",
          items_schema["type"] == "array" and set(items_schema["items"]["properties"]) == {"name", "quantity", "note"}
          and "$ref" not in json.dumps(schema) and items_schema["items"]["required"] == ["name"])
    with undo.collect() as ids:
        out = await tool("add_shopping_items", items=[{"name": "Yogurt", "quantity": "4", "note": "plain"}, "pasta",
                                                       {"name": "lemons"}])
    check("tool add: added with quantity, already-on-list reported", out == "Added Yogurt (4); added pasta; "
          "Lemons was already on the list." and len(ids) == 1 and by_name("Yogurt")["note"] == "plain", out)
    out = await tool("get_shopping_list")
    check("tool get: by aisle with ids, quantity and note", "🛒 Shopping list:" in out
          and f"- Yogurt (4) — plain [id: {by_name('Yogurt')['id']}]" in out and "✅ Bought" in out, out)
    with undo.collect() as ids:
        out = await tool("update_shopping_items", action="bought", items=["yogurt", "PASTA", "unicorn"])
    check("tool update: names resolved without reading the list; an unknown one reported",
          out == "Marked Yogurt, pasta bought. 'unicorn' is not on the list." and by_name("Yogurt")["checked"] == 1
          and len(ids) == 1 and ids[0]["label"] == "Bought 'Yogurt' and 'pasta'", out)
    with users.acting_as(ADMIN):
        await undo.run(ids[0]["id"])
    check("tool update: its undo is the same undo", by_name("Yogurt")["checked"] == 0 and by_name("pasta")["checked"] == 0)
    await tool("add_shopping_items", items=["green olives"])
    out = await tool("update_shopping_items", action="remove", items=["olive"])
    check("tool update: several matches → candidates, nothing changed",
          out.startswith("'olive' matches several items: ") and "Olives (bought)" in out and "green olives" in out
          and by_name("green olives"), out)
    out = await tool("update_shopping_items", action="to_buy", items=["olive"])
    check("tool update: the state prefers the bought one for 'to_buy'", out == "Marked Olives back on the list." and
          by_name("Olives")["checked"] == 0, out)
    out = await tool("update_shopping_items", action="bought", items=["olives"])
    check("tool update: the exact name wins over a name containing it", out == "Marked Olives bought."
          and by_name("Olives")["checked"] == 1 and by_name("green olives")["checked"] == 0, out)
    out = await tool("update_shopping_items", action="remove", items=[by_name("green olives")["id"]])
    check("tool update: by id", out == "Removed green olives." and not [i for i in db_items() if i["name"] == "green olives"])
    out = await tool("edit_shopping_item", item="yogurt", quantity="6", aisle="dairy")
    check("tool edit: quantity and aisle", out.startswith("Changed: Yogurt (6) — plain") and by_name("Yogurt")["category"] == "dairy")
    out = await tool("edit_shopping_item", item="yogurt", aisle="garden")
    check("tool edit: an unknown aisle is an error", out.startswith("Error:") and by_name("Yogurt")["category"] == "dairy")
    out = await tool("update_shopping_items", action="clear_bought")
    check("tool update: clear_bought", out.startswith("Cleared ") or out == "There were no bought items to clear.", out)
    out = await tool("add_shopping_items", items=[f"x{i}" for i in range(31)])
    check("tool add: more than 30 → error", out == "Error: at most 30 items at a time.")


def test_search() -> None:
    with users.acting_as(ADMIN):
        hits = [h for h in search.search(ADMIN, "yog") if h["kind"] == "shopping"]
    check("search: finds items on the active list", [(h["title"], h["subtitle"]) for h in hits] == [("Yogurt", "On the list")],
          str(hits))
    with users.acting_as(CARL):
        check("search: nothing for a user without shopping", not [h for h in search.search(CARL, "yog") if h["kind"] == "shopping"])


async def test_without_kind(client: TestClient) -> None:
    c = {"Authorization": f"Bearer {CARL_TOKEN}"}
    for method, path in (("GET", "/api/shopping"), ("GET", "/api/shopping/items"), ("POST", "/api/shopping/items"),
                         ("PATCH", "/api/shopping/items/s1"), ("DELETE", "/api/shopping/items/s1"),
                         ("POST", "/api/shopping/clear-bought"), ("GET", "/api/shopping/suggestions"),
                         ("DELETE", "/api/shopping/suggestions"), ("GET", "/api/shopping/events"),
                         ("GET", "/api/shopping/lists"), ("PUT", "/api/shopping/active"),
                         ("POST", f"/api/shopping/invites/{ADMIN_LIST}")):
        resp = await client.request(method, path, headers=c, json={"items": ["x"], "name": "x", "list_id": ADMIN_LIST,
                                                                   "action": "use"})
        body = await resp.json()
        check(f"{method} {path} without shopping: 403 not_connected",
              resp.status == 403 and body == {"error": "not_connected", "addon": "shopping"}, f"{resp.status} {body}")
    check("without shopping: no shopping tools", not {s["function"]["name"] for s in registry.tool_schemas(CARL)}
          & {"get_shopping_list", "add_shopping_items", "update_shopping_items", "edit_shopping_item"})
    try:
        with users.acting_as(CARL):
            await registry.execute(CARL, "get_shopping_list", {})
        refused = False
    except registry.ToolNotAvailable:
        refused = True
    check("without shopping: a call is refused like an unknown tool", refused)


async def main() -> None:
    async with TestClient(TestServer(build_app())) as client:
        await test_screen(client)
        await test_add(client)
        await test_aisles(client)
        await test_change(client)
        await test_remove_and_clear(client)
        await test_undo(client)
        await test_stream(client)
        await test_widget(client)
        await test_tools()
        test_search()
        await test_without_kind(client)
        await aisles.drain()
        await events.drain_pushes()


test_names()
asyncio.run(main())

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
if passed != len(results):
    sys.exit(1)
