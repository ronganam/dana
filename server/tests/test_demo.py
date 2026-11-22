"""Checks for the demo household (core/demo.py, core/demo_api.py, the view cookie in core/auth.py): load, view, remove exactly it.
Run: bin/test test_demo

Robin (the first admin) has real data (a task, a shopping item, a person, a usage row) and Maya is a member.
Only manage_users may load, view or remove the demo; loading writes made-up users and their rows (people,
calls, notifications, tasks, shopping, money, about) and touches no real user's row; the demo users are left
out of everything that walks the household; loading twice changes nothing; Robin views the app as a demo
user (the Now screen shows the demo's data) while /api/demo stays his; removing it brings every table, FTS
tables included, back to exactly what it held before. Temp DB, no network.
"""

import asyncio
import json
import os
import sqlite3
from typing import Any, Dict, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("demo_test")
os.environ["DANA_ADDON_DIRS"] = ""

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import addons  # noqa: F401  (loads the addons and their demo data)
from core import db, demo, demo_api, now_api, users
from core.addons import registry, scheduler, sharing, store
from core.auth import DEMO_VIEW_COOKIE, request_user, setup_auth
from core.db import get_db_connection
from core.paths import DATABASE_PATH

db.migrate()
users.bootstrap()
store.connect_first_admin_defaults()

results: List[Tuple[str, bool]] = []


def check(name: str, cond: Any, detail: Any = "") -> None:
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


async def whoami(request: web.Request) -> web.Response:
    viewer = request.get("viewer")
    return web.json_response({"user": request_user(request).id, "viewer": viewer.id if viewer else None})


def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    demo_api.setup(app)
    now_api.setup(app)
    app.router.add_get("/api/whoami", whoami)
    return app


# FTS5's own b-tree segments: their bytes need not shrink back after deletes (the index's contents are
# compared through the FTS table itself and its _content / _docsize tables).
FTS_INTERNALS = ("_data", "_idx", "_config")


def dump() -> Dict[str, List[Tuple[Any, ...]]]:
    """Every row of every table (an FTS table's rows and its content), sorted; a token's last use left out
    (the requests themselves write it)."""
    conn = sqlite3.connect(str(DATABASE_PATH))
    try:
        tables = [t for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                             "AND name NOT LIKE 'sqlite_%' ORDER BY name")
                  if not (t.endswith(FTS_INTERNALS) and "_fts" in t)]
        out = {}
        for t in tables:
            cols = "id, user_id, label, token_hash, session_hash, created_at" if t == "user_tokens" else "*"
            out[t] = sorted((tuple(map(repr, r)) for r in conn.execute(f'SELECT {cols} FROM "{t}"')), key=repr)
        return out
    finally:
        conn.close()


def count_for(user_ids: List[str]) -> Dict[str, int]:
    """Rows per table whose user_id is one of ``user_ids``."""
    out: Dict[str, int] = {}
    with get_db_connection() as conn:
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"):
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')]
            if "user_id" in cols:
                marks = ",".join("?" * len(user_ids))
                n = conn.execute(f'SELECT COUNT(*) FROM "{t}" WHERE user_id IN ({marks})', user_ids).fetchone()[0]
                if n:
                    out[t] = n
    return out


def real_data(robin: users.User) -> None:
    """Some of Robin's own rows, to prove the demo leaves them alone."""
    from addons.people import store as people_store
    from addons.tasks import service
    from addons.shopping import lists
    service.create_task(robin, title="Robin's real task")
    lst = lists.ensure_list(robin)
    with get_db_connection() as conn:
        conn.execute("INSERT INTO shopping_items (id, list_id, name, name_key, created_at) "
                     "VALUES ('realitem0001', ?, 'Real milk', 'real milk', '2026-11-01T10:00:00+00:00')", (lst,))
        conn.execute("INSERT INTO usage_events (ts, day, kind, purpose, model, user_id) "
                     "VALUES (1761991200, '2026-11-01', 'llm', 'chat', 'm', ?)", (robin.id,))
        conn.commit()
    people_store.create_person(robin, "Real Friend", phones=["+15555550199"])


async def call(c: TestClient, method: str, path: str, token: str, body: Any = None,
               cookies: Dict[str, str] | None = None) -> Tuple[int, Any]:
    headers = {"Authorization": f"Bearer {token}"}
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    r = await c.request(method, path, headers=headers, json=body)
    try:
        return r.status, await r.json()
    except Exception:
        return r.status, await r.text()


async def run(c: TestClient) -> None:
    robin = users.first_admin()
    assert robin is not None
    maya = users.create("Maya")
    store.connect_defaults(maya)
    t_robin, _ = users.issue_token(robin.id)
    t_maya, _ = users.issue_token(maya.id)
    real_data(robin)
    real_ids = [robin.id, maya.id]
    before = dump()
    real_before = count_for(real_ids)

    # --- Who may ---
    for method in ("GET", "POST", "DELETE"):
        st, b = await call(c, method, "/api/demo", t_maya)
        check(f"member: {method} /api/demo is refused (403 manage_users)", st == 403 and b.get("needs") == "manage_users", b)
    st, b = await call(c, "POST", "/api/demo/view", t_maya)
    check("member: viewing the demo is refused", st == 403, b)
    st, b = await call(c, "GET", "/api/demo", t_robin)
    check("admin: GET before loading → not loaded", st == 200 and b == {"loaded": False, "users": [], "rows": {},
                                                                         "viewing": None}, b)
    st, b = await call(c, "POST", "/api/demo/view", t_robin)
    check("admin: viewing before loading → 409 not_loaded", st == 409 and b.get("error") == "not_loaded", b)
    check("nothing was written by the refused calls", dump() == before)

    # --- Loading ---
    st, b = await call(c, "POST", "/api/demo", t_robin)
    demo_users = users.demo_users()
    demo_ids = [u.id for u in demo_users]
    check("admin: POST loads two demo users (201) with rows from each addon that has demo data",
          st == 201 and b["loaded"] and [u["id"] for u in b["users"]] == demo_ids and len(demo_ids) == 2
          and {"people", "phone", "tasks", "shopping", "money", "about"} <= set(b["rows"])
          and all(n > 0 for n in b["rows"].values()), b)
    got = count_for(demo_ids)
    check("the demo users own their rows in every table the hooks wrote",
          {"people", "person_phones", "phone_calls", "call_suggestions", "notifications", "tasks", "task_groups",
           "shopping_lists", "money_accounts", "money_transactions", "about_facts", "connections"} <= set(got), got)
    sam = demo_users[0]
    with get_db_connection() as conn:
        items = conn.execute("SELECT COUNT(*) FROM shopping_items i JOIN shopping_lists l ON l.id = i.list_id "
                             "WHERE l.user_id = ?", (sam.id,)).fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND triage_status != 'done'",
                               (sam.id,)).fetchone()[0]
        aisles = conn.execute("SELECT COUNT(*) FROM shopping_aisles").fetchone()[0]
    check("demo data: shopping items on the demo list, notifications already sorted (no model call waits)",
          items == 8 and pending == 0, (items, pending))
    check("demo data: the shared aisle table is untouched", aisles == len(before["shopping_aisles"]), aisles)
    check("no real user's row changed: their per-table counts are the same", count_for(real_ids) == real_before,
          (count_for(real_ids), real_before))
    after_load = dump()
    check("no real row changed: every row there before is still there as it was",
          all(set(rows) <= set(after_load[t]) for t, rows in before.items()
              if t not in ("system_marks",)) and set(before["system_marks"]) <= set(after_load["system_marks"]))

    # --- Kept out of the household ---
    check("demo users are not in list_users (household, sharing targets, composers)",
          [u.id for u in users.list_users()] == real_ids and all(u.demo for u in demo_users))
    check("the scheduler runs no job for a demo user",
          not any(u.demo for a in ("phone", "people", "about", "money") for u in scheduler.users_for(a)))
    check("share_item is not offered on account of demo users (Robin is still the only other user Maya has)",
          sharing.tool_available(maya) == sharing.tool_available(robin))
    check("a demo user is a member, has no token and no Telegram id",
          all(u.access == "member" and not u.telegram_ids and not users.list_tokens(u.id) for u in demo_users))
    check("a demo user cannot be made admin", _refuses_admin(sam.id))

    # --- Twice ---
    st, b = await call(c, "POST", "/api/demo", t_robin)
    check("loading twice: 200, the same users, nothing written", st == 200 and [u["id"] for u in b["users"]] == demo_ids
          and b["rows"] == {} and dump() == after_load, b)
    st, b = await call(c, "GET", "/api/demo", t_robin)
    check("GET says it is loaded, with its users", st == 200 and b["loaded"] and [u["id"] for u in b["users"]] == demo_ids, b)

    # --- Viewing ---
    st, b = await call(c, "POST", "/api/demo/view", t_robin, {"user_id": robin.id})
    check("viewing a real user is refused (404)", st == 404, b)
    st, b = await call(c, "POST", "/api/demo/view", t_robin)
    check("admin: views the first demo user by default", st == 200 and b == {"viewing": sam.id}, b)
    c.session.cookie_jar.clear()   # each request below names its own cookie
    view = {DEMO_VIEW_COOKIE: sam.id}
    st, b = await call(c, "GET", "/api/whoami", t_robin, cookies=view)
    check("while viewing, requests are the demo user's, with the admin as the viewer",
          st == 200 and b == {"user": sam.id, "viewer": robin.id}, b)
    st, b = await call(c, "GET", "/api/demo", t_robin, cookies=view)
    check("while viewing, /api/demo is still the admin's (and says who is viewed)",
          st == 200 and b["viewing"] == sam.id, b)
    st, b = await call(c, "GET", "/api/now", t_robin, cookies=view)
    text = json.dumps(b)
    check("while viewing, the Now screen shows the demo's data (its people and notifications)",
          st == 200 and ("Riley Brooks" in text or "Parcel Tracker" in text) and "Real milk" not in text, text[:600])
    st, b = await call(c, "GET", "/api/whoami", t_maya, cookies=view)
    check("a member with the cookie stays themselves", b == {"user": maya.id, "viewer": None}, b)
    st, b = await call(c, "GET", "/api/whoami", t_robin, cookies={DEMO_VIEW_COOKIE: maya.id})
    check("the cookie naming a real user is ignored", b == {"user": robin.id, "viewer": None}, b)
    r = await c.request("DELETE", "/api/demo/view", headers={"Authorization": f"Bearer {t_robin}"})
    check("stop viewing clears the cookie", r.status == 200 and DEMO_VIEW_COOKIE in r.cookies
          and not r.cookies[DEMO_VIEW_COOKIE].value)

    # --- Removing ---
    with get_db_connection() as conn:
        conn.execute("INSERT INTO usage_events (ts, day, kind, purpose, model, user_id) "
                     "VALUES (1762077600, '2026-11-02', 'llm', 'chat', 'm', ?)", (sam.id,))
        conn.execute("INSERT INTO usage_daily (day, user_id, kind, purpose, model, calls) "
                     "VALUES ('2026-11-02', ?, 'llm', 'chat', 'm', 1)",
                     (sam.id,))
        conn.commit()
    r = await c.request("DELETE", "/api/demo", headers={"Authorization": f"Bearer {t_robin}"})
    b = await r.json()
    check("admin: DELETE removes the demo users", r.status == 200 and b["removed"] == 2 and not users.demo_users()
          and all(users.get(i) is None for i in demo_ids), b)
    after = dump()
    changed = {t: (len(before.get(t, [])), len(after.get(t, []))) for t in set(before) | set(after)
               if before.get(t) != after.get(t)}
    check("after removing: every table (FTS included) holds exactly what it held before loading", not changed, changed)
    check("after removing: no row of a demo user anywhere, their usage rows too", not count_for(demo_ids))
    st, b = await call(c, "GET", "/api/demo", t_robin)
    check("GET says it is not loaded", st == 200 and not b["loaded"], b)
    st, b = await call(c, "DELETE", "/api/demo", t_robin)
    check("removing again does nothing", st == 200 and b["removed"] == 0 and dump() == before, b)
    st, b = await call(c, "POST", "/api/demo", t_robin)
    check("it loads again after removing", st == 201 and len(users.demo_users()) == 2, b)
    await demo.remove()
    check("and removes again, exactly", dump() == before)


def _refuses_admin(user_id: str) -> bool:
    try:
        users.update(user_id, access_level="admin")
    except ValueError:
        return not users.get(user_id).is_admin
    return False


async def main() -> None:
    check("every official addon with demo data is loaded", {"people", "phone", "tasks", "shopping", "money", "about"}
          <= {h.addon for h in registry.hooks("demo", None)})
    async with TestClient(TestServer(build_app())) as c:
        await run(c)
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
