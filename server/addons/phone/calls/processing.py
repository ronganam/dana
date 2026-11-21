"""One call from upload to ready, as its owner.

Flow for an upload (``POST /api/calls/upload``, addons/phone/calls/upload.py):

1. The server stores the audio and inserts a ``phone_calls`` row of the
   request's user with ``status='processing'`` and answers right away (202).
2. :func:`schedule_processing` runs :func:`process_call` in the background,
   as the call's owner (``users.acting_as``: usage rows are theirs):
   transcribe (``core.llm``, audio inline) → ``analysis.analyze_transcript``
   (one structured model call, with the owner's own context) →
   :func:`apply_analysis` (stores it and turns action items into the owner's
   ``call_suggestions``) → people linking and the pop-up to the owner's phone.
3. Clients poll ``GET /api/calls/{id}`` until ``status`` is ``ready``/``failed``
   and show the pending suggestions (welcome area, Debrief screen).

``CALL_PROCESSING_CONCURRENCY`` calls are processed at once for the whole
server. Calls left in ``processing`` by a restart resume at startup, per user
with ``phone`` (:func:`resume_unfinished`).
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
from typing import Any, Dict, List, Optional

from core import clock, users
from core.db import get_db_connection
from core.users import User
from addons.people import briefs as people_briefs
from addons.phone import popups as phone_popups
from addons.phone.calls import store as calls_store
from addons.phone.calls.analysis import analyze_transcript
from addons.phone.calls.suggest import replace_pending_suggestions, suggestions_from_analysis
from addons.phone.calls.transcription import audio_duration_seconds, err_text, transcribe_call_audio
from addons.phone.calls.values import FAILED_TRANSCRIPT_PREFIX, PROCESSING_CONCURRENCY, transcript_usable

logger = logging.getLogger(__name__)

_processing = asyncio.Semaphore(PROCESSING_CONCURRENCY)
_scheduled: set = set()
_tasks: set = set()


def recent_call(call: Dict[str, Any]) -> bool:
    """A call that just happened (not a backfill or a recording recovered days later)."""
    try:
        when = datetime.datetime.fromisoformat(str(call.get("call_timestamp") or call.get("when") or ""))
    except ValueError:
        return False
    now = clock.now()
    when = when.replace(tzinfo=now.tzinfo) if when.tzinfo is None else when
    return now - when <= datetime.timedelta(hours=phone_popups.SUGGESTION_POPUP_MAX_AGE_HOURS)


def apply_analysis(user: User, call_id: str, analysis: Dict[str, Any], *, replace_text: bool = True,
                   create_suggestions: bool = True, only_future: bool = False) -> int:
    """Store an analysis on ``user``'s call; returns the number of suggestions made (0: not their call)."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM phone_calls WHERE id = ? AND user_id = ?", (call_id, user.id)).fetchone()
        if not row:
            return 0
        call = calls_store.call_dict(row)
        sets = ["analysis = ?", "labels = ?", "importance = ?", "status = 'ready'", "error = NULL"]
        params: List[Any] = [json.dumps(analysis, ensure_ascii=False), json.dumps(analysis.get("labels") or []),
                             analysis.get("importance") or "normal"]
        if analysis.get("project_id"):
            sets.append("project_id = ?")
            params.append(analysis["project_id"])
        if replace_text:
            sets += ["title = ?", "summary = ?"]
            params += [analysis.get("title") or row["title"], analysis.get("summary") or row["summary"]]
        params.append(call_id)
        # scoped: the user's call, checked above (id AND user_id)
        conn.execute(f"UPDATE phone_calls SET {', '.join(sets)} WHERE id = ?", params)
        count = 0
        if create_suggestions:
            count = replace_pending_suggestions(conn, user, call_id, suggestions_from_analysis(
                user, call, analysis, only_future=only_future))
        conn.commit()
    return count


async def process_call(call_id: str, *, custom_instructions: Optional[str] = None) -> None:
    """Transcribe (if needed) and analyse one call, as its owner. Never raises."""
    async with _processing:
        try:
            owner = calls_store.owner_of(call_id)
            if owner is None:
                return
            with users.acting_as(owner):
                await _process(owner, call_id, custom_instructions)
        except Exception as e:
            logger.error(f"Processing call {call_id} failed: {e}", exc_info=True)
            try:
                calls_store.set_status(call_id, "failed", str(e)[:500])
            except Exception:
                pass
        finally:
            _scheduled.discard(call_id)


async def _process(owner: User, call_id: str, custom_instructions: Optional[str]) -> None:
    row = calls_store.get_row(owner, call_id)
    if not row:
        return
    call = calls_store.call_dict(row)
    path = calls_store.audio_path(row)
    have_audio = os.path.exists(path)

    if have_audio and not int(row["duration_seconds"] or 0):
        dur = await asyncio.to_thread(audio_duration_seconds, path)
        if dur:
            call["duration_seconds"] = dur
            calls_store.set_status(call_id, "processing", None, duration_seconds=dur)

    transcript = row["transcript"] or ""
    if not transcript_usable(transcript) and have_audio:
        mime = row["mime_type"] or "audio/mp4"
        if mime in ("audio/*", "application/octet-stream", "audio/m4a", "audio/x-m4a"):
            mime = "audio/mp4"
        try:
            transcript = await transcribe_call_audio(path, mime, int(call.get("duration_seconds") or 0),
                                                     ref=f"call:{call_id}")
        except Exception as e:
            logger.error(f"Transcription failed for call {call_id}: {err_text(e)}")
            transcript = f"{FAILED_TRANSCRIPT_PREFIX}: {err_text(e)}]"
        with get_db_connection() as conn:
            # scoped: calls_store.get_row(owner, call_id) checked above
            conn.execute("UPDATE phone_calls SET transcript = ? WHERE id = ?", (transcript, call_id))
            conn.commit()

    if not transcript_usable(transcript):
        who = call["contact"]
        calls_store.set_status(call_id, "failed", "No speech in the recording" if not transcript.strip()
                               else transcript[:300],
                               title=row["title"] if row["title"] and row["title"] != "Processing…" else f"Call with {who}",
                               summary="No summary available (audio is silent, empty, or transcription failed).")
        return

    instructions = custom_instructions if custom_instructions is not None else (row["instructions"] or "")
    analysis = await analyze_transcript(owner, transcript, call, custom_instructions=instructions)
    count = apply_analysis(owner, call_id, analysis)
    logger.info(f"Call {call_id} analysed: labels={analysis['labels']} suggestions={count}")
    people_briefs.after_call(owner, call_id)
    if count and recent_call(call):
        await phone_popups.announce_suggestions(owner, "call", call_id, f"From the call with {call['contact']}")


def schedule_processing(call_id: str, custom_instructions: Optional[str] = None) -> bool:
    """Run :func:`process_call` in the background (once per call at a time)."""
    if call_id in _scheduled:
        return False
    _scheduled.add(call_id)
    task = asyncio.create_task(process_call(call_id, custom_instructions=custom_instructions))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


def request_reanalysis(user: User, call_id: str, custom_instructions: str) -> None:
    """Re-run the analysis of ``user``'s call in the background (re-transcribing
    first when the stored transcript is unusable and the audio still exists).

    Raises:
        LookupError: not their call.
        ValueError: the call has no transcript or audio to analyze.
    """
    row = calls_store.get_row(user, call_id)
    if not row:
        raise LookupError("Call recording not found")
    if not transcript_usable(row["transcript"]) and not calls_store.audio_available(row):
        raise ValueError("Call has no transcript or audio to analyze.")
    calls_store.update_fields(user, call_id, {"status": "processing", "error": None,
                                              "instructions": custom_instructions})
    schedule_processing(call_id, custom_instructions=custom_instructions)


def resume_unfinished(user: User) -> int:
    """Re-queue ``user``'s calls left in 'processing' by a restart."""
    with get_db_connection() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM phone_calls WHERE status = 'processing' AND user_id = ?",
                                             (user.id,)).fetchall()]
    for cid in ids:
        schedule_processing(cid)
    if ids:
        logger.info(f"Resumed processing of {len(ids)} call(s) for user {user.id}")
    return len(ids)
