"""Tasks per user and sharing (docs/access.md, "Sharing"; docs/tasks-and-triggers.md).

Users on a temporary database: the first admin and a member with ``tasks``,
and a member without it. Through the service, the agent tools (via the
registry, as the model would call them), the trigger engine and the real
aiohttp handlers (tasks, task groups, sharing). Only the model (task titles),
phone push and Telegram (``notify``) are faked.

Covers: per-user default lists (seeded on connect, ids kept for the first
admin), list names unique per user, isolation of lists / tasks / links /
triggers, the time checker per user with the reminder to the creator,
location / car / wake triggers per user, sharing a list read vs edit
(owner-only rename / delete / share, unshare hides, a name clash resolves to
the user's own list, tasks follow the list, a shared-list task's reminder goes
to its creator), ``share_item`` (hidden with one user, refused at execution,
asks for approval, a bad user name lists the names), ``/api/sharing``, 403
``not_connected`` for the user without tasks, the location-trigger message
without Home Assistant, and ``shared_by`` / ``access`` only on shared items.

Run: bin/test test_tasks_users
"""

import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Tuple
from unittest.mock import AsyncMock, patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("tasks_users_test")
os.environ["API_TOKEN"] = "tasks-users-test-env-token"  # a test value: the first admin's token (a headless install)
os.environ.setdefault("GEMINI_API_KEY", "test-key")
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake credentials

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import db, users
from addons.tasks.groups import ensure_default_groups_for_everyone

db.migrate()
users.bootstrap()
ensure_default_groups_for_everyone()

from addons import bootstrap as addons_bootstrap

addons_bootstrap.bootstrap()

from addons.tasks import api as tasks_api
from core import clock, notify as core_notify, sharing_api, zones
from core.addons import sharing
from core.auth import setup_auth
from core.db import get_db_connection
from core.addons import registry, scheduler, store
from addons.phone import push as phone_push
from addons.tasks import groups, links, service, titles, triggers

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: str = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


async def _plain_title(title: str) -> str:
    return title.strip()


async def _general(title: str, available: list) -> str:
    return "General"


titles.sanitize_task_title = _plain_title  # no model call for an emoji
titles.get_fitting_group_ai = _general     # nor for a list

ADMIN = users.first_admin()
assert ADMIN is not None


LOOP = asyncio.new_event_loop()


def run(coro):
    return LOOP.run_until_complete(coro)


def tool(who, name: str, **args) -> Any:
    with users.acting_as(who):
        return run(registry.execute(who, name, args))


def own_names(user) -> List[str]:
    with get_db_connection() as conn:
        return [r[0] for r in conn.execute("SELECT name FROM task_groups WHERE user_id = ? ORDER BY sort_order, name",
                                           (user.id,))]


# --- 1. One user: no sharing tool ---------------------------------------------------------

def test_single_user() -> None:
    names = [s["function"]["name"] for s in registry.tool_schemas(ADMIN)]
    check("one user: share_item is not offered", "share_item" not in names and "add_task" in names)
    try:
        tool(ADMIN, "share_item", item_type="task_list", item="Home", user="x", level="read")
        refused = False
    except registry.ToolNotAvailable:
        refused = True
    check("one user: share_item is refused at execution like an unknown tool", refused)
    check("one user: the first admin's default lists keep their bare ids",
          {"general", "home", "work", "shopping", "health", "personal"} <= {g["id"] for g in groups.list_groups(ADMIN)})


test_single_user()  # before the other users exist


# --- Users -----------------------------------------------------------------------------------

BOB = users.create("Bob Builder")
CARL = users.create("Carl")  # no tasks
ADMIN_TOKEN, _ = users.issue_token(ADMIN.id, "test admin")
BOB_TOKEN, _ = users.issue_token(BOB.id, "test bob")
CARL_TOKEN, _ = users.issue_token(CARL.id, "test carl")


def auth(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- 2. Lists per user -----------------------------------------------------------------------

def test_lists_per_user() -> None:
    check("before connecting tasks: Bob has no lists", own_names(BOB) == [])
    store.connect(BOB, "tasks")
    check("connecting tasks seeds Bob's default lists",
          own_names(BOB) == ["General", "Home", "Work", "Shopping", "Health", "Personal"], str(own_names(BOB)))
    check("Bob's list ids carry his user id; the first admin's are unchanged",
          groups.default_group_id(BOB) == f"{BOB.id}-general" and groups.default_group_id(ADMIN) == "general")
    check("each user sees only their own lists",
          {g["id"] for g in groups.list_groups(BOB)}.isdisjoint({g["id"] for g in groups.list_groups(ADMIN)}))
    check("Carl (no tasks) has no lists and sees none", own_names(CARL) == [] and groups.list_groups(CARL) == [])
    gid = groups.create_group(BOB, "Fitness")
    check("a name another user has is fine", groups.create_group(ADMIN, "Fitness") == "fitness" and gid == f"{BOB.id}-fitness")
    try:
        groups.create_group(BOB, "Fitness")
        dup = False
    except Exception:
        dup = True
    check("a name the same user has is refused", dup)
    # The first admin keeps "a deleted default list comes back at restart".
    groups.delete_group(ADMIN, "health")
    ensure_default_groups_for_everyone()
    check("a deleted default list comes back at the next start (first admin and Bob)",
          "health" in {g["id"] for g in groups.list_groups(ADMIN)} and "Health" in own_names(BOB))
    ensure_default_groups_for_everyone()
    check("Carl still has no lists after a start", own_names(CARL) == [])
    # Payloads: no owner columns, no sharing keys on own lists.
    g = groups.list_groups(BOB)[0]
    check("own list payload: the old keys in their order, no shared_by / access / user_id",
          list(g) == [*groups.PUBLIC_COLUMNS, "task_count"], str(list(g)))


# --- 3. Tasks per user ------------------------------------------------------------------------

def test_tasks_per_user() -> None:
    a = service.create_task(ADMIN, title="Admin task")
    b = service.create_task(BOB, title="Bob task", triggers=json.dumps({"wake": True}))
    with get_db_connection() as conn:
        row = dict(conn.execute("SELECT user_id, created_by, group_id FROM tasks WHERE id = ?", (b,)).fetchone())
    check("a task belongs to its list's owner and records its creator",
          row == {"user_id": BOB.id, "created_by": BOB.id, "group_id": f"{BOB.id}-general"}, str(row))
    check("each user sees only their own tasks",
          [t["id"] for t in service.list_tasks(BOB)] == [b] and b not in [t["id"] for t in service.list_tasks(ADMIN)])
    check("another user's task is not found", service.get_task(BOB, a) is None and service.get_task(ADMIN, b) is None)
    try:
        service.delete_task(BOB, a)
        hidden = False
    except LookupError:
        hidden = True
    check("deleting another user's task: not found", hidden and service.get_task(ADMIN, a) is not None)
    try:
        service.create_task(BOB, title="sneak", group_id="home")
        refused = False
    except LookupError:
        refused = True
    check("adding to another user's list: not found", refused)
    out = tool(BOB, "list_tasks")
    check("list_tasks tool: Bob's tasks only", "Bob task" in out and "Admin task" not in out, out)
    t = service.list_tasks(BOB)[0]
    check("own task payload: no owner columns, no shared_by / access",
          not {"user_id", "created_by", "shared_by", "access"} & set(t) and "links" in t, str(list(t)))
    # Links: another user's call link does not show the task.
    links.link_task(a, "call", "call-1")
    check("a task linked to a call is shown only to who can see it",
          [x["id"] for x in service.tasks_for_call(ADMIN, "call-1")] == [a] and service.tasks_for_call(BOB, "call-1") == [])
    check("Carl sees no task at all", service.list_tasks(CARL) == [] and service.get_task(CARL, a) is None)


# --- 4. Triggers per user ---------------------------------------------------------------------

def test_triggers_per_user() -> None:
    past = (clock.now().replace(microsecond=0)).isoformat()
    ta = service.create_task(ADMIN, title="Admin due", triggers=json.dumps({"time": past}))
    tb = service.create_task(BOB, title="Bob due", triggers=json.dumps({"time": past}))
    sent: List[Tuple[str, str]] = []

    async def fake_notify(user, text, **kw):
        sent.append((user.id, text))

    with patch.object(core_notify, "notify", fake_notify), \
            patch.object(phone_push, "send", AsyncMock(return_value=True)):
        run(scheduler.run_once(registry.job("tasks.check_time_triggers")))
    check("time checker: each user's due task fires once, the reminder to its creator",
          sorted(sent) == sorted([(ADMIN.id, "⏰ *Time Reminder*\n\nRemember to: *Admin due*"),
                                  (BOB.id, "⏰ *Time Reminder*\n\nRemember to: *Bob due*")]), str(sent))
    with get_db_connection() as conn:
        keys = {(r[0], r[1]) for r in conn.execute("SELECT user_id, key FROM task_trigger_marks")}
    check("marks are per user", (ADMIN.id, f"last_trigger_v2_time_{ta}") in keys
          and (BOB.id, f"last_trigger_v2_time_{tb}") in keys, str(keys))
    check("the time trigger is removed after firing",
          json.loads(service.get_task(BOB, tb)["triggers"]) == {})
    check("last_fired is per user", tb in triggers.last_fired(BOB) and tb not in triggers.last_fired(ADMIN))

    # Wake, car: only the user's own tasks.
    sent.clear()
    with patch.object(core_notify, "notify", fake_notify), \
            patch.object(phone_push, "send", AsyncMock(return_value=True)):
        n = run(triggers.fire(ADMIN, "wake"))
    check("wake for the first admin does not fire Bob's wake task", n == 0 and not sent, str(sent))
    with patch.object(core_notify, "notify", fake_notify), \
            patch.object(phone_push, "send", AsyncMock(return_value=True)):
        n = run(triggers.fire(BOB, "wake"))
    check("wake for Bob fires his wake task to him", n == 1 and [u for u, _ in sent] == [BOB.id], str(sent))
    service.create_task(BOB, title="Bob car", triggers=json.dumps({"car": "enter"}))

    async def fire_and_drain(user, kind, **kw):
        n = await triggers.fire(user, kind, **kw)
        await asyncio.sleep(0.05)
        return n

    sent.clear()
    with patch.object(core_notify, "notify", fake_notify), \
            patch.object(phone_push, "send", AsyncMock(return_value=True)):
        n_admin = run(fire_and_drain(ADMIN, "car", action="enter"))
        n_bob = run(fire_and_drain(BOB, "car", action="enter"))
    check("car: only the user's own car task fires, to them",
          n_admin == 0 and n_bob == 1 and [u for u, _ in sent] == [BOB.id], str(sent))
    check("transitions are recorded per user", triggers.last_transition("car", BOB) is not None
          and triggers.last_transition("car", CARL) is None)

    # Location triggers name one of the user's own zones (core/zones.py), with or without Home Assistant.
    try:  # like any bad argument, the error reaches the agent loop as the tool's error
        tool(BOB, "add_task", title="Milk", location="home", location_event="arrive")
        out = ""
    except service.UnknownZone as e:
        out = str(e)
    check("a location reminder without any zone: a clear message, no task",
          "Settings → Zones" in out and "time, car or wake-up" in out and "Milk" not in tool(BOB, "list_tasks"), out)
    zones.create(BOB, "Home", 40.1, -74.0, is_home=True)
    zones.create(BOB, "Bakery", 40.2, -74.1)
    zones.create(CARL, "Carl Gym", 40.3, -74.2)
    check("get_zones: the user's own zones, Home marked",
          tool(BOB, "get_zones") == "Your zones:\n- Home (Home)\n- Bakery", tool(BOB, "get_zones"))
    out = tool(BOB, "add_task", title="Bread", location="bakery", location_event="arrive")
    stored = [t for t in service.list_tasks(BOB) if t["title"] == "Bread"]
    check("a location reminder on Bob's zone (no Home Assistant): added, the zone stored",
          stored and json.loads(stored[0]["triggers"])["location"] == {"location": "bakery", "event": "arrive"}, out)
    try:
        service.resolve_zone(BOB, "Carl Gym")
        msg = ""
    except service.UnknownZone as e:
        msg = str(e)
    check("another user's zone is unknown (Bob's zones listed, not Carl's)",
          "Bakery" in msg and "Carl Gym" not in msg.split("Your zones")[1], msg)
    check("'home' is the user's Home zone", service.resolve_zone(BOB, "HOME") == "Home")

    async def fire_location(user: users.User, action: str, target: str) -> int:
        n = await triggers.fire(user, "location", action=action, target=target)
        await asyncio.sleep(0.05)
        return n

    sent.clear()
    with patch.object(core_notify, "notify", fake_notify), \
            patch.object(phone_push, "send", AsyncMock(return_value=True)):
        n_carl = run(fire_location(CARL, "arrive", "Bakery"))
        n_bob = run(fire_location(BOB, "arrive", "Bakery"))
    check("location: arriving at Bob's Dana zone fires his reminder, to him only (no Home Assistant)",
          n_carl == 0 and n_bob == 1 and [u for u, _ in sent] == [BOB.id], str(sent))


# --- 5. Sharing --------------------------------------------------------------------------------

def test_share_tool() -> None:
    names = [s["function"]["name"] for s in registry.tool_schemas(ADMIN)]
    check("two users with tasks: share_item is offered", "share_item" in names)
    check("share_item asks for approval", registry.needs_confirmation("share_item", {"item_type": "task_list"}))
    check("Carl (no shareable addon) is not offered share_item",
          "share_item" not in [s["function"]["name"] for s in registry.tool_schemas(CARL)]
          and not registry.has_tool(CARL, "share_item"))
    out = tool(ADMIN, "share_item", item_type="task_list", item="Home", user="Nobody", level="read")
    check("an unknown user: an error listing the people", out.startswith("Error") and "Bob Builder" in out
          and "Carl" in out, out)
    out = tool(ADMIN, "share_item", item_type="task_list", item="Nope", user="Bob", level="read")
    check("an unknown list: an error", out.startswith("Error") and "no task list called 'Nope'" in out, out)
    out = tool(ADMIN, "share_item", item_type="widget", item="x", user="Bob", level="read")
    check("an item type that can't be shared: an error listing the types", "task_list" in out, out)
    out = tool(ADMIN, "share_item", item_type="task_list", item="Home", user="bob", level="read")
    check("sharing by first name works", "Shared the task list 'Home' with Bob Builder (read-only)" in out, out)
    out = tool(ADMIN, "share_item", item_type="task_list", item="Work", user="Carl", level="edit")
    check("sharing with a user without tasks says when they'll see it", "once the tasks addon" in out, out)
    check("…and Carl sees nothing until then", groups.list_groups(CARL) == [])
    tool(ADMIN, "share_item", item_type="task_list", item="Work", user="Carl", level="none")


def test_sharing_read_edit() -> None:
    home_task = service.create_task(ADMIN, title="Fix the sink", group_id="home")
    # Read: Bob sees the list and its tasks, can't change them.
    shared = {g["id"]: g for g in groups.list_groups(BOB)}.get("home")
    check("read share: the list shows for Bob with shared_by / access",
          shared is not None and shared.get("shared_by") == ADMIN.name and shared.get("access") == "read", str(shared))
    t = service.get_task(BOB, home_task)
    check("read share: its tasks show for Bob", t is not None)
    pub = next(x for x in service.list_tasks(BOB) if x["id"] == home_task)
    check("read share: the task payload has shared_by / access, no owner columns",
          pub.get("shared_by") == ADMIN.name and pub.get("access") == "read" and "user_id" not in pub, str(pub))
    for name, fn in (("complete", lambda: service.complete_task(BOB, home_task)),
                     ("delete", lambda: service.delete_task(BOB, home_task)),
                     ("add", lambda: service.create_task(BOB, title="x", group_id="home"))):
        try:
            fn()
            ok = False
        except service.ReadOnly:
            ok = True
        check(f"read share: {name} is refused", ok)
    out = tool(BOB, "list_tasks")
    check("list_tasks tool marks the shared read-only list", f"shared by {ADMIN.name}, read-only" in out, out)
    # Owner only.
    for name, fn in (("rename", lambda: groups.update_group(BOB, "home", {"name": "Mine"})),
                     ("delete", lambda: groups.delete_group(BOB, "home")),
                     ("share", lambda: sharing.set_level(BOB, "task_list", "home", CARL.id, "read"))):
        try:
            fn()
            ok = False
        except sharing.NotOwner:
            ok = True
        check(f"owner only: {name} by Bob is refused", ok)
    out = tool(BOB, "share_item", item_type="task_list", item="Home", user="Carl", level="read")
    check("share_item by a non-owner: his own Home is shared, never the admin's",
          sharing.access(CARL, "task_list", "home") is None, out)
    tool(BOB, "share_item", item_type="task_list", item="Home", user="Carl", level="none")
    # Name clash: Bob's own "Home" wins.
    found = groups.find_group(BOB, "Home")
    check("a name clash resolves to the user's own list", found is not None and found["id"] == f"{BOB.id}-home")
    out = tool(BOB, "add_task", title="Own home thing", group="Home")
    with get_db_connection() as conn:
        gid = conn.execute("SELECT group_id FROM tasks WHERE title = 'Own home thing'").fetchone()[0]
    check("add_task with a clashing name goes to the user's own list", gid == f"{BOB.id}-home", out)
    # Edit: Bob may add / change tasks in it; they belong to the owner, created by Bob.
    sharing.set_level(ADMIN, "task_list", "home", BOB.id, "edit")
    past = clock.now().replace(microsecond=0).isoformat()
    bt = service.create_task(BOB, title="Bob in admin home", group_id="home", triggers=json.dumps({"time": past}))
    with get_db_connection() as conn:
        row = dict(conn.execute("SELECT user_id, created_by FROM tasks WHERE id = ?", (bt,)).fetchone())
    check("edit share: Bob adds a task; it belongs to the list owner, created by Bob",
          row == {"user_id": ADMIN.id, "created_by": BOB.id}, str(row))
    service.save_task(BOB, home_task, {"priority": "high"})
    check("edit share: Bob changes a task", service.get_task(ADMIN, home_task)["priority"] == "high")
    check("edit share: the shared list is offered when picking a list for Bob",
          "Home" in groups.group_names(BOB) and groups.group_names(BOB).count("Home") == 2)
    sent: List[Tuple[str, str]] = []

    async def fake_notify(user, text, **kw):
        sent.append((user.id, text))

    with patch.object(core_notify, "notify", fake_notify), \
            patch.object(phone_push, "send", AsyncMock(return_value=True)):
        run(scheduler.run_once(registry.job("tasks.check_time_triggers")))
    check("a shared-list task's reminder goes to its creator (Bob), not the owner",
          [u for u, t in sent if "Bob in admin home" in t] == [BOB.id], str(sent))
    # Moving a task out to Bob's own list makes it his.
    service.save_task(BOB, bt, {"group_id": groups.default_group_id(BOB)})
    with get_db_connection() as conn:
        owner = conn.execute("SELECT user_id FROM tasks WHERE id = ?", (bt,)).fetchone()[0]
    check("a task moved to Bob's own list becomes his", owner == BOB.id)
    # Unshare hides the list and its tasks.
    sharing.set_level(ADMIN, "task_list", "home", BOB.id, "none")
    check("unshare: the list and its tasks are hidden from Bob",
          "home" not in {g["id"] for g in groups.list_groups(BOB)} and service.get_task(BOB, home_task) is None)
    check("unshare: Bob's moved task stays his", service.get_task(BOB, bt) is not None)
    # Deleting a shared list: tasks go to the owner's General, the shares end.
    gid = groups.create_group(ADMIN, "Trip")
    sharing.set_level(ADMIN, "task_list", gid, BOB.id, "edit")
    tt = service.create_task(BOB, title="Pack", group_id=gid)
    groups.delete_group(ADMIN, gid)
    check("deleting a shared list: its tasks move to the owner's General, the share ends",
          service.get_task(ADMIN, tt)["group_id"] == "general" and service.get_task(BOB, tt) is None
          and sharing.shares("task_list", gid) == [])


# --- 6. HTTP -----------------------------------------------------------------------------------

def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    r = app.router
    r.add_get("/api/tasks", tasks_api.get_tasks_api)
    r.add_post("/api/tasks", tasks_api.create_task_api)
    r.add_put("/api/tasks/{id}", tasks_api.update_task_api)
    r.add_delete("/api/tasks/{id}", tasks_api.delete_task_api)
    r.add_get("/api/task-groups", tasks_api.get_task_groups_api)
    r.add_post("/api/task-groups", tasks_api.create_task_group_api)
    r.add_put("/api/task-groups/{id}", tasks_api.update_task_group_api)
    r.add_delete("/api/task-groups/{id}", tasks_api.delete_task_group_api)
    sharing_api.setup(app)
    return app


async def test_http(client: TestClient) -> None:
    for method, path in (("GET", "/api/tasks"), ("POST", "/api/tasks"), ("GET", "/api/task-groups"),
                         ("GET", "/api/sharing/task_list/home")):
        resp = await client.request(method, path, headers=auth(CARL_TOKEN), json={"title": "x"})
        body = await resp.json()
        check(f"{method} {path} without tasks: 403 not_connected",
              resp.status == 403 and body.get("error") == "not_connected", f"{resp.status} {body}")
    resp = await client.get("/api/sharing/task_list/nope", headers=auth(ADMIN_TOKEN))
    check("sharing API: unknown list → 404", resp.status == 404)
    resp = await client.get("/api/sharing/widget/home", headers=auth(ADMIN_TOKEN))
    check("sharing API: unknown item type → 404", resp.status == 404)
    resp = await client.put("/api/sharing/task_list/work", headers=auth(ADMIN_TOKEN),
                            json={"user_id": BOB.id, "level": "read"})
    body = await resp.json()
    check("sharing API: PUT shares the list",
          resp.status == 200 and body["shared_with"] == [{"user_id": BOB.id, "name": BOB.name, "level": "read"}]
          and body["owner"] == {"id": ADMIN.id, "name": ADMIN.name} and body["title"] == "Work", str(body))
    check("sharing API: users lists the others", {u["id"] for u in body["users"]} == {BOB.id, CARL.id})
    resp = await client.get("/api/sharing/task_list/work", headers=auth(BOB_TOKEN))
    body = await resp.json()
    check("sharing API: a list shared with you (not yours) → 403 owner_only",
          resp.status == 403 and body.get("error") == "owner_only", str(body))
    resp = await client.get("/api/sharing/task_list/personal", headers=auth(BOB_TOKEN))
    check("sharing API: another user's unshared list → 404", resp.status == 404)
    resp = await client.put("/api/sharing/task_list/work", headers=auth(ADMIN_TOKEN),
                            json={"user_id": BOB.id, "level": "owner"})
    check("sharing API: a bad level → 400", resp.status == 400)
    resp = await client.put("/api/sharing/task_list/work", headers=auth(ADMIN_TOKEN),
                            json={"user_id": ADMIN.id, "level": "read"})
    check("sharing API: sharing with yourself → 400", resp.status == 400)

    resp = await client.get("/api/task-groups", headers=auth(BOB_TOKEN))
    gs = {g["id"]: g for g in await resp.json()}
    check("GET /api/task-groups: own lists plain, the shared one with shared_by / access",
          gs["work"].get("access") == "read" and "access" not in gs[f"{BOB.id}-general"], str(gs.get("work")))
    resp = await client.put("/api/task-groups/work", headers=auth(BOB_TOKEN), json={"name": "Mine"})
    check("PUT a shared list by a non-owner → 403", resp.status == 403)
    resp = await client.delete("/api/task-groups/work", headers=auth(BOB_TOKEN))
    check("DELETE a shared list by a non-owner → 403", resp.status == 403)
    resp = await client.delete("/api/task-groups/personal", headers=auth(BOB_TOKEN))
    check("DELETE another user's list → 404", resp.status == 404)
    resp = await client.post("/api/tasks", headers=auth(BOB_TOKEN), json={"title": "In work", "group_id": "work"})
    check("POST a task to a read-only shared list → 403", resp.status == 403)
    wt = service.create_task(ADMIN, title="Report", group_id="work")
    resp = await client.put(f"/api/tasks/{wt}", headers=auth(BOB_TOKEN), json={"status": "completed"})
    check("PUT a task of a read-only shared list → 403", resp.status == 403)
    resp = await client.delete(f"/api/tasks/{wt}", headers=auth(BOB_TOKEN))
    check("DELETE a task of a read-only shared list → 403", resp.status == 403)
    resp = await client.post("/api/tasks", headers=auth(BOB_TOKEN),
                             json={"title": "Where", "triggers": {"location": {"location": "Moon", "event": "arrive"}}})
    body = await resp.json()
    check("POST a location reminder on a zone Bob does not have → 400 listing his zones",
          resp.status == 400 and "not one of your zones" in body.get("error", "") and "Bakery" in body["error"], str(body))
    resp = await client.post("/api/tasks", headers=auth(BOB_TOKEN),
                             json={"title": "Where", "triggers": {"location": {"location": "home", "event": "arrive"}}})
    body = await resp.json()
    made = service.get_task(BOB, body.get("id", ""))
    check("POST a location reminder at 'home' (no Home Assistant) → his Home zone",
          resp.status == 200 and made and json.loads(made["triggers"])["location"]["location"] == "Home", str(body))
    resp = await client.get("/api/tasks", headers=auth(ADMIN_TOKEN))
    ts = await resp.json()
    check("GET /api/tasks for the owner: no shared_by / access / owner columns",
          ts and not any({"shared_by", "access", "user_id", "created_by"} & set(t) for t in ts))
    resp = await client.get("/api/tasks", headers=auth(BOB_TOKEN))
    ts = {t["id"]: t for t in await resp.json()}
    check("GET /api/tasks for Bob: the shared task has shared_by / access, his own don't",
          ts[wt].get("shared_by") == ADMIN.name and all("access" not in t for i, t in ts.items() if i != wt),
          str(ts.get(wt)))


async def http_main() -> None:
    async with TestClient(TestServer(build_app())) as client:
        await test_http(client)


test_lists_per_user()
test_tasks_per_user()
test_triggers_per_user()
test_share_tool()
test_sharing_read_edit()
run(http_main())

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
if passed != len(results):
    sys.exit(1)
