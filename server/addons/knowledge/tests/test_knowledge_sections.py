"""Project sections, option decisions, the budget roll-up and files kept on entries (docs/knowledge.md).

Users: Robin (the first admin, owns the project), Bob (an ``edit`` share), Eve (a
``read`` share) and Carl (knowledge, nothing shared). Through the real aiohttp
handlers, the agent tools (via the registry) and a fake WAHA for
``save_whatsapp_file``.

Covers: sections in the items payload, ordered; an edit share adds and changes
sections, a read share is refused (403 ``read_only``), a stranger gets 404;
column keys survive a rename and the rename reaches the entries' fields; an
entry's section must be one of its project's (400); decisions and the
project's ``decided`` / ``committed`` (chosen options' price column); deleting
a section leaves its entries loose (or deletes them with ``with_entries``);
files: multipart upload, inline GET (Range) for whoever sees the entry, 404
for others, no writes through a read share, gone from disk with their entry /
project; the tools: create_section, save into a section by title with a
decision, list_project_entries shows sections, columns and values,
update_section writes the summary, get_knowledge_item lists files and
transcripts, the undo of create_section; save_whatsapp_file keeps a WhatsApp
file on an entry (and its undo removes it); search finds a word in a file's text, Hebrew prefix
forms, falls back to any word and shows the snippet, section and decision; update_knowledge_item
moves an entry to another project (undoable); update_entry_file renames (undoable) and removes
(asks first); update_section merges columns; delete_section's Approve card.

Run: bin/test test_knowledge_sections
"""

import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("knowledge_sections_test")
ROOT = str(testenv.SERVER)
os.environ["API_TOKEN"] = "knowledge-sections-test-env-token"
os.environ.setdefault("GEMINI_API_KEY", "test-key")
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, ROOT)

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from core import db, paths, undo, users

db.migrate()
users.bootstrap()

from core.auth import setup_auth
from core.db import get_db_connection
from core.addons import registry, sharing, store
from addons.knowledge import api as knowledge_api, entries, files, projects, sections, tools
from testfakes import FakeWAHA

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


ROBIN = users.first_admin()
assert ROBIN is not None
BOB, EVE, CARL = users.create("Bob"), users.create("Eve"), users.create("Carl")
for u in (ROBIN, BOB, EVE, CARL):
    store.connect(u, "knowledge")
TOKENS = {u.id: users.issue_token(u.id, "test")[0] for u in (ROBIN, BOB, EVE, CARL)}


def auth(u) -> Dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[u.id]}"}


UNDOS: List[str] = []  # the undo ids the last tool call registered


async def tool(who, tool_name: str, **args) -> str:
    with users.acting_as(who), undo.collect() as recorded:
        out = str(await registry.execute(who, tool_name, args))
    UNDOS[:] = [u["id"] for u in recorded]
    return out


async def undo_last(who) -> str:
    with users.acting_as(who):
        try:
            await undo.run(UNDOS[-1])
            return ""
        except Exception as e:
            return str(e) or type(e).__name__


def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    knowledge_api.setup(app)
    return app


def new_id(out: str) -> str:
    return out.rsplit("`", 2)[-2]


WEB_FIXTURE = os.path.join(testenv.FIXTURES, "projects_web.json")


def shape(value: Any) -> Any:
    """The keys of a payload, recursively (lists by their items' shapes, values dropped)."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):  # order-free: rows made in the same second come back in either order
        return sorted((shape(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    return type(value).__name__ if value is not None else None


async def web_fixture(c: TestClient, pid: str) -> None:
    """The payloads the web panel reads (web/projects-core.js, test_web_projects.mjs): the owner's
    list and items, and a read share's items. Written with WRITE_WEB_FIXTURE=1, else checked for drift."""
    captured = {}
    r = await c.get("/api/projects", headers=auth(ROBIN))
    captured["list_owner"] = [p for p in await r.json() if p["id"] == pid]
    r = await c.get(f"/api/projects/{pid}/items", headers=auth(ROBIN))
    captured["items_owner"] = await r.json()
    r = await c.get(f"/api/projects/{pid}/items", headers=auth(EVE))
    captured["items_read"] = await r.json()
    text = json.dumps(captured, indent=1, ensure_ascii=False, sort_keys=True)
    if os.getenv("WRITE_WEB_FIXTURE"):
        with open(WEB_FIXTURE, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"wrote {WEB_FIXTURE}")
        return
    with open(WEB_FIXTURE, encoding="utf-8") as f:
        stored = json.load(f)
    drift = [k for k in captured if shape(captured[k]) != shape(stored.get(k))] + [k for k in stored if k not in captured]
    check("fixture: tests/fixtures/projects_web.json has the payloads' current shape "
          "(regenerate with WRITE_WEB_FIXTURE=1 bin/test test_knowledge_sections)", not drift, drift)


async def main() -> None:
    async with TestClient(TestServer(build_app())) as c:
        # --- the project, shares ---------------------------------------------------------------
        r = await c.post("/api/projects", headers=auth(ROBIN),
                         json={"title": "חתונה", "icon": "💍", "target_date": "2027-09-20", "budget": "150,000"})
        pid = (await r.json())["id"]
        sharing.set_level(ROBIN, projects.ITEM_TYPE, pid, BOB.id, "edit")
        sharing.set_level(ROBIN, projects.ITEM_TYPE, pid, EVE.id, "read")

        # --- sections -----------------------------------------------------------------------------
        cols = [{"name": "מחיר", "type": "money", "role": "price"}, {"name": "טלפון", "type": "phone"},
                {"name": "וידאו", "type": "checkbox"}]
        r = await c.post(f"/api/projects/{pid}/sections", headers=auth(ROBIN),
                         json={"title": "צלמים", "kind": "options", "columns": cols, "icon": "📸"})
        body = await r.json()
        sid = body.get("id")
        section = body.get("section") or {}
        check("section: created with keyed, typed columns",
              r.status == 200 and [c_["name"] for c_ in section.get("columns", [])] == ["מחיר", "טלפון", "וידאו"]
              and all(c_.get("key") for c_ in section["columns"]) and section["columns"][0].get("role") == "price",
              body)
        r = await c.post(f"/api/projects/{pid}/sections", headers=auth(BOB), json={"title": "רעיונות", "kind": "notes"})
        notes_sid = (await r.json()).get("id")
        check("section: an edit share adds one", r.status == 200 and notes_sid)
        r = await c.post(f"/api/projects/{pid}/sections", headers=auth(EVE), json={"title": "x"})
        check("section: a read share is refused (403 read_only)", r.status == 403 and (await r.json())["error"] == "read_only")
        r = await c.post(f"/api/projects/{pid}/sections", headers=auth(CARL), json={"title": "x"})
        check("section: a stranger gets 404", r.status == 404)
        r = await c.put(f"/api/sections/{sid}", headers=auth(CARL), json={"title": "x"})
        check("section: a stranger can't change one (404)", r.status == 404)
        r = await c.put(f"/api/sections/{sid}", headers=auth(EVE), json={"summary": "x"})
        check("section: a read share can't change one (403)", r.status == 403)

        # --- options and decisions ----------------------------------------------------------------
        async def option(title: str, price: Any, decision: str = "", who=ROBIN) -> str:
            r = await c.post("/api/entries", headers=auth(who), json={
                "project_id": pid, "section_id": sid, "title": title, "decision": decision,
                "fields": [{"name": "מחיר", "type": "money", "value": price},
                           {"name": "טלפון", "type": "phone", "value": "052-0000000"}]})
            body = await r.json()
            assert r.status == 200, body
            return body["id"]

        ori = await option("אורי עשור", "13,400 ₪")
        ett = await option("אטדגי", 13900, "shortlist", who=BOB)
        kobi = await option("רובין קובי", "10500-16500", "rejected")
        r = await c.get(f"/api/projects/{pid}/items", headers=auth(EVE))
        items = await r.json()
        by_id = {e["id"]: e for e in items["entries"]}
        check("items: sections in order with the project",
              [s["title"] for s in items["sections"]] == ["צלמים", "רעיונות"] and items["project"]["target_date"] == "2027-09-20"
              and items["project"]["budget"] == 150000, [s["title"] for s in items["sections"]])
        check("options: money parsed to a number, a range kept as text, decisions stored",
              by_id[ori]["fields"][0]["value"] == 13400 and by_id[kobi]["fields"][0]["value"] == "10500-16500"
              and by_id[ett]["decision"] == "shortlist" and by_id[ori]["section_id"] == sid, by_id[ori]["fields"])
        r = await c.put(f"/api/entries/{ori}", headers=auth(ROBIN), json={"decision": "chosen"})
        check("decision: set through PUT", (await r.json())["entry"]["decision"] == "chosen")
        r = await c.get("/api/projects", headers=auth(ROBIN))
        p = next(x for x in await r.json() if x["id"] == pid)
        check("project: decided / committed count the chosen option's price",
              p["decisions"] == 1 and p["decided"] == 1 and p["committed"] == 13400 and p["section_count"] == 2, p)

        # wrong section
        r = await c.post("/api/projects", headers=auth(ROBIN), json={"title": "Other"})
        other = (await r.json())["id"]
        r = await c.post("/api/entries", headers=auth(ROBIN), json={"project_id": other, "section_id": sid, "title": "x"})
        check("entry: a section of another project is refused (400 section_id)",
              r.status == 400 and (await r.json()).get("field") == "section_id")

        # rename keeps key and values
        cols_now = items["sections"][0]["columns"]
        renamed = [dict(cols_now[0], name="מחיר כולל"), *cols_now[1:]]
        r = await c.put(f"/api/sections/{sid}", headers=auth(BOB), json={"columns": renamed})
        new_cols = (await r.json())["section"]["columns"]
        r = await c.get(f"/api/projects/{pid}/items", headers=auth(ROBIN))
        ori_now = next(e for e in (await r.json())["entries"] if e["id"] == ori)
        check("columns: a rename keeps the key and renames the entries' values",
              new_cols[0]["key"] == cols_now[0]["key"] and new_cols[0]["name"] == "מחיר כולל"
              and ori_now["fields"][0] == {"name": "מחיר כולל", "type": "money", "value": 13400}, ori_now["fields"])

        # --- files ----------------------------------------------------------------------------------
        form = FormData()
        form.add_field("file", b"%PDF-1.4 quote", filename="הצעת מחיר.pdf", content_type="application/pdf")
        form.add_field("note", "הצעת מחיר")
        r = await c.post(f"/api/entries/{ori}/files", headers=auth(BOB), data=form)
        body = await r.json()
        f = body.get("file") or {}
        check("files: an edit share uploads (name, mime, size, note, url; no stored name)",
              r.status == 200 and f.get("name") == "הצעת מחיר.pdf" and f.get("mime") == "application/pdf"
              and f.get("size") == 14 and f.get("note") == "הצעת מחיר" and "stored" not in f
              and f.get("url") == f"/api/entries/{ori}/files/{f.get('id')}", body)
        r = await c.get(f["url"], headers=auth(EVE))
        check("files: a read share opens it inline", r.status == 200 and await r.read() == b"%PDF-1.4 quote"
              and r.headers["Content-Type"].startswith("application/pdf")
              and r.headers["Content-Disposition"].startswith("inline"))
        r = await c.get(f["url"], headers={**auth(ROBIN), "Range": "bytes=0-3"})
        check("files: Range works (206)", r.status == 206 and await r.read() == b"%PDF")
        r = await c.get(f["url"], headers=auth(CARL))
        check("files: a stranger gets 404", r.status == 404)
        r = await c.get(f"/api/entries/{ett}/files/{f['id']}", headers=auth(ROBIN))
        check("files: only under the entry it is on", r.status == 404)
        form = FormData()
        form.add_field("file", b"x", filename="x.txt", content_type="text/plain")
        r = await c.post(f"/api/entries/{ori}/files", headers=auth(EVE), data=form)
        check("files: a read share can't upload (403)", r.status == 403)
        r = await c.delete(f["url"], headers=auth(EVE))
        check("files: a read share can't delete (403)", r.status == 403)
        r = await c.get(f"/api/projects/{pid}/items", headers=auth(ROBIN))
        ori_now = next(e for e in (await r.json())["entries"] if e["id"] == ori)
        check("files: listed on the entry", [x["id"] for x in ori_now["files"]] == [f["id"]])
        with get_db_connection() as conn:
            stored = conn.execute("SELECT stored FROM entry_files WHERE id = ?", (f["id"],)).fetchone()["stored"]
        on_disk = paths.KNOWLEDGE_FILES_DIR / stored
        check("files: kept under the data directory", on_disk.is_file())

        # --- tools --------------------------------------------------------------------------------
        out = await tool(BOB, "create_section", project_id=pid, title="DJ", kind="options",
                         columns=[{"name": "מחיר", "type": "money", "role": "price"}])
        dj = new_id(out)
        check("tool: create_section", "Created section" in out and dj, out)
        out = await tool(BOB, "save_knowledge_item", project_id=pid, section="dj", title="DJ Noa", kind="record",
                         decision="shortlist", fields=[{"name": "מחיר", "type": "money", "value": 6000}])
        noa = new_id(out)
        with get_db_connection() as conn:
            row = dict(conn.execute("SELECT section_id, decision FROM entries WHERE id = ?", (noa,)).fetchone())
        check("tool: save into a section by title, with a decision", row == {"section_id": dj, "decision": "shortlist"}, out)
        out = await tool(ROBIN, "save_knowledge_item", project_id=pid, section="nope", title="x")
        check("tool: an unknown section is an error", out.startswith("Error") and "section" in out, out)
        out = await tool(ROBIN, "update_section", section_id=sid, summary="## TL;DR\n- אורי: 13,400")
        out2 = await tool(EVE, "list_project_entries", project_id=pid)
        check("tool: list_project_entries shows sections, columns, decisions and values",
              "צלמים" in out2 and "Columns: מחיר כולל (money, price)" in out2 and "CHOSEN" in out2
              and "13,400 ILS" in out2 and "budget 150,000 ILS" in out2 and "Summary: written" in out2 and "DJ Noa" in out2, out2)
        out = await tool(EVE, "get_knowledge_item", item_id=ori)
        check("tool: get_knowledge_item lists the section, decision and files",
              "Section: צלמים" in out and "Decision: CHOSEN" in out and "הצעת מחיר.pdf" in out, out)
        out = await tool(EVE, "update_section", section_id=sid, summary="x")
        check("tool: a read share can't change a section", out.startswith("Error"), out)

        await web_fixture(c, pid)

        # undo of create_section: an empty one goes; one with entries stays
        out = await tool(ROBIN, "create_section", project_id=pid, title="Cake", kind="notes")
        cake = new_id(out)
        err = await undo_last(ROBIN)
        with get_db_connection() as conn:
            cake_gone = conn.execute("SELECT 1 FROM project_sections WHERE id = ?", (cake,)).fetchone() is None
        check("undo: an empty new section is removed", err == "" and cake_gone, err)
        out = await tool(ROBIN, "create_section", project_id=pid, title="Flowers", kind="notes")
        flowers = new_id(out)
        flowers_undo = UNDOS[-1]
        await tool(ROBIN, "save_knowledge_item", project_id=pid, section=flowers, title="Roses")
        UNDOS[:] = [flowers_undo]
        err = await undo_last(ROBIN)
        check("undo: a section things were saved into is kept", "kept" in err, err)

        # --- save_whatsapp_file ---------------------------------------------------------------------
        waha = FakeWAHA("A", "key-a", {"default": {"id": "972500000001@c.us"}}, ("972500000011@c.us", "Ori"), "Ori")
        await waha.start()
        store.connect(ROBIN, "whatsapp", credentials={"url": waha.url, "api_key": "key-a", "session": "default"},
                      settings={"voice_telegram_chat_id": 1})
        out = await tool(ROBIN, "save_whatsapp_file", chat_id="972500000011@c.us",
                         message_id="false_972500000011@c.us_m3", item_id=ori, note="קובץ")
        with get_db_connection() as conn:
            wa_rows = [dict(r) for r in conn.execute("SELECT name, source, note, size FROM entry_files "
                                                     "WHERE entry_id = ? AND source = 'whatsapp'", (ori,))]
        check("whatsapp: the file is kept on the entry (source whatsapp, named by chat and date)",
              out.startswith("Kept") and len(wa_rows) == 1 and wa_rows[0]["note"] == "קובץ"
              and wa_rows[0]["size"] == len(b"bytes of A") and wa_rows[0]["name"].startswith("Ori"), (out, wa_rows))
        err = await undo_last(ROBIN)
        with get_db_connection() as conn:
            left = conn.execute("SELECT COUNT(*) AS n FROM entry_files WHERE entry_id = ? AND source = 'whatsapp'",
                                (ori,)).fetchone()["n"]
        check("whatsapp: Undo removes the kept file", err == "" and left == 0, err)
        out = await tool(ROBIN, "save_whatsapp_file", chat_id="972500000011@c.us", message_id="nonsense", item_id=ori)
        check("whatsapp: a made-up id is refused", out.startswith("Error"), out)
        out = await tool(ROBIN, "save_whatsapp_file", chat_id="972500000011@c.us",
                         message_id="false_972500000011@c.us_m3", item_id="no-such-entry")
        check("whatsapp: an unknown item is an error", out.startswith("Error"), out)
        await waha.server.close()

        # --- deleting -----------------------------------------------------------------------------
        r = await c.delete(f"/api/sections/{flowers}", headers=auth(EVE))
        check("delete section: a read share is refused", r.status == 403)
        r = await c.delete(f"/api/sections/{sid}", headers=auth(ROBIN))
        with get_db_connection() as conn:
            loose = conn.execute("SELECT COUNT(*) AS n FROM entries WHERE project_id = ? AND section_id IS NULL "
                                 "AND id IN (?, ?, ?)", (pid, ori, ett, kobi)).fetchone()["n"]
        check("delete section: its entries stay, loose", r.status == 200 and loose == 3, loose)
        r = await c.delete(f"/api/sections/{dj}?with_entries=1", headers=auth(BOB))
        with get_db_connection() as conn:
            gone = conn.execute("SELECT COUNT(*) AS n FROM entries WHERE id = ?", (noa,)).fetchone()["n"] == 0
        check("delete section with_entries: its entries go too", r.status == 200 and gone)
        r = await c.delete(f"/api/entries/{ori}", headers=auth(ROBIN))
        with get_db_connection() as conn:
            left = conn.execute("SELECT COUNT(*) AS n FROM entry_files WHERE entry_id = ?", (ori,)).fetchone()["n"]
        check("delete entry: its files go, from the disk too", r.status == 200 and left == 0 and not on_disk.exists())
        form = FormData()
        form.add_field("file", b"abc", filename="a.txt", content_type="text/plain")
        r = await c.post(f"/api/entries/{ett}/files", headers=auth(ROBIN), data=form)
        f2 = (await r.json())["file"]
        with get_db_connection() as conn:
            stored2 = conn.execute("SELECT stored FROM entry_files WHERE id = ?", (f2["id"],)).fetchone()["stored"]
        r = await c.delete(f"/api/projects/{pid}", headers=auth(ROBIN))
        check("delete project: its files leave the disk", r.status == 200
              and not (paths.KNOWLEDGE_FILES_DIR / stored2).exists())
        check("files: the size limit is the module's", files.MAX_BYTES >= 50 * 1024 * 1024)
    await search_and_edits()


async def search_and_edits() -> None:
    """Search (file text, Hebrew prefixes, any-word fallback, snippets), moving an entry between
    projects, renaming / removing a file, merging section columns."""
    pid = new_id(await tool(ROBIN, "create_project", title="דירה"))
    other = new_id(await tool(ROBIN, "create_project", title="Trip"))
    sid = new_id(await tool(ROBIN, "create_section", project_id=pid, title="דירות", kind="options",
                            columns=[{"name": "מחיר", "type": "money"}, {"name": "חדרים", "type": "number"}]))
    eid = new_id(await tool(ROBIN, "save_knowledge_item", project_id=pid, title="Flat on Herzl", section=sid,
                            body="Third floor, a balcony.", decision="shortlist"))
    saved = files.add_file(ROBIN, eid, "lease.pdf", "application/pdf", b"%PDF",
                           transcript="The landlord asks for a guarantee of two months rent.")
    assert saved is not None
    out = await tool(ROBIN, "search_knowledge", query="guarantee")
    check("search: a word only in a file's text finds the entry",
          f"[{eid}]" in out and "lease.pdf" in out and "**guarantee**" in out, out)
    check("search: shows the section and the decision", "section דירות" in out and "shortlisted" in out, out)
    await tool(ROBIN, "save_knowledge_item", project_id=pid, title="חוזה", body="נפגשנו עם המתווך בתל אביב")
    out = await tool(ROBIN, "search_knowledge", query="באביב")
    check("search: a Hebrew word also matches without its prefix letter (באביב → אביב)", "חוזה" in out, out)
    out = await tool(ROBIN, "search_knowledge", query="מתוו")
    check("search: a Hebrew word prefix also matches with a prefix letter (מתוו → המתווך)", "חוזה" in out, out)
    out = await tool(ROBIN, "search_knowledge", query="balcony nonexistentword")
    check("search: no entry has every word → any word", f"[{eid}]" in out, out)
    found = entries.search_entries(ROBIN, "landlord")
    check("search_entries: the snippet, the column and the file of the hit",
          found and found[0]["matched"] == "files" and found[0]["matched_file"] == "lease.pdf"
          and entries.HIT[0] in found[0]["snippet"] and found[0]["files"][0]["id"] == saved["id"], found)

    # files: rename, re-note (undoable), remove (asks first)
    out = await tool(ROBIN, "get_knowledge_item", item_id=eid)
    check("get_knowledge_item: lists file ids", saved["id"] in out, out)
    out = await tool(ROBIN, "update_entry_file", item_id=eid, file="lease.pdf", name="contract.pdf", note="signed")
    row = files.file_row(ROBIN, eid, saved["id"]) or {}
    check("update_entry_file: renames and re-notes by name", row.get("name") == "contract.pdf"
          and row.get("note") == "signed", out)
    check("search: a renamed file is found by its new name",
          any(e["id"] == eid for e in entries.search_entries(ROBIN, "contract")))
    await undo_last(ROBIN)
    check("update_entry_file: undo puts the name back",
          (files.file_row(ROBIN, eid, saved["id"]) or {}).get("name") == "lease.pdf")
    check("update_entry_file: remove asks first, a rename does not",
          registry.needs_confirmation("update_entry_file", {"remove": True})
          and not registry.needs_confirmation("update_entry_file", {"name": "x"}))
    out = await tool(ROBIN, "update_entry_file", item_id=eid, file=saved["id"], remove=True)
    check("update_entry_file: removes by id", files.file_row(ROBIN, eid, saved["id"]) is None, out)
    check("search: a removed file's text is no longer found",
          not any(e["id"] == eid for e in entries.search_entries(ROBIN, "guarantee")))

    # move between projects (section cleared), undo puts it back
    out = await tool(ROBIN, "update_knowledge_item", item_id=eid, project="trip")
    row = entries.entry_row(ROBIN, eid) or {}
    check("update_knowledge_item: moves to another project by title, its section cleared",
          row.get("project_id") == other and row.get("section_id") is None and "Trip" in out, out)
    await undo_last(ROBIN)
    row = entries.entry_row(ROBIN, eid) or {}
    check("update_knowledge_item: undo of a move puts it back with its section",
          row.get("project_id") == pid and row.get("section_id") == sid, row)
    out = await tool(ROBIN, "update_knowledge_item", item_id=eid, project="nowhere")
    check("update_knowledge_item: an unknown project is an error, nothing moves",
          out.startswith("Error") and (entries.entry_row(ROBIN, eid) or {}).get("project_id") == pid, out)
    out = await tool(CARL, "update_knowledge_item", item_id=eid, project=other)
    check("update_knowledge_item: a stranger can't move it", (entries.entry_row(ROBIN, eid) or {}).get("project_id") == pid,
          out)

    # columns merge: rename by key, drop one, add one; the rest stay
    sec = sections.get_section(ROBIN, sid) or {}
    price = next(c_ for c_ in sec["columns"] if c_["name"] == "מחיר")
    await tool(ROBIN, "update_knowledge_item", item_id=eid, fields=[{"name": "מחיר", "type": "money", "value": 5000}])
    await tool(ROBIN, "update_section", section_id=sid, columns=[
        {"key": price["key"], "name": "שכירות"}, {"name": "חדרים", "remove": True}, {"name": "קומה", "type": "number"}])
    sec = sections.get_section(ROBIN, sid) or {}
    check("update_section: columns merge (rename by key keeps type, remove drops, new added)",
          [(c_["name"], c_["type"]) for c_ in sec["columns"]] == [("שכירות", "money"), ("קומה", "number")]
          and sec["columns"][0]["key"] == price["key"], sec.get("columns"))
    fields = (entries.get_entry(ROBIN, eid) or {}).get("fields") or []
    check("update_section: the renamed column's value follows", fields == [{"name": "שכירות", "type": "money",
                                                                          "value": 5000}], fields)
    check("delete_section: the Approve card names the section",
          "דירות" in tools._describe_delete_section(ROBIN, {"section_id": sid}).title)


asyncio.run(main())
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
for n in failed:
    print(f"  FAILED: {n}")
sys.exit(1 if failed else 0)
