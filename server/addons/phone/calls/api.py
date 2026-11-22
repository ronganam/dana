"""HTTP routes for call recordings (docs/calls.md).

- ``POST /api/calls/upload`` (multipart) → 202, processed in the background
- ``GET /api/calls``, ``GET /api/calls/labels``
- ``GET|PUT|DELETE /api/calls/{id}``, ``GET /api/calls/{id}/audio``, ``POST /api/calls/{id}/reanalyze``

Every route answers for the request's user, who must have ``phone`` (else 403
``not_connected``); another user's call id is 404 like an unknown one.
"""

import json
import logging
import os
from typing import Any, Dict

from aiohttp import web

from core import budget, paths
from addons.knowledge import projects as knowledge_projects
from addons.phone import suggestions as phone_suggestions
from addons.phone.access import phone_user
from addons.phone.calls import (
    labels as calls_labels,
    processing as calls_processing,
    store as calls_store,
    upload as calls_upload,
)
from addons.tasks import service as tasks_service

logger = logging.getLogger(__name__)


RECORDINGS_DIR = str(paths.RECORDINGS_DIR)


async def upload_call_api(request: web.Request) -> web.Response:
    """POST /api/calls/upload (multipart) → 202 {id, status: "processing"}

    Stores the audio as the caller's call and answers right away;
    transcription + analysis run in the background, as the caller
    (addons/phone/calls/processing.py). Poll GET /api/calls/{id} for the
    result. Re-uploading the same recording (same bytes or same original file
    name, among the caller's calls) returns the existing call with
    ``duplicate: true``.

    Fields: audio|file, caller, receiver, call_timestamp, duration_seconds,
    direction (incoming|outgoing|missed), phone_number, instructions.
    """
    user = phone_user(request)
    try:
        reader = await request.multipart()

        audio_bytes = bytearray()
        filename = "recording.mp3"
        mime_type = "audio/mp3"
        fields: Dict[str, str] = {}

        while True:
            field = await reader.next()
            if not field:
                break
            if field.name in ("audio", "file"):
                filename = field.filename or "recording.mp3"
                mime_type = field.headers.get("Content-Type", "audio/mp3")
                while True:
                    chunk = await field.read_chunk()
                    if not chunk:
                        break
                    audio_bytes.extend(chunk)
            elif field.name:
                fields[field.name] = (await field.text()).strip()

        audio_bytes = bytes(audio_bytes)
        if not audio_bytes:
            return web.json_response({"error": "No audio file provided"}, status=400)

        call_id, duplicate = await calls_upload.receive(user, audio_bytes, filename, mime_type, fields)
        if duplicate:
            return web.json_response({**calls_store.call_payload(user, call_id), "duplicate": True})
        return web.json_response(calls_store.call_payload(user, call_id), status=202)
    except Exception as e:
        logger.error(f"API Error uploading call: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def get_calls_api(request: web.Request) -> web.Response:
    """GET /api/calls?label=&include_duplicates=1&transcripts=0 — the caller's calls."""
    user = phone_user(request)
    try:
        return web.json_response(calls_store.list_calls(
            user, label=(request.query.get("label") or "").strip(),
            include_duplicates=request.query.get("include_duplicates") in ("1", "true"),
            include_transcripts=request.query.get("transcripts", "1") not in ("0", "false")))
    except Exception as e:
        logger.error(f"API Error listing calls: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def get_call_labels_api(request: web.Request) -> web.Response:
    """GET /api/calls/labels — the label catalog."""
    return web.json_response(calls_labels.labels_catalog(phone_user(request)))


async def get_call_detail_api(request: web.Request) -> web.Response:
    """GET /api/calls/{id} — the caller's call with its suggestions and linked tasks."""
    user = phone_user(request)
    call_id = request.match_info.get("id")
    payload = calls_store.call_payload(user, call_id)
    if payload.get("status") == "missing":
        return web.json_response({"error": "Call not found"}, status=404)
    payload["suggestions"] = phone_suggestions.list_suggestions(user, None, call_id=call_id)
    payload["tasks"] = tasks_service.tasks_for_call(user, call_id)
    return web.json_response(payload)


async def update_call_api(request: web.Request) -> web.Response:
    """PUT /api/calls/{id} {labels?, project_id?, title?, importance?}

    ``project_id`` must be a project the caller can see (else 404 "Project not found")."""
    user = phone_user(request)
    call_id = request.match_info.get("id")
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    fields: Dict[str, Any] = {}
    if "labels" in data:
        fields["labels"] = json.dumps(calls_labels.clean_labels(data.get("labels")))
    if "project_id" in data:
        project_id = data.get("project_id") or None
        if project_id and not knowledge_projects.access(user, project_id):
            return web.json_response({"error": "Project not found"}, status=404)
        fields["project_id"] = project_id
    if (data.get("title") or "").strip():
        fields["title"] = data["title"].strip()
    if data.get("importance") in ("low", "normal", "high"):
        fields["importance"] = data["importance"]
    if not fields:
        return web.json_response({"error": "Nothing to update"}, status=400)
    if not calls_store.update_fields(user, call_id, fields):
        return web.json_response({"error": "Call not found"}, status=404)
    return web.json_response(calls_store.call_payload(user, call_id, include_transcript=False))


async def get_call_audio_api(request: web.Request) -> web.Response:
    """GET /api/calls/{id}/audio — the caller's recording; 410 once retention removed the file."""
    user = phone_user(request)
    call_id = request.match_info.get("id")
    try:
        row = calls_store.get_row(user, call_id)
        if not row:
            return web.Response(text="Call recording not found in database", status=404)

        filename = row["filename"]
        file_path = os.path.join(RECORDINGS_DIR, filename)
        if not os.path.exists(file_path):
            if row["audio_deleted_at"]:
                return web.Response(text="Audio expired (kept 90 days); the transcript is still available", status=410)
            return web.Response(text="Audio file not found on disk", status=404)

        ext = os.path.splitext(filename)[1].lower()
        audio_type = {".m4a": "audio/mp4", ".wav": "audio/wav", ".mp3": "audio/mpeg",
                      ".ogg": "audio/ogg", ".aac": "audio/aac"}.get(ext, "application/octet-stream")
        return web.FileResponse(file_path, headers={"Content-Type": audio_type})
    except Exception as e:
        logger.error(f"API Error fetching audio for call {call_id}: {e}", exc_info=True)
        return web.Response(text=str(e), status=500)


async def delete_call_api(request: web.Request) -> web.Response:
    """DELETE /api/calls/{id} — the caller's call, its suggestions, task links and recording."""
    user = phone_user(request)
    call_id = request.match_info.get("id")
    try:
        if not calls_store.delete_call(user, call_id):
            return web.json_response({"error": "Call recording not found"}, status=404)
        return web.json_response({"status": "deleted", "id": call_id})
    except Exception as e:
        logger.error(f"API Error deleting call {call_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def reanalyze_call_api(request: web.Request) -> web.Response:
    """POST /api/calls/{id}/reanalyze {custom_instructions?} → 202

    Re-runs the structured analysis of the caller's call in the background, as
    them (re-transcribing first when the stored transcript is unusable and the
    audio still exists). Pending suggestions of the call are replaced;
    accepted ones are kept.
    """
    user = phone_user(request)
    call_id = request.match_info.get("id")
    try:
        body = await request.json() if request.can_read_body else {}
    except Exception:
        body = {}
    custom_instructions = (body.get("custom_instructions") or "").strip()
    try:
        with budget.user_started():  # asked for now: the monthly budget does not hold it back
            calls_processing.request_reanalysis(user, call_id, custom_instructions)
    except LookupError as e:
        return web.json_response({"error": str(e)}, status=404)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response(calls_store.call_payload(user, call_id), status=202)


def setup(app: web.Application) -> None:
    """Register the calls routes."""
    app.router.add_post("/api/calls/upload", upload_call_api)
    app.router.add_get("/api/calls", get_calls_api)
    app.router.add_get("/api/calls/labels", get_call_labels_api)
    app.router.add_get("/api/calls/{id}", get_call_detail_api)
    app.router.add_put("/api/calls/{id}", update_call_api)
    app.router.add_get("/api/calls/{id}/audio", get_call_audio_api)
    app.router.add_delete("/api/calls/{id}", delete_call_api)
    app.router.add_post("/api/calls/{id}/reanalyze", reanalyze_call_api)
