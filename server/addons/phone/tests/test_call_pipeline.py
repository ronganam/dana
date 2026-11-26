"""Call pipeline: async upload → transcription → structured analysis →
labels / suggestions → accept (task + link) / dismiss, dedupe, reanalyze,
chat context sessions, audio retention, backfill dry run, FTS triggers,
phone.call.ended once per call (its first analysis, not a reanalysis).

The model is faked at the LiteLLM boundary (litellm.acompletion is patched, so
core/llm.py runs for real); everything else is real:
aiohttp handlers, SQLite schema, background processing.

Run: bin/test test_call_pipeline
"""
import asyncio
import datetime
import json
import os
import sys

from cryptography.fernet import Fernet

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("call_pipeline_test")
os.environ["DANA_INSECURE_DEV"] = "1"  # no API_TOKEN: local development, a request without credentials is the first admin
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # throwaway: encrypts the fake credentials
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ["TRANSCRIBE_MODEL"] = "gemini/gemini-3.1-flash-lite"  # a model that hears audio (LiteLLM is mocked)

import httpx
import litellm
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from core import db, users
from addons import bootstrap as addons_bootstrap
from addons.tasks.groups import ensure_default_groups_for_everyone

db.migrate()
users.bootstrap()
addons_bootstrap.bootstrap()  # the first admin's default addons
testenv.connect_admin("calendar", credentials={"refresh_token": "test-refresh-token"})  # Google is mocked
OWNER = users.first_admin()
ensure_default_groups_for_everyone()
from core import sessions_api
from addons.phone import api as phone_api
from addons.phone.calls import api as calls_api
from addons.tasks import api as tasks_api
from core.auth import setup_auth
from core import clock, context_chats, events, paths
from addons.phone import suggestions as phone_suggestions
from addons.phone.calls import analysis as call_analysis, backfill as call_backfill
from addons.phone.calls import context as call_context, processing as call_processing
from addons.phone.calls import retention as call_retention, transcription as call_transcription
from core.db import get_db_connection
from core.addons import registry

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


# --- Fake model ----------------------------------------------------------------

TOMORROW = (clock.now() + datetime.timedelta(days=1)).date().isoformat()
NEXT_WEEK = (clock.now() + datetime.timedelta(days=7)).date().isoformat()
calls_made = {"transcribe": 0, "analyze": 0, "last_prompt": ""}

ANALYSIS = {
    "title": "תיאום טכנאי מזגנים",
    "summary": ["המזגן קופץ במפסק", "רובין יחפש טכנאי"],  # a list on purpose → normalised to bullets
    "labels": ["home", "family", "bogus", "home"],
    "importance": "normal",
    "is_automated": False,
    "participants": [{"name": "אמא", "role": "Robin's mother"}],
    "key_facts": [{"kind": "money", "text": "ביקור טכנאי 250 ש\"ח"}],
    "action_items": [
        {"text": "למצוא טכנאי מזגנים", "owner": "me", "kind": "task", "due_date": TOMORROW,
         "time_trigger": f"{TOMORROW} 09:00", "location_zone": "Home", "location_event": None,
         "priority": "medium", "group": "Home", "confidence": 0.9, "evidence": "תחפש טכנאי"},
        {"text": "תור לווטרינר", "owner": "me", "kind": "event", "event_start": f"{NEXT_WEEK}T16:15",
         "event_end": None, "priority": "medium", "confidence": 0.95, "evidence": "יום חמישי 16:15"},
        {"text": "אמא תשלח תמונות", "owner": "them", "kind": "info", "priority": "low", "confidence": 0.9},
        {"text": "אולי לקנות מזגן חדש", "owner": "me", "kind": "task", "priority": "low", "confidence": 0.3},
        {"text": "Buy milk", "owner": "me", "kind": "task", "priority": "low", "confidence": 0.9},
    ],
    "project_id": "not-a-project",
    "security_alert": None,
}


def reply(text):
    return litellm.ModelResponse(choices=[{"message": {"role": "assistant", "content": text}}])


async def fake_acompletion(**kwargs):
    content = kwargs["messages"][0]["content"]
    if isinstance(content, list):  # audio → transcription (core.llm.transcribe)
        prompt, audio = content[0]["text"], content[1]["file"]["file_data"]
        calls_made["transcribe"] += 1
        calls_made.setdefault("notes", []).append(prompt)
        if "part 2 of" in prompt and calls_made.get("fail_part2"):
            class TooBig(Exception):
                status_code = 413
            raise TooBig("payload too large")
        silent = "U0lMRU5DRQ" in audio  # base64("SILENCE")
        return reply("" if silent else "אמא: המזגן קופץ. רובין: אני אחפש טכנאי מחר.")
    props = set(kwargs["response_format"]["json_schema"]["schema"]["properties"])
    if props == {"points", "open"}:  # people brief rewritten after a call (addons/people/briefs.py)
        calls_made["briefs"] = calls_made.get("briefs", 0) + 1
        return reply(json.dumps({"points": ["x"], "open": []}))
    calls_made["analyze"] += 1
    calls_made["last_prompt"] = content
    return reply(json.dumps(ANALYSIS, ensure_ascii=False))


litellm.acompletion = fake_acompletion


# The owner's zones (core/zones.py): Home and Acme.
from core import zones as core_zones  # noqa: E402
core_zones.create(OWNER, "Home", 32.08, 34.78, is_home=True)
core_zones.create(OWNER, "Acme", 32.07, 34.79)

# Keep the task title as given (no LLM emoji call) and resolve groups by name.
import addons.tasks.tools as todos
import addons.tasks.titles as task_titles  # noqa: E402


async def plain_title(t):
    return t


async def plain_group(user, group, title):
    return "general"

task_titles.sanitize_task_title = plain_title
task_titles.resolve_task_group = plain_group

events_added = []


def fake_add_event(conn, summary, start_time, end_time, description=None, location=None, attendees=None):
    events_added.append((summary, start_time, end_time))
    return f"Successfully added event '{summary}' to your personal calendar with ID: evt123."

import addons.calendar.tools as cal  # noqa: E402
cal.add_event = fake_add_event


def build_app():
    app = web.Application()
    setup_auth(app)  # no API_TOKEN: a request without credentials is the first admin
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
    r.add_put("/api/suggestions/{id}", phone_api.update_suggestion_api)
    r.add_post("/api/suggestions/{id}/accept", phone_api.accept_suggestion_api)
    r.add_post("/api/suggestions/{id}/dismiss", phone_api.dismiss_suggestion_api)
    r.add_post("/api/chat/context", sessions_api.start_context_chat_api)
    r.add_get("/api/tasks", tasks_api.get_tasks_api)
    r.add_get("/api/sessions/{session_id}", sessions_api.get_session_detail_api)
    return app


async def wait_ready(client, call_id, timeout=10):
    for _ in range(int(timeout * 20)):
        r = await client.get(f"/api/calls/{call_id}")
        data = await r.json()
        if data.get("status") in ("ready", "failed"):
            return data
        await asyncio.sleep(0.05)
    return data


def upload_form(content: bytes, name="Call אמא_261108_114129.m4a", **fields):
    form = FormData()
    an_hour_ago = (clock.now() - datetime.timedelta(hours=1)).replace(tzinfo=None).isoformat(timespec="seconds")
    for k, v in {"caller": "אמא", "receiver": "Me", "call_timestamp": an_hour_ago, **fields}.items():
        form.add_field(k, str(v))
    form.add_field("audio", content, filename=name, content_type="audio/mp4")
    return form


ENDED = []  # phone.call.ended data, as a tap sees it


async def on_event(name, user, data):
    if name == "phone.call.ended":
        ENDED.append((user.id, data))


async def ended_for(call_id, timeout=5.0):
    """The phone.call.ended events of ``call_id`` (waiting a little for the one emitted after ready)."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not [d for _, d in ENDED if d["call_id"] == call_id] and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.02)
    await events.drain()
    return [(u, d) for u, d in ENDED if d["call_id"] == call_id]


async def main():
    paths.ensure_data_dirs()
    events.tap(on_event)
    client = TestClient(TestServer(build_app()))
    await client.start_server()
    try:
        # --- upload is async (202) and gets processed in the background
        r = await client.post("/api/calls/upload", data=upload_form(b"AUDIO-1", duration_seconds=95,
                                                                      direction="incoming", phone_number="0501234567"))
        up = await r.json()
        check("upload answers 202 processing", r.status == 202 and up["status"] == "processing", f"{r.status} {up}")
        call_id = up["id"]
        call = await wait_ready(client, call_id)
        check("call becomes ready", call["status"] == "ready", call.get("error"))
        ended = await ended_for(call_id)
        check("phone.call.ended once, for its owner, with the call's data",
              len(ended) == 1 and ended[0][0] == OWNER.id
              and ended[0][1] == {"call_id": call_id, "person": call["caller"], "direction": "incoming",
                                  "duration_s": 95, "title": "תיאום טכנאי מזגנים", "summary": call["summary"]},
              (ended, call["caller"]))
        check("transcribed once", calls_made["transcribe"] == 1)
        check("labels cleaned (known, deduped)", call["labels"] == ["home", "family"], call["labels"])
        check("label details included", call["label_details"][0]["emoji"] == "🏠")
        check("summary normalised to bullets", call["summary"].startswith("- "), call["summary"])
        check("title from analysis", call["title"] == "תיאום טכנאי מזגנים")
        check("direction + number stored", call["direction"] == "incoming" and call["phone_number"] == "0501234567")
        check("duration kept", call["duration_seconds"] == 95)
        check("invalid project dropped", call["project_id"] is None)
        check("prompt has contact + direction", "אמא" in calls_made["last_prompt"] and "incoming call" in calls_made["last_prompt"])
        check("prompt lists zones", "Acme" in calls_made["last_prompt"])

        sugg = call["suggestions"]
        kinds = sorted(s["kind"] for s in sugg)
        check("suggestions: task + event only (no 'them', no low confidence)", kinds == ["event", "task", "task"] or kinds == ["event", "task"],
              f"{[(s['kind'], s['payload']['title']) for s in sugg]}")
        task_s = next(s for s in sugg if s["payload"]["title"] == "למצוא טכנאי מזגנים")
        check("task suggestion carries triggers", task_s["payload"]["triggers"].get("time") == f"{TOMORROW} 09:00"
              and task_s["payload"]["triggers"].get("location") == {"location": "home", "event": "arrive"},
              task_s["payload"]["triggers"])
        check("task suggestion due date + group", task_s["payload"]["due_date"] == TOMORROW and task_s["payload"]["group"] == "Home")

        # --- an old recording (a backlog of past calls): only what is still ahead
        month_ago = (clock.now() - datetime.timedelta(days=30)).replace(tzinfo=None).isoformat(timespec="seconds")
        r = await client.post("/api/calls/upload", data=upload_form(b"AUDIO-OLD", name="Call אמא_old.m4a",
                                                                      call_timestamp=month_ago, duration_seconds=95))
        old = await wait_ready(client, (await r.json())["id"])
        old_titles = sorted(s["payload"]["title"] for s in old["suggestions"])
        check("old call: only suggestions still ahead (the dated task and event, no undated 'Buy milk')",
              old["status"] == "ready" and "Buy milk" not in old_titles
              and old_titles == sorted(["למצוא טכנאי מזגנים", "תור לווטרינר"]), old_titles)
        with get_db_connection() as conn:  # out of the way of the pending-suggestion checks below
            conn.execute("DELETE FROM call_suggestions WHERE call_id = ?", (old["id"],))
            conn.execute("DELETE FROM phone_calls WHERE id = ?", (old["id"],))
            conn.commit()
        check("pending count on call", call["pending_suggestions"] == len(sugg))

        # --- duplicate upload returns the same call
        r = await client.post("/api/calls/upload", data=upload_form(b"AUDIO-1", name="other.m4a"))
        dup = await r.json()
        check("same bytes → duplicate", dup.get("duplicate") and dup["id"] == call_id)
        r = await client.post("/api/calls/upload", data=upload_form(b"AUDIO-2"))
        dup2 = await r.json()
        check("same file name → duplicate", dup2.get("duplicate") and dup2["id"] == call_id)

        # --- suggestions list (welcome area)
        r = await client.get("/api/suggestions")
        pending = await r.json()
        check("pending suggestions listed with call info", len(pending) == len(sugg) and pending[0]["call"]["contact"] == "אמא")

        # --- edit then accept task suggestion → task + link
        r = await client.put(f"/api/suggestions/{task_s['id']}", json={"priority": "high"})
        check("suggestion edit", (await r.json())["payload"]["priority"] == "high")
        r = await client.post(f"/api/suggestions/{task_s['id']}/accept", json={"title": "להזמין טכנאי"})
        acc = await r.json()
        check("accept creates task", r.status == 200 and acc.get("task_id"), acc)
        with get_db_connection() as conn:
            t = conn.execute("SELECT * FROM tasks WHERE id = ?", (acc["task_id"],)).fetchone()
        check("task has edited title + priority + triggers",
              t["title"] == "להזמין טכנאי" and t["priority"] == "high" and json.loads(t["triggers"]).get("time"))
        r = await client.get("/api/tasks")
        tasks = await r.json()
        tl = next(x for x in tasks if x["id"] == acc["task_id"])
        check("GET /api/tasks returns call link", tl["links"] and tl["links"][0]["ref_type"] == "call"
              and tl["links"][0]["ref_id"] == call_id and tl["links"][0]["contact"] == "אמא", tl["links"])
        r = await client.post(f"/api/suggestions/{task_s['id']}/accept")
        check("accepting twice is idempotent", (await r.json()).get("already"))

        # --- accept event suggestion → calendar
        ev = next(s for s in sugg if s["kind"] == "event")
        r = await client.post(f"/api/suggestions/{ev['id']}/accept")
        check("event accept calls calendar", r.status == 200 and events_added and events_added[0][1].startswith(NEXT_WEEK),
              f"{r.status} {events_added}")

        # --- dismiss
        rest = [s for s in sugg if s["id"] not in (task_s["id"], ev["id"])]
        for s in rest:
            await client.post(f"/api/suggestions/{s['id']}/dismiss")
        r = await client.get("/api/suggestions")
        check("nothing pending after accept/dismiss", (await r.json()) == [])

        # --- call detail lists linked tasks
        r = await client.get(f"/api/calls/{call_id}")
        detail = await r.json()
        check("call detail lists linked task", any(x["id"] == acc["task_id"] for x in detail["tasks"]))

        # --- manual label edit
        r = await client.put(f"/api/calls/{call_id}", json={"labels": ["wedding", "nope"]})
        check("label edit", (await r.json())["labels"] == ["wedding"])
        r = await client.get("/api/calls?label=wedding&transcripts=0")
        lst = await r.json()
        check("label filter + no transcripts", len(lst) == 1 and "transcript" not in lst[0])

        # --- context chat session from a suggestion
        call_processing.apply_analysis(OWNER, call_id, call_analysis.normalize_analysis(
            ANALYSIS, await call_context.gather_context(OWNER)))
        new_pending = phone_suggestions.list_suggestions(OWNER, "pending", call_id=call_id)
        check("reanalysis recreates pending suggestions (accepted kept)", len(new_pending) >= 1)
        sid = new_pending[0]["id"]
        r = await client.post("/api/chat/context", json={"kind": "suggestion", "id": sid})
        ctx = await r.json()
        check("context session created", r.status == 200 and ctx["session_id"] and ctx["context"]["quick_prompts"], ctx)
        r = await client.get(f"/api/sessions/{ctx['session_id']}")
        state = await r.json()
        check("session state carries context card", state.get("context", {}).get("ref_id") == sid)
        prompt = context_chats.session_context_prompt(users.first_admin(), ctx["session_id"])
        check("agent context names the suggestion + call", sid in prompt and call_id in prompt and "accept_call_suggestion" in prompt)
        r = await client.post("/api/chat/context", json={"kind": "call", "id": call_id})
        check("context session from a call", r.status == 200)
        r = await client.post("/api/chat/context", json={"kind": "nope", "id": "x"})
        check("bad context kind → 400", r.status == 400)

        # --- agent tools
        out = await registry.execute(OWNER, "accept_call_suggestion", {"suggestion_id": sid[:8], "time_trigger": f"{NEXT_WEEK} 10:00"})
        check("tool accepts by id prefix", "Created task" in out or "calendar" in out, out)
        out = await registry.execute(OWNER, "list_call_suggestions", {"status": "accepted"})
        check("tool lists accepted", "accepted" in out)
        out = await registry.execute(OWNER, "add_task", {"title": "Call the technician back", "call_id": call_id})
        check("add_task links to call", "linked to call" in out, out)
        out = await registry.execute(OWNER, "get_call_transcript", {"call_id": call_id})
        check("transcript tool shows linked tasks", "Tasks from this call" in out)
        out = await registry.execute(OWNER, "list_recent_calls", {"label": "home"})
        check("list_recent_calls label filter", call_id in out)

        # --- silent recording → failed, no analysis call
        before = calls_made["analyze"]
        r = await client.post("/api/calls/upload", data=upload_form(b"SILENCE", name="Call x_261108_120000.m4a"))
        silent = await wait_ready(client, (await r.json())["id"])
        check("silent call → failed", silent["status"] == "failed" and calls_made["analyze"] == before, silent.get("error"))

        # --- reanalyze endpoint is async
        r = await client.post(f"/api/calls/{call_id}/reanalyze", json={"custom_instructions": "focus on money"})
        check("reanalyze → 202", r.status == 202)
        again = await wait_ready(client, call_id)
        check("reanalyze finishes + used instructions", again["status"] == "ready" and "focus on money" in calls_made["last_prompt"])
        await asyncio.sleep(0.1)
        check("a reanalysis does not emit phone.call.ended again", len(await ended_for(call_id, timeout=0)) == 1)
        check("a silent (failed) call emits no phone.call.ended", not await ended_for(silent["id"], timeout=0))

        # --- retention: old upload loses audio, keeps transcript; API says 410
        with get_db_connection() as conn:
            conn.execute("UPDATE phone_calls SET created_at = '2020-01-01 00:00:00' WHERE id = ?", (call_id,))
            conn.commit()
        removed = call_retention.purge_old_audio(OWNER, 90)
        r = await client.get(f"/api/calls/{call_id}/audio")
        detail = await (await client.get(f"/api/calls/{call_id}")).json()
        check("retention deletes audio, keeps transcript", removed == 1 and r.status == 410 and detail["transcript"]
              and not detail["audio_available"], f"{removed} {r.status}")

        # --- delete removes suggestions + links
        r = await client.delete(f"/api/calls/{call_id}")
        with get_db_connection() as conn:
            left = conn.execute("SELECT COUNT(*) FROM call_suggestions WHERE call_id = ?", (call_id,)).fetchone()[0]
            links = conn.execute("SELECT COUNT(*) FROM task_links WHERE ref_id = ?", (call_id,)).fetchone()[0]
            fts = conn.execute("SELECT COUNT(*) FROM phone_calls_fts WHERE id = ?", (call_id,)).fetchone()[0]
        check("delete cleans suggestions, links and FTS", r.status == 200 and left == 0 and links == 0 and fts == 0)

        # --- backfill dry run on legacy rows
        with get_db_connection() as conn:
            conn.execute("INSERT INTO phone_calls (id, user_id, filename, caller, receiver, call_timestamp, transcript, summary, title) "
                         "VALUES ('legacy1', ?, 'x_a.m4a', 'נועה', 'Me', '2026-07-01T10:00', 'נועה: צריך להזמין אולם. רובין: אני אתקשר מחר.', \"['a', 'b']\", 'Call Recording')", (OWNER.id,))
            conn.execute("INSERT INTO phone_calls (id, user_id, filename, caller, receiver, call_timestamp, transcript, summary, title) "
                         "VALUES ('legacy2', ?, 'x_b.m4a', 'נועה', 'Me', '2026-07-01T10:00', '', 'No summary', 'Call with נועה')", (OWNER.id,))
            conn.execute("INSERT INTO phone_calls (id, user_id, filename, caller, receiver, call_timestamp, transcript, summary, title) "
                         "VALUES ('legacy3', ?, 'x_c.m4a', 'דור', 'Me', '2026-07-02T10:00', 'דור: נתראה בערב, אני אביא את הכלבה איתי.', '- good summary', 'Good title')", (OWNER.id,))
            conn.commit()
        rep = await call_backfill.run(OWNER, apply=False, redo=False, limit=None)
        by = {c["id"]: c for c in rep["calls"]}
        check("backfill marks empty twin as duplicate", by["legacy2"]["result"] == "duplicate")
        check("backfill regenerates broken text only", by["legacy1"]["regenerated_text"] and not by["legacy3"]["regenerated_text"])
        with get_db_connection() as conn:
            untouched = conn.execute("SELECT analysis FROM phone_calls WHERE id = 'legacy1'").fetchone()[0]
        check("dry run writes nothing", untouched is None)
        rep = await call_backfill.run(OWNER, apply=True, redo=False, limit=None)
        with get_db_connection() as conn:
            l1 = conn.execute("SELECT * FROM phone_calls WHERE id = 'legacy1'").fetchone()
            l3 = conn.execute("SELECT * FROM phone_calls WHERE id = 'legacy3'").fetchone()
            l2 = conn.execute("SELECT status FROM phone_calls WHERE id = 'legacy2'").fetchone()
        check("apply: labels + new title on broken row", json.loads(l1["labels"]) and l1["title"] == ANALYSIS["title"])
        check("apply: good title kept", l3["title"] == "Good title" and l3["analysis"])
        check("apply: duplicate status", l2["status"] == "duplicate")
        r = await client.get("/api/calls")
        check("duplicates hidden from list", all(c["id"] != "legacy2" for c in await r.json()))
        # --- long recordings: split into chunks, one bad chunk doesn't lose the call
        import subprocess
        long_path = testenv.scratch_path("long.m4a")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "sine=frequency=300:duration=150", "-c:a", "aac", "-b:a", "32k", long_path], check=True)
        saved = (call_transcription.TRANSCRIBE_DIRECT_MAX_SECONDS, call_transcription.TRANSCRIBE_CHUNK_SECONDS)
        call_transcription.TRANSCRIBE_DIRECT_MAX_SECONDS, call_transcription.TRANSCRIBE_CHUNK_SECONDS = 30, 60
        try:
            before = calls_made["transcribe"]
            calls_made["notes"] = []
            text = await call_transcription.transcribe_call_audio(long_path, "audio/mp4", 150)
            check("long call split into 3 parts", calls_made["transcribe"] - before == 3
                  and all("of 3 of one long phone call" in n for n in calls_made["notes"]))
            check("parts joined in order", text.count("טכנאי") == 3)
            calls_made["fail_part2"] = True
            text = await call_transcription.transcribe_call_audio(long_path, "audio/mp4", 150)
            check("failed part marked, rest kept", "[1:00–2:00 could not be transcribed]" in text
                  and text.count("טכנאי") == 2)
            calls_made["fail_part2"] = False
            short = await call_transcription.transcribe_call_audio(long_path, "audio/mp4", 20)
            check("short call sent whole", short.count("טכנאי") == 1)
        finally:
            call_transcription.TRANSCRIBE_DIRECT_MAX_SECONDS, call_transcription.TRANSCRIBE_CHUNK_SECONDS = saved
        fresh = clock.now().replace(tzinfo=None).isoformat(timespec="seconds")
        old_ts = (clock.now() - datetime.timedelta(days=2)).replace(tzinfo=None).isoformat(timespec="seconds")
        check("pop-up only for calls that just happened",
              call_processing.recent_call({"call_timestamp": fresh}) and not call_processing.recent_call({"call_timestamp": old_ts})
              and not call_processing.recent_call({}))
        check("timeout errors are named", call_transcription.err_text(httpx.ReadTimeout("")) == "ReadTimeout")
    finally:
        events.untap(on_event)
        await client.close()

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
