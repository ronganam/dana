"""Projects and entries per user, and project sharing (docs/access.md, "Sharing").

A temporary database that starts as the 9.0 fixture (its project and two
entries must become the first admin's, ids and FTS index kept), then users:
the first admin, Bob (knowledge + tasks), Eve (knowledge) and Carl (without
knowledge). Through the agent tools (via the registry, as the model would
call them), the real aiohttp handlers (projects, entries, media files,
sharing), the search box, the approval text, the call-analysis context and
the media pipeline (its download faked: nothing external runs).

Covers: the migration (owners, ids, FTS, payloads unchanged for the first
admin, share_item not offered to a single user); isolation of projects,
entries, FTS search (tool, search box) and stored media in both directions;
the user without the addon (tools not offered / refused, 403
``not_connected``); sharing read vs edit (entries follow the project, read
refuses writes, edit adds entries owned by the project's owner and created by
the editor), owner-only rename / delete / share, unsharing hides the project,
its entries, search hits and media, a name clash resolves to the user's own
project, ``shared_by`` / ``access`` only on shared items, undo refused after
unsharing; tasks carrying a project stay governed by their own list (a link
or a project never grants access to a task, nor a task to a project);
deleting a shared project ends its shares and leaves other users' tasks;
media processing runs as the entry's owner.

Run: bin/test test_knowledge_users
"""

import asyncio
import os
import sqlite3
import sys
from typing import Any, Dict, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
DB_PATH = testenv.isolate("knowledge_users_test")
ROOT = str(testenv.SERVER)
os.environ["API_TOKEN"] = "knowledge-users-test-env-token"  # a test value: the first admin's token (a headless install)
os.environ.setdefault("GEMINI_API_KEY", "test-key")
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, ROOT)

# A 9.0 production database (converted at start): its project and entries are the first admin's.
_conn = sqlite3.connect(DB_PATH)
with open(os.path.join(testenv.FIXTURES, "db_9_0.sql"), encoding="utf-8") as _f:
    _conn.executescript(_f.read())
_conn.close()

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import db, users

db.migrate()
users.bootstrap()

from addons import bootstrap as addons_bootstrap

addons_bootstrap.bootstrap()

from addons.tasks.groups import ensure_default_groups_for_everyone

ensure_default_groups_for_everyone()

from addons.knowledge import api as knowledge_api
from core import approval_text, events, search, sharing_api, undo
from core.addons import sharing
from addons.phone.calls import context as call_context
from core.auth import setup_auth
from core.db import get_db_connection
from core.addons import registry, store
from addons.knowledge import entries, media, projects
from addons.tasks import links, titles

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: str = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


ADMIN = users.first_admin()
assert ADMIN is not None
LOOP = asyncio.new_event_loop()

# The media pipeline's download / transcription is faked: it records who it ran as.
MEDIA_RUNS: List[Tuple[str, str]] = []


async def fake_download(url: str, restricted: Any = None) -> Dict[str, Any]:
    current = users.current()
    MEDIA_RUNS.append((url, current.id if current else ""))
    return {"media_path": None, "transcript": "", "summary": f"Summary of {url}", "tags": ["video"],
            "title": "", "author": "someone", "comments": []}


media.download_and_process_media = fake_download


async def _plain_title(title: str) -> str:
    return title.strip()


async def _general(title: str, available: list) -> str:
    return "General"


titles.sanitize_task_title = _plain_title  # no model call for a task title's emoji
titles.get_fitting_group_ai = _general     # nor for its list


def run(coro):
    return LOOP.run_until_complete(coro)


def settle() -> None:
    """Let background media processing and the event handlers started by the last call finish."""
    pending = list(media._tasks)
    if pending:
        run(asyncio.gather(*pending))
    run(events.drain())


def tool(who, tool_name: str, **args) -> Any:
    with users.acting_as(who):
        out = run(registry.execute(who, tool_name, args))
    settle()
    return out


def row(table: str, item_id: str) -> Dict[str, Any]:
    with get_db_connection() as conn:
        r = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone()
    return dict(r) if r else {}


def tool_names(who) -> set:
    return {s["function"]["name"] for s in registry.tool_schemas(who)}


def new_id(out: str) -> str:
    return out.rsplit("`", 2)[-2]


def notes(who, q: str) -> List[Dict[str, Any]]:
    with users.acting_as(who):
        return [r for r in search.search(who, q, limit=20) if r["kind"] == "note"]


PROJECT_KEYS = {"id", "title", "description", "icon", "color", "tags", "status", "created_at", "updated_at",
                "entry_count", "task_count", "target_date", "budget", "section_count", "decisions", "decided",
                "committed", "currency"}
ENTRY_KEYS = {"id", "project_id", "kind", "title", "body", "fields", "status", "priority", "due_at", "tags", "url",
              "media_path", "transcript", "summary", "comments", "source_author", "created_at", "updated_at",
              "section_id", "decision", "files"}
KNOWLEDGE_TOOLS = {"create_project", "list_projects", "list_project_entries", "get_knowledge_item",
                   "save_knowledge_item", "search_knowledge", "update_project", "delete_project",
                   "update_knowledge_item", "delete_knowledge_item", "create_section", "update_section",
                   "delete_section"}


# --- 1. Migration and the first admin ------------------------------------------------------

def test_migration() -> None:
    p, e1, e2 = row("projects", "p1"), row("entries", "e1"), row("entries", "e2")
    check("migration: the existing project is the first admin's, id and title kept",
          p.get("user_id") == ADMIN.id and p.get("title") == "Garden plan")
    check("migration: the existing entries are the first admin's (owner and creator), in their project",
          all(e.get("user_id") == ADMIN.id and e.get("created_by") == ADMIN.id and e.get("project_id") == "p1"
              for e in (e1, e2)))
    listed = projects.list_projects(ADMIN)
    check("first admin: project payloads have exactly the old keys (no user_id, shared_by, access)",
          [set(x) for x in listed] == [PROJECT_KEYS], str([sorted(set(x) ^ PROJECT_KEYS) for x in listed]))
    check("first admin: the counts are what they were (the fixture's task t2 carries p1)",
          listed[0]["entry_count"] == 2 and listed[0]["task_count"] == 1, str(listed[0]))
    items = entries.list_entries(ADMIN, "p1")
    check("first admin: entry payloads have exactly the old keys",
          [set(x) for x in items] == [ENTRY_KEYS, ENTRY_KEYS], str([sorted(set(x) ^ ENTRY_KEYS) for x in items]))
    out = tool(ADMIN, "search_knowledge", query="tomato")
    check("migration: the FTS index still finds the first admin's entries", "Tomato varieties" in out, out)
    check("one user: share_item is not offered (tool list unchanged)",
          "share_item" not in tool_names(ADMIN) and KNOWLEDGE_TOOLS <= tool_names(ADMIN))


test_migration()

# --- Users -----------------------------------------------------------------------------------

BOB = users.create("Bob Builder")
CARL = users.create("Carl")  # no knowledge
EVE = users.create("Eve")
store.connect(BOB, "knowledge")
store.connect(BOB, "tasks")
store.connect(EVE, "knowledge")
ADMIN_TOKEN, _ = users.issue_token(ADMIN.id, "test admin")
BOB_TOKEN, _ = users.issue_token(BOB.id, "test bob")
CARL_TOKEN, _ = users.issue_token(CARL.id, "test carl")
IDS: Dict[str, str] = {}


def auth(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- 2. Isolation ----------------------------------------------------------------------------

def test_isolation() -> None:
    out = tool(BOB, "create_project", title="Garden plan", description="Bob's own garden")
    IDS["bob_garden"] = new_id(out)
    out = tool(BOB, "save_knowledge_item", project_id=IDS["bob_garden"], title="Cucumber seeds",
               body="Cucumber and tomato seeds from the market")
    IDS["bob_entry"] = new_id(out)
    check("tools: Bob's project and entry are his (owner and creator)",
          row("projects", IDS["bob_garden"]).get("user_id") == BOB.id
          and row("entries", IDS["bob_entry"]).get("user_id") == BOB.id
          and row("entries", IDS["bob_entry"]).get("created_by") == BOB.id)

    a, b = tool(ADMIN, "list_projects"), tool(BOB, "list_projects")
    check("tools: each lists only their own projects",
          "`p1`" in a and IDS["bob_garden"] not in a and IDS["bob_garden"] in b and "`p1`" not in b, b)
    out = tool(BOB, "list_project_entries", project_id="p1")
    check("tools: Bob can't list the first admin's project (not found)", "not found" in out and "Tomato" not in out, out)
    out = tool(BOB, "get_knowledge_item", item_id="e1")
    check("tools: Bob can't read the first admin's entry", out.startswith("Error: No entry") and "Options" not in out, out)
    out = tool(ADMIN, "get_knowledge_item", item_id=IDS["bob_entry"])
    check("tools: the first admin can't read Bob's entry", out.startswith("Error: No entry"), out)
    out = tool(BOB, "save_knowledge_item", project_id="p1", title="Sneaky")
    check("tools: Bob can't save into the first admin's project (not found)",
          "not found" in out and entries.list_entries(ADMIN, "p1")[0]["title"] != "Sneaky", out)
    out = tool(BOB, "update_project", project_id="p1", title="Mine now")
    check("tools: Bob can't change the first admin's project (not found)",
          "not found" in out and row("projects", "p1")["title"] == "Garden plan", out)
    out = tool(BOB, "update_knowledge_item", item_id="e1", title="Mine now")
    check("tools: Bob can't change the first admin's entry (not found)",
          "not found" in out and row("entries", "e1")["title"] == "Tomato varieties", out)
    out = tool(BOB, "delete_knowledge_item", item_id="e1")
    check("tools: Bob can't delete the first admin's entry (not found)", "not found" in out and row("entries", "e1"), out)
    out = tool(BOB, "delete_project", project_id="p1")
    check("tools: Bob can't delete the first admin's project (not found)", "not found" in out and row("projects", "p1"), out)

    a, b = tool(ADMIN, "search_knowledge", query="tomato"), tool(BOB, "search_knowledge", query="tomato")
    check("FTS (tool): each finds only their own entries",
          "Tomato varieties" in a and "Cucumber" not in a and "Cucumber" in b and "Tomato varieties" not in b, b)
    out = tool(BOB, "search_knowledge", query="tomato", project_id="p1")
    check("FTS (tool): Bob scoping to the first admin's project finds nothing", out.startswith("No results"), out)
    a, b = notes(ADMIN, "tomato"), notes(BOB, "tomato")
    check("search box: each finds only their own notes",
          {x["id"] for x in a} == {"e1"} and {x["id"] for x in b} == {IDS["bob_entry"]}, f"{a} | {b}")
    a, b = notes(ADMIN, "garden"), notes(BOB, "garden")
    check("search box: projects too (two 'Garden plan's, one each)",
          [x["id"] for x in a if x["id"].startswith("project:")] == ["project:p1"]
          and [x["id"] for x in b if x["id"].startswith("project:")] == [f"project:{IDS['bob_garden']}"], f"{a} | {b}")
    check("search box: Carl (no knowledge) finds no notes", notes(CARL, "tomato") == [] and notes(CARL, "garden") == [])

    check("Carl: the knowledge tools are not offered", not KNOWLEDGE_TOOLS & tool_names(CARL))
    try:
        tool(CARL, "list_projects")
        refused = False
    except registry.ToolNotAvailable:
        refused = True
    check("Carl: a knowledge tool is refused at execution like an unknown tool", refused)

    with users.acting_as(BOB):
        card = approval_text.describe("delete_project", {"project_id": "p1"})["title"]
        own = approval_text.describe("delete_knowledge_item", {"item_id": IDS["bob_entry"]})["title"]
        other = approval_text.describe("delete_knowledge_item", {"item_id": "e1"})["title"]
    check("approval text: another user's project / entry is not named", card == "Delete a project"
          and other == "Delete a note" and own == "Delete the note “Cucumber seeds”", f"{card} | {other} | {own}")
    with users.acting_as(BOB):
        bob_ctx = call_context.active_projects(BOB)
    with users.acting_as(ADMIN):
        admin_ctx = call_context.active_projects(ADMIN)
    check("call analysis: each user's active projects only",
          [p["id"] for p in bob_ctx] == [IDS["bob_garden"]] and [p["id"] for p in admin_ctx] == ["p1"],
          f"{bob_ctx} | {admin_ctx}")


# --- 3. Sharing --------------------------------------------------------------------------------

def test_sharing_read() -> None:
    check("several users: share_item is offered", "share_item" in tool_names(ADMIN))
    try:
        sharing.set_level(BOB, projects.ITEM_TYPE, "p1", EVE.id, "read")
        refused = "no"
    except LookupError:
        refused = "lookup"
    check("only the owner shares: Bob can't share a project he can't see", refused == "lookup")

    out = tool(ADMIN, "share_item", item_type="project", item="garden plan", user="Bob", level="read")
    check("share_item: the first admin shares his own 'Garden plan' (his, though Bob has one too)",
          out == "Shared the project 'Garden plan' with Bob Builder (read-only).", out)
    check("share_item: the grant is on p1", sharing.shared_ids(BOB, projects.ITEM_TYPE) == {"p1": "read"})

    out = tool(BOB, "list_projects")
    check("read share: Bob lists the shared project with a 'shared by … read-only' note, and his own",
          "`p1`" in out and f"[shared by {ADMIN.name}, read-only]" in out and IDS["bob_garden"] in out, out)
    out = tool(ADMIN, "list_projects")
    check("read share: the first admin's listing has no 'shared by'", "shared by" not in out, out)
    out = tool(BOB, "list_project_entries", project_id="p1")
    check("read share: entries follow the project (Bob lists them)", "Tomato varieties" in out and "Raised beds" in out, out)
    out = tool(BOB, "get_knowledge_item", item_id="e1")
    check("read share: Bob reads an entry, marked shared read-only",
          "Tomato varieties" in out and f"Shared by {ADMIN.name} (read-only)" in out, out)
    out = tool(BOB, "search_knowledge", query="tomato")
    check("read share: Bob's search finds the shared entry and his own",
          "Tomato varieties" in out and "Cucumber" in out, out)
    check("read share: the search box too", {x["id"] for x in notes(BOB, "tomato")} == {"e1", IDS["bob_entry"]})

    out = tool(BOB, "save_knowledge_item", project_id="p1", title="Sneaky")
    check("read share: saving into it is refused with a clear message",
          out == "Error: This project is shared with you read-only, so nothing in it can be changed."
          and all(e["title"] != "Sneaky" for e in entries.list_entries(ADMIN, "p1")), out)
    out = tool(BOB, "update_knowledge_item", item_id="e1", title="Changed")
    check("read share: changing an entry is refused", "read-only" in out and row("entries", "e1")["title"] == "Tomato varieties", out)
    out = tool(BOB, "delete_knowledge_item", item_id="e1")
    check("read share: deleting an entry is refused", "read-only" in out and row("entries", "e1"), out)

    out = tool(BOB, "update_project", project_id="p1", title="Renamed")
    check("owner only: Bob can't rename a project shared with him",
          out == "Error: Only the owner of the project can change it." and row("projects", "p1")["title"] == "Garden plan", out)
    out = tool(BOB, "delete_project", project_id="p1")
    check("owner only: Bob can't delete it", out == "Error: Only the owner of the project can delete it."
          and row("projects", "p1"), out)
    try:
        sharing.set_level(BOB, projects.ITEM_TYPE, "p1", EVE.id, "read")
        refused = "no"
    except sharing.NotOwner:
        refused = "owner"
    check("owner only: Bob can't re-share it", refused == "owner")
    out = tool(BOB, "share_item", item_type="project", item="Garden plan", user="Eve", level="read")
    check("name clash: Bob's share_item 'Garden plan' shares his own project",
          out == "Shared the project 'Garden plan' with Eve (read-only)." and
          sharing.shared_ids(EVE, projects.ITEM_TYPE) == {IDS["bob_garden"]: "read"}, out)
    sharing.set_level(BOB, projects.ITEM_TYPE, IDS["bob_garden"], EVE.id, "none")

    shared = {p["id"]: p for p in projects.list_projects(BOB)}
    check("payloads: the shared project carries shared_by / access for Bob",
          shared["p1"].get("shared_by") == ADMIN.name and shared["p1"].get("access") == "read"
          and set(shared["p1"]) == PROJECT_KEYS | {"shared_by", "access"})
    check("payloads: Bob's own project doesn't", set(shared[IDS["bob_garden"]]) == PROJECT_KEYS)
    users.update(ADMIN.id, currency="EUR")
    users.update(BOB.id, currency="GBP")
    shared = {p["id"]: p for p in projects.list_projects(BOB)}
    check("payloads: a project's currency is its owner's, also through a share",
          shared["p1"]["currency"] == "EUR" and shared[IDS["bob_garden"]]["currency"] == "GBP",
          [shared["p1"]["currency"], shared[IDS["bob_garden"]]["currency"]])
    users.update(ADMIN.id, currency="")
    users.update(BOB.id, currency="")
    check("payloads: the shared project's entries carry them too",
          all(e.get("shared_by") == ADMIN.name and e.get("access") == "read" for e in entries.list_entries(BOB, "p1")))
    check("payloads: the first admin's are unchanged", [set(x) for x in projects.list_projects(ADMIN)] == [PROJECT_KEYS]
          and all(set(e) == ENTRY_KEYS for e in entries.list_entries(ADMIN, "p1")))
    check("Eve (nothing shared) still sees none of it", projects.list_projects(EVE) == [] and notes(EVE, "tomato") == [])


def test_sharing_edit() -> None:
    out = tool(ADMIN, "share_item", item_type="project", item="p1", user="Bob", level="edit")
    check("share_item: edit", "they can edit it" in out, out)
    with undo.collect() as ids:
        out = tool(BOB, "save_knowledge_item", project_id="p1", title="Basil", body="Basil next to the tomatoes")
    IDS["bob_in_admin"] = new_id(out)
    IDS["bob_in_admin_undo"] = ids[0]["id"]
    r = row("entries", IDS["bob_in_admin"])
    check("edit share: Bob's entry in the shared project belongs to its owner and records Bob as its creator",
          r.get("project_id") == "p1" and r.get("user_id") == ADMIN.id and r.get("created_by") == BOB.id, str(r))
    out = tool(ADMIN, "get_knowledge_item", item_id=IDS["bob_in_admin"])
    check("edit share: the first admin sees Bob's entry, without a 'shared' note", "Basil" in out and "Shared by" not in out, out)
    out = tool(BOB, "update_knowledge_item", item_id="e1", priority="high")
    check("edit share: Bob changes the first admin's entry", "Successfully" in out and row("entries", "e1")["priority"] == "high", out)
    tool(ADMIN, "update_knowledge_item", item_id="e1", priority="medium")
    check("edit share: the first admin's search finds Bob's entry", "Basil" in tool(ADMIN, "search_knowledge", query="basil"))
    out = tool(BOB, "update_project", project_id="p1", description="Bob's")
    check("edit share: still only the owner changes the project itself", "Only the owner" in out, out)
    check("edit share: payload access is edit",
          {p["id"]: p for p in projects.list_projects(BOB)}["p1"].get("access") == "edit")


def test_links_and_tasks() -> None:
    """Tasks carrying a project stay governed by their own list."""
    admin_task = tool(ADMIN, "add_task", title="Buy compost")
    admin_task_id = row_by_title("Buy compost")
    bob_task = tool(BOB, "add_task", title="Water seedlings")
    bob_task_id = row_by_title("Water seedlings")
    with get_db_connection() as conn:
        conn.execute("UPDATE tasks SET project_id = 'p1' WHERE id IN (?, ?)", (admin_task_id, bob_task_id))
        conn.commit()
    links.link_task(bob_task_id, "project", "p1", "related")
    admin_tasks = [t["id"] for t in projects.project_tasks(ADMIN, "p1")]
    bob_tasks = [t["id"] for t in projects.project_tasks(BOB, "p1")]
    check("tasks of a shared project: each sees only the tasks their own lists give them",
          sorted(admin_tasks) == sorted(["t2", admin_task_id]) and bob_tasks == [bob_task_id],
          f"{admin_task} {bob_task} {admin_tasks} {bob_tasks}")
    counts = {u.name: {p["id"]: p["task_count"] for p in projects.list_projects(u)}["p1"] for u in (ADMIN, BOB)}
    check("task_count counts only the tasks the caller can see", counts == {ADMIN.name: 2, BOB.name: 1}, str(counts))
    with users.acting_as(ADMIN):
        admin_view = {t["id"] for t in projects.project_tasks(ADMIN, "p1")}
    check("a task linked to a project gives the project's owner no access to it", bob_task_id not in admin_view)
    IDS["bob_task"] = bob_task_id
    IDS["admin_task"] = admin_task_id


def row_by_title(title: str) -> str:
    with get_db_connection() as conn:
        return conn.execute("SELECT id FROM tasks WHERE title = ?", (title,)).fetchone()[0]


def test_media_processing() -> None:
    MEDIA_RUNS.clear()
    tool(BOB, "save_knowledge_item", project_id="p1", kind="link", title="", url="https://example.com/v/shared")
    tool(BOB, "save_knowledge_item", project_id=IDS["bob_garden"], kind="link", url="https://example.com/v/own")
    check("media: processing runs as the entry's owner (the project's owner for Bob's link in a shared project, "
          "Bob for his own)", MEDIA_RUNS == [("https://example.com/v/shared", ADMIN.id),
                                             ("https://example.com/v/own", BOB.id)], str(MEDIA_RUNS))
    with get_db_connection() as conn:
        r = conn.execute("SELECT summary, tags, source_author FROM entries WHERE url = ?",
                         ("https://example.com/v/shared",)).fetchone()
    check("media: the result is stored on the entry", r and r["summary"] == "Summary of https://example.com/v/shared"
          and r["source_author"] == "someone" and '"video"' in r["tags"], str(dict(r) if r else None))


# --- 4. HTTP -------------------------------------------------------------------------------

MEDIA_NAME = "e2-video.mp4"


def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    r = app.router
    r.add_get("/api/projects", knowledge_api.get_projects_api)
    r.add_post("/api/projects", knowledge_api.create_project_api)
    r.add_put("/api/projects/{id}", knowledge_api.update_project_api)
    r.add_delete("/api/projects/{id}", knowledge_api.delete_project_api)
    r.add_get("/api/projects/{id}/items", knowledge_api.get_project_items_api)
    r.add_post("/api/entries", knowledge_api.create_entry_api)
    r.add_put("/api/entries/{id}", knowledge_api.update_entry_api)
    r.add_delete("/api/entries/{id}", knowledge_api.delete_entry_api)
    r.add_post("/api/entries/{id}/retry", knowledge_api.retry_entry_api)
    r.add_get("/media/{name}", knowledge_api.media_file_api)
    sharing_api.setup(app)
    return app


async def test_http(client: TestClient) -> None:
    for method, path in (("GET", "/api/projects"), ("POST", "/api/projects"), ("GET", "/api/projects/p1/items"),
                         ("PUT", "/api/projects/p1"), ("DELETE", "/api/projects/p1"), ("POST", "/api/entries"),
                         ("PUT", "/api/entries/e1"), ("DELETE", "/api/entries/e1"), ("POST", "/api/entries/e2/retry"),
                         ("GET", f"/media/{MEDIA_NAME}"), ("GET", "/api/sharing/project/p1")):
        resp = await client.request(method, path, headers=auth(CARL_TOKEN), json={"title": "x", "project_id": "p1"})
        body = await resp.json()
        check(f"{method} {path} without knowledge: 403 not_connected",
              resp.status == 403 and body.get("error") == "not_connected" and body.get("addon") == "knowledge",
              f"{resp.status} {body}")

    resp = await client.get("/api/projects", headers=auth(ADMIN_TOKEN))
    admin_projects = await resp.json()
    check("GET /api/projects: the first admin's payload has the old keys only",
          resp.status == 200 and [set(p) for p in admin_projects] == [PROJECT_KEYS], str(admin_projects))
    resp = await client.get("/api/projects", headers=auth(BOB_TOKEN))
    bob_projects = {p["id"]: p for p in await resp.json()}
    check("GET /api/projects: Bob gets his own and the shared one (with shared_by / access)",
          set(bob_projects) == {"p1", IDS["bob_garden"]} and bob_projects["p1"].get("access") == "edit"
          and "shared_by" not in bob_projects[IDS["bob_garden"]], str(bob_projects))
    resp = await client.get(f"/api/projects/{IDS['bob_garden']}/items", headers=auth(ADMIN_TOKEN))
    check("GET items of Bob's project as the first admin: 404", resp.status == 404)
    resp = await client.get("/api/projects/p1/items", headers=auth(BOB_TOKEN))
    body = await resp.json()
    check("GET items of the shared project as Bob: its entries (shared_by / access) and only his own tasks",
          resp.status == 200 and {e["id"] for e in body["entries"]} >= {"e1", "e2", IDS["bob_in_admin"]}
          and all(e.get("access") == "edit" for e in body["entries"])
          and [t["id"] for t in body["tasks"]] == [IDS["bob_task"]]
          and not {"user_id", "created_by"} & set(body["tasks"][0]), str(body)[:300])
    resp = await client.get("/api/projects/p1/items", headers=auth(ADMIN_TOKEN))
    body = await resp.json()
    check("GET items as the first admin: entries with the old keys, only his own tasks",
          all(set(e) == ENTRY_KEYS for e in body["entries"])
          and sorted(t["id"] for t in body["tasks"]) == sorted(["t2", IDS["admin_task"]]),
          str(body)[:300])

    resp = await client.post("/api/projects", headers=auth(BOB_TOKEN), json={"title": "Bob via HTTP"})
    pid = (await resp.json()).get("id")
    check("POST /api/projects: owned by the caller", resp.status == 200 and row("projects", pid).get("user_id") == BOB.id)
    resp = await client.put(f"/api/projects/{pid}", headers=auth(ADMIN_TOKEN), json={"title": "x"})
    check("PUT another user's project: 404", resp.status == 404 and row("projects", pid)["title"] == "Bob via HTTP")
    resp = await client.delete(f"/api/projects/{pid}", headers=auth(ADMIN_TOKEN))
    check("DELETE another user's project: 404", resp.status == 404 and row("projects", pid))
    resp = await client.put("/api/projects/p1", headers=auth(BOB_TOKEN), json={"title": "x"})
    check("PUT a project shared with you: 403 owner_only",
          resp.status == 403 and (await resp.json())["error"] == "owner_only" and row("projects", "p1")["title"] == "Garden plan")
    resp = await client.delete("/api/projects/p1", headers=auth(BOB_TOKEN))
    check("DELETE a project shared with you: 403 owner_only", resp.status == 403 and row("projects", "p1"))
    resp = await client.post("/api/entries", headers=auth(ADMIN_TOKEN), json={"project_id": pid, "title": "x"})
    check("POST an entry into another user's project: 404", resp.status == 404)
    resp = await client.put(f"/api/entries/{IDS['bob_entry']}", headers=auth(ADMIN_TOKEN), json={"title": "x"})
    check("PUT another user's entry: 404", resp.status == 404 and row("entries", IDS["bob_entry"])["title"] == "Cucumber seeds")
    resp = await client.delete(f"/api/entries/{IDS['bob_entry']}", headers=auth(ADMIN_TOKEN))
    check("DELETE another user's entry: 404", resp.status == 404 and row("entries", IDS["bob_entry"]))
    resp = await client.post("/api/entries/e2/retry", headers=auth(EVE_TOKEN))
    check("retry another user's entry: 404", resp.status == 404)
    resp = await client.get("/api/sharing/project/p1", headers=auth(BOB_TOKEN))
    check("sharing API: Bob can't manage a project shared with him (403 owner_only)",
          resp.status == 403 and (await resp.json())["error"] == "owner_only")
    resp = await client.get("/api/sharing/project/p1", headers=auth(ADMIN_TOKEN))
    body = await resp.json()
    check("sharing API: the owner sees who has it",
          resp.status == 200 and body["title"] == "Garden plan"
          and body["shared_with"] == [{"user_id": BOB.id, "name": BOB.name, "level": "edit"}], str(body))

    # Read-only over HTTP.
    resp = await client.put("/api/sharing/project/p1", headers=auth(ADMIN_TOKEN), json={"user_id": BOB.id, "level": "read"})
    check("sharing API: the owner sets read", resp.status == 200)
    resp = await client.post("/api/entries", headers=auth(BOB_TOKEN), json={"project_id": "p1", "title": "x"})
    body = await resp.json()
    check("POST an entry into a read-only project: 403 read_only", resp.status == 403 and body["error"] == "read_only", str(body))
    resp = await client.put("/api/entries/e1", headers=auth(BOB_TOKEN), json={"title": "x"})
    check("PUT an entry of a read-only project: 403 read_only",
          resp.status == 403 and row("entries", "e1")["title"] == "Tomato varieties")
    resp = await client.delete("/api/entries/e1", headers=auth(BOB_TOKEN))
    check("DELETE an entry of a read-only project: 403", resp.status == 403 and row("entries", "e1"))
    resp = await client.post("/api/entries/e2/retry", headers=auth(BOB_TOKEN))
    check("retry an entry of a read-only project: 403", resp.status == 403)

    # Stored media files.
    os.makedirs(media.MEDIA_DIR, exist_ok=True)
    with open(os.path.join(media.MEDIA_DIR, MEDIA_NAME), "wb") as f:
        f.write(b"VIDEO")
    with open(os.path.join(media.MEDIA_DIR, "orphan.mp4"), "wb") as f:
        f.write(b"ORPHAN")
    with get_db_connection() as conn:
        conn.execute("UPDATE entries SET media_path = ? WHERE id = 'e2'", (media.URL_PREFIX + MEDIA_NAME,))
        conn.commit()
    resp = await client.get(f"/media/{MEDIA_NAME}", headers=auth(ADMIN_TOKEN))
    check("media: the owner gets the file", resp.status == 200 and await resp.read() == b"VIDEO")
    resp = await client.get(f"/media/{MEDIA_NAME}", headers={**auth(ADMIN_TOKEN), "Range": "bytes=1-2"})
    check("media: range requests still work (video seeking)", resp.status == 206 and await resp.read() == b"ID")
    resp = await client.get(f"/media/{MEDIA_NAME}", headers=auth(BOB_TOKEN))
    check("media: Bob (project shared with him) gets it", resp.status == 200)
    resp = await client.get(f"/media/{MEDIA_NAME}", headers=auth(EVE_TOKEN))
    check("media: Eve (nothing shared) gets 404", resp.status == 404)
    resp = await client.get("/media/orphan.mp4", headers=auth(ADMIN_TOKEN))
    check("media: a file no entry has is not served", resp.status == 404)
    resp = await client.get("/media/..%2Fdb.sqlite3", headers=auth(ADMIN_TOKEN))
    check("media: no path tricks", resp.status == 404)


EVE_TOKEN, _ = users.issue_token(EVE.id, "test eve")


async def test_http_unshared(client: TestClient) -> None:
    resp = await client.get(f"/media/{MEDIA_NAME}", headers=auth(BOB_TOKEN))
    check("unshare: Bob no longer gets the media file", resp.status == 404)
    resp = await client.get("/api/projects/p1/items", headers=auth(BOB_TOKEN))
    check("unshare: GET items of the project → 404 for Bob", resp.status == 404)


async def http_main(fn) -> None:
    async with TestClient(TestServer(build_app())) as client:
        await fn(client)


# --- 5. Unshare, undo, delete ----------------------------------------------------------------

def test_unshare() -> None:
    sharing.set_level(ADMIN, projects.ITEM_TYPE, "p1", BOB.id, "edit")
    with undo.collect() as ids:
        out = tool(BOB, "update_knowledge_item", item_id="e1", status="done")
    undo_id = ids[0]["id"]
    out = tool(ADMIN, "share_item", item_type="project", item="Garden plan", user="Bob", level="none")
    check("share_item: stop sharing", out == "Stopped sharing the project 'Garden plan' with Bob Builder.", out)
    out = tool(BOB, "list_projects")
    check("unshare: the project is gone from Bob's listing", "`p1`" not in out and IDS["bob_garden"] in out, out)
    check("unshare: its entries too (tool, search, search box)",
          tool(BOB, "get_knowledge_item", item_id="e1").startswith("Error: No entry")
          and "Tomato varieties" not in tool(BOB, "search_knowledge", query="tomato")
          and "Basil" not in tool(BOB, "search_knowledge", query="basil")
          and {x["id"] for x in notes(BOB, "tomato")} == {IDS["bob_entry"]})
    check("unshare: the entry Bob added stays in the project, the first admin's",
          row("entries", IDS["bob_in_admin"]).get("user_id") == ADMIN.id
          and "Basil" in tool(ADMIN, "search_knowledge", query="basil"))
    with users.acting_as(BOB):
        try:
            run(undo.run(undo_id))
            conflict = ""
        except Exception as e:  # UndoConflict
            conflict = str(e)
        try:
            run(undo.run(IDS["bob_in_admin_undo"]))
            conflict2 = ""
        except Exception as e:
            conflict2 = str(e)
    check("undo: after unsharing Bob's undo of a change is refused and the change stays",
          "no longer change" in conflict and row("entries", "e1")["status"] == "done", conflict)
    check("undo: and his undo of an entry he saved there is refused",
          "no longer change" in conflict2 and row("entries", IDS["bob_in_admin"]), conflict2)
    tool(ADMIN, "update_knowledge_item", item_id="e1", status="open")
    run(http_main(test_http_unshared))
    bob_view = {t["id"] for t in projects.project_tasks(BOB, "p1")}
    check("unshare: Bob's own task that carries the project is still his", IDS["bob_task"] in bob_view)


def test_delete_shared_project() -> None:
    sharing.set_level(ADMIN, projects.ITEM_TYPE, "p1", BOB.id, "read")
    out = tool(ADMIN, "delete_project", project_id="p1")
    check("delete: the owner deletes the shared project", "Successfully deleted" in out, out)
    check("delete: its entries are gone, also from the FTS index",
          not row("entries", "e1") and not row("entries", IDS["bob_in_admin"])
          and "Tomato varieties" not in tool(ADMIN, "search_knowledge", query="tomato"))
    check("delete: its shares end", sharing.shares(projects.ITEM_TYPE, "p1") == []
          and sharing.shared_ids(BOB, projects.ITEM_TYPE) == {})
    check("delete: the owner's task with the project is deleted, Bob's task only loses the project",
          not row("tasks", IDS["admin_task"]) and not row("tasks", "t2")
          and row("tasks", IDS["bob_task"]).get("project_id") is None)
    check("delete: the media file is no longer served to anyone", entries.media_file(ADMIN, MEDIA_NAME) is None)


test_isolation()
test_sharing_read()
test_sharing_edit()
test_links_and_tasks()
test_media_processing()
run(http_main(test_http))
test_unshare()
test_delete_shared_project()

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
if passed != len(results):
    sys.exit(1)
