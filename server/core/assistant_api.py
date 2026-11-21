"""The assistant routes: a message (text, images, voice) starts a run and is
answered as SSE (core/sessions_api.py relays it), plus approvals, the model
picker and the chat's images (docs/agent-actions.md, the stream contract in
core/runs.py).

- ``GET /api/assistant/models`` — the model picker catalog for the caller's level
- ``POST /api/assistant/message``, ``POST /api/assistant/message_with_image`` (multipart),
  ``POST /api/assistant/voice`` (multipart) → SSE
- ``GET /api/assistant/voice_stream`` (WebSocket), ``GET /api/assistant/realtime`` (WebSocket, core/realtime.py)
- ``POST /api/assistant/approve {session_id, action, selection?}`` → SSE (``cancel`` → JSON)
- ``GET /api/images/{filename}`` — an image saved for the caller
"""

import base64
import json
import logging
from typing import Any, Dict, List, Optional

import aiohttp
from aiohttp import web

from core import device_screen, llm
from core.auth import request_user
from core.images import get_image_path, save_image
from core.models import catalog_payload, resolve_profile
from core.realtime import assistant_realtime_ws_api
from core.runs import SessionBusyError, runs
from core.session import get_pending, set_session_status
from core.session_runner import (
    cancel_agent_task,
    normalize_screen,
    start_agent_task,
    start_resume_selection_task,
    start_resume_task,
)
from core.sessions_api import SSE_HEARTBEAT_SECONDS, build_state_payload, not_found, relay_run, session_of
from core.users import User
from core.voice import create_wav_header

logger = logging.getLogger(__name__)


def _busy_response(err: SessionBusyError) -> web.Response:
    """409 for a message sent while the session's previous run is still going."""
    return web.json_response({
        "error": "This chat is still working on the previous request.",
        "code": "session_busy",
        "active_run": err.run.info(),
    }, status=409)


def _requested_model_id(user: User, value: Optional[str]) -> str:
    """Resolve the client's model choice to a catalog id the user's access
    level may use (unknown, not allowed or empty → the level's default)."""
    return resolve_profile((value or "").strip() or None, user.level).id


# A multipart device_screen field is read up to this size (then ignored as invalid JSON).
DEVICE_SCREEN_FIELD_MAX_BYTES = 512 * 1024


def _requested_screen(data: Any = None, query: Any = None) -> Optional[Dict[str, str]]:
    """The item on the user's screen: ``screen: {kind, id}`` in a JSON body
    (or a JSON string in a form field), or ``screen_kind`` + ``screen_id`` in
    the query string. None when absent or invalid."""
    screen = (data or {}).get("screen") if isinstance(data, dict) else None
    if isinstance(screen, str):
        try:
            screen = json.loads(screen)
        except ValueError:
            screen = None
    if not screen and query is not None and query.get("screen_kind"):
        screen = {"kind": query.get("screen_kind"), "id": query.get("screen_id")}
    return normalize_screen(screen)


def _requested_device_screen(data: Any = None, query: Any = None) -> Optional[Dict[str, Any]]:
    """What is on the phone screen, shared with this message:
    ``device_screen: {app?, package?, title?, url?, text?}`` as an object in a
    JSON body / WebSocket frame, or its JSON string in a form field or the
    query string. Validated and capped by core/device_screen.py; None when
    absent or unusable (never fails the message)."""
    value = data.get("device_screen") if isinstance(data, dict) else None
    if value is None and query is not None:
        value = query.get("device_screen")
    return device_screen.normalize(value) if value is not None else None


async def _read_text_part(field: Any, limit: int) -> str:
    """A multipart text field, read up to ``limit`` bytes (the rest is
    drained and dropped; an over-long JSON then fails to parse and is ignored)."""
    buf = bytearray()
    while True:
        chunk = await field.read_chunk()
        if not chunk:
            break
        if len(buf) < limit:
            buf.extend(chunk[: limit - len(buf)])
    return buf.decode("utf-8", errors="replace").strip()


def _decode_data_uri_images(items, user: User) -> List[str]:
    """Images of a message: data URIs saved for ``user``, and ``/api/images/``
    URLs kept only when they are ``user``'s own."""
    images = []
    for img_data in items or []:
        if not isinstance(img_data, str):
            continue
        if img_data.startswith("data:"):
            header, b64 = img_data.split(",", 1)
            mime = header.split(";")[0].split(":")[1] if ":" in header else "image/jpeg"
            images.append(save_image(base64.b64decode(b64), mime, user))
        elif img_data.startswith("/api/images/") and get_image_path(img_data, user) is not None:
            images.append(img_data)
    return images


async def assistant_models_api(request: web.Request) -> web.Response:
    """GET /api/assistant/models — the model picker catalog and the default
    id: the profiles the caller's access level may use."""
    return web.json_response(catalog_payload(request_user(request).level))


async def assistant_message_api(request: web.Request) -> web.StreamResponse:
    """POST /api/assistant/message {session_id, message, model?, images?, device?, screen?, device_screen?} → SSE

    ``session_id: "main"`` is the shared thread; ``device`` (phone|watch|tablet|tv|mac|web)
    is stored with the message there; ``screen: {kind, id}`` is the item on screen
    (this turn only); ``device_screen: {app, package, title, url, text}`` is the
    phone screen the user shared with this message (this turn only, untrusted).
    See docs/agent-actions.md.
    """
    request_user(request)
    try:
        data = await request.json()
        message = (data.get("message") or "").strip()
        session_id = data.get("session_id") or "0"
        user = session_of(request, session_id)
        if user is None:
            return not_found()
        if not message:
            return web.json_response({"error": "Message is required"}, status=400)
        model_id = _requested_model_id(user, data.get("model"))

        images = _decode_data_uri_images(data.get("images"), user)
        try:
            run = start_agent_task(user, session_id, message, is_voice=False,
                                   images=images or None, model_id=model_id,
                                   device=data.get("device") or "", screen=_requested_screen(data),
                                   device_screen=_requested_device_screen(data))
        except SessionBusyError as busy:
            return _busy_response(busy)
        return await relay_run(request, user, session_id, run)
    except Exception as e:
        logger.error(f"API assistant message error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def assistant_voice_api(request: web.Request) -> web.StreamResponse:
    """POST /api/assistant/voice?session_id=&model=&device=&screen_kind=&screen_id=&device_screen= (multipart) → SSE

    Multipart parts: ``audio`` (required), optional ``device_screen`` (JSON
    string, the shared phone screen; the query parameter of the same name
    works too, for short content only).
    """
    session_id = request.query.get("session_id") or "0"
    user = session_of(request, session_id)
    if user is None:
        return not_found()
    try:
        reader = await request.multipart()
        mime_type = "audio/webm"
        audio_bytes = bytearray()
        got_audio = False
        form: Dict[str, Any] = {}
        while True:
            field = await reader.next()
            if field is None:
                break
            if not isinstance(field, aiohttp.BodyPartReader):
                continue
            if field.name == "audio" and not got_audio:
                got_audio = True
                mime_type = field.headers.get("Content-Type", "audio/webm")
                while True:
                    chunk = await field.read_chunk()
                    if not chunk:
                        break
                    audio_bytes.extend(chunk)
            elif field.name == "device_screen":
                form["device_screen"] = await _read_text_part(field, DEVICE_SCREEN_FIELD_MAX_BYTES)
            else:
                await field.release()
        if not got_audio:
            return web.json_response({"error": "Audio file field 'audio' is required"}, status=400)
        if not audio_bytes:
            return web.json_response({"error": "Received empty audio file"}, status=400)

        busy = runs.active(user.id, session_id)
        if busy is not None:
            return _busy_response(SessionBusyError(busy))

        logger.info(f"Transcribing audio upload with MIME type: {mime_type}...")
        try:
            transcript = await llm.transcribe(bytes(audio_bytes), mime_type, source="app")
        except Exception as te:
            logger.error(f"Failed to transcribe web audio: {te}", exc_info=True)
            return web.json_response({"error": f"Transcription failed: {str(te)}"}, status=500)

        logger.info(f"Transcribed web audio: '{transcript}'")
        if not transcript:
            return web.json_response({"error": "No speech detected", "code": "no_speech"}, status=422)

        try:
            run = start_agent_task(
                user, session_id, transcript, is_voice=True,
                model_id=_requested_model_id(user, request.query.get("model")),
                pre_events=[{"type": "transcription", "text": transcript}],
                device=request.query.get("device") or "",
                screen=_requested_screen(query=request.query),
                device_screen=_requested_device_screen(form, query=request.query),
            )
        except SessionBusyError as busy:
            return _busy_response(busy)
        return await relay_run(request, user, session_id, run, transcription=transcript)
    except Exception as e:
        logger.error(f"API assistant voice error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def assistant_voice_stream_api(request: web.Request) -> web.StreamResponse:
    """GET /api/assistant/voice_stream?session_id=&model=&device= (WebSocket)

    Client → server: binary 16-bit mono PCM frames, then a text
    ``{"event": "stop", "sample_rate": 16000, "screen"?: {kind, id},
    "device_screen"?: {app, package, title, url, text}}`` (both per utterance).

    Server → client: JSON text frames with the same schema as the SSE
    stream — ``transcription``, then the run's events, then a closing
    ``{"type": "state", ...}``. ``{"type": "no_speech"}`` replaces all of that
    when nothing intelligible was said. The socket stays open for another
    utterance.
    """
    session_id = request.query.get("session_id", "0")
    user = session_of(request, session_id)
    if user is None:
        return not_found()

    # Server pings keep the client's read timeout satisfied while the run works.
    ws = web.WebSocketResponse(heartbeat=SSE_HEARTBEAT_SECONDS)
    await ws.prepare(request)

    audio_buffer = bytearray()
    model_id = _requested_model_id(user, request.query.get("model"))
    device = request.query.get("device") or ""
    logger.info(f"WebSocket voice stream started for session {session_id}")

    async def send_state(transcription: Optional[str] = None):
        await ws.send_json({"type": "state", "state": build_state_payload(user, session_id, transcription)})

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.BINARY:
                audio_buffer.extend(msg.data)
                continue
            if msg.type == aiohttp.WSMsgType.ERROR:
                logger.error(f"WebSocket error: {ws.exception()}")
                continue
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue

            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                logger.error("Invalid JSON received over WebSocket")
                continue
            if data.get("event") != "stop":
                continue

            logger.info(f"Received stop signal. Processing {len(audio_buffer)} bytes of audio.")
            if not audio_buffer:
                await ws.send_json({"type": "no_speech"})
                continue

            sample_rate = data.get("sample_rate", 16000)
            wav_data = create_wav_header(sample_rate, 16, 1, len(audio_buffer)) + audio_buffer
            audio_buffer.clear()

            if runs.is_running(user.id, session_id):
                await ws.send_json({"type": "error", "code": "session_busy",
                                    "message": "This chat is still working on the previous request."})
                await send_state()
                continue

            try:
                transcript = await llm.transcribe(bytes(wav_data), "audio/wav", source="app")
            except Exception as te:
                logger.error(f"Transcription failed: {te}", exc_info=True)
                await ws.send_json({"type": "error", "message": f"Transcription failed: {te}"})
                await send_state()
                continue

            logger.info(f"Stream transcribed: '{transcript}'")
            if not transcript:
                await ws.send_json({"type": "no_speech"})
                continue

            try:
                run = start_agent_task(
                    user, session_id, transcript, is_voice=True, model_id=model_id,
                    pre_events=[{"type": "transcription", "text": transcript}],
                    device=device,
                    # Per utterance: the stop frame may name what is on screen now.
                    screen=_requested_screen(data, query=request.query),
                    # Per utterance, stop frame only: the phone screen shared with it.
                    device_screen=_requested_device_screen(data),
                )
            except SessionBusyError:
                await ws.send_json({"type": "error", "code": "session_busy",
                                    "message": "This chat is still working on the previous request."})
                await send_state()
                continue

            seq = 0
            while not ws.closed:
                events = await run.wait_for_events(seq, SSE_HEARTBEAT_SECONDS)
                if not events:
                    if run.finished:
                        break
                    continue
                for event in events:
                    await ws.send_json(event, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))
                seq = events[-1]["seq"]
            if not ws.closed:
                await send_state(transcript)

    except Exception as e:
        logger.error(f"WebSocket voice stream exception: {e}", exc_info=True)
    finally:
        logger.info(f"WebSocket voice stream closed for session {session_id}")

    return ws


async def assistant_approve_api(request: web.Request) -> web.StreamResponse:
    """POST /api/assistant/approve {session_id, action, selection?} → SSE

    ``action``: approve | deny | extend_iterations | decline_iterations | select
    | cancel (aliases: stop, interrupt — returns JSON, not a stream).
    """
    request_user(request)
    try:
        data = await request.json()
        session_id = data.get("session_id") or "0"
        action = data.get("action")
        user = session_of(request, session_id)
        if user is None:
            return not_found()

        if action in ("cancel", "stop", "interrupt"):
            cancelled = cancel_agent_task(user, session_id)
            if cancelled:
                set_session_status(user.id, session_id, "idle")
            return web.json_response({"status": "cancelled" if cancelled else "idle", "session_id": session_id})

        if action not in ("approve", "deny", "extend_iterations", "decline_iterations", "select"):
            return web.json_response({"error": "Action must be 'approve', 'deny', 'extend_iterations', 'decline_iterations', 'select', 'cancel', 'stop', or 'interrupt'"}, status=400)

        if get_pending(user.id, session_id) is None:
            return web.json_response({"error": "No pending actions found or expired"}, status=400)

        device = data.get("device") or ""
        try:
            if action == "select":
                selection = data.get("selection") or []
                if not isinstance(selection, list):
                    return web.json_response({"error": "Selection must be a list of option indices"}, status=400)
                run = start_resume_selection_task(user, session_id, selection, device=device)
            else:
                run = start_resume_task(user, session_id, action, device=device)
        except SessionBusyError as busy:
            return _busy_response(busy)

        return await relay_run(request, user, session_id, run)
    except Exception as e:
        logger.error(f"API assistant approve error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def serve_image_api(request: web.Request) -> web.Response:
    """GET /api/images/{filename} — an image saved for the caller (else 404)."""
    path = get_image_path(request.match_info.get("filename", ""), request_user(request))
    if not path:
        return web.Response(status=404)
    ext = path.suffix.lower()
    content_type = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                    ".webp": "image/webp", ".gif": "image/gif"}.get(ext, "application/octet-stream")
    # NOTE: web.FileResponse takes no content_type kwarg (TypeError on aiohttp 3.13+)
    return web.FileResponse(path, headers={"Content-Type": content_type})


async def assistant_message_with_image_api(request: web.Request) -> web.StreamResponse:
    """POST /api/assistant/message_with_image?session_id=&model=&device=&screen_kind=&screen_id= (multipart) → SSE

    Form fields: ``message``, ``image`` (repeatable), optional ``screen`` (JSON ``{kind, id}``),
    optional ``device_screen`` (JSON ``{app, package, title, url, text}``: the phone screen
    the user shared with this message; the screenshot goes in ``image``).
    """
    session_id = request.query.get("session_id") or "0"
    user = session_of(request, session_id)
    if user is None:
        return not_found()
    try:
        reader = await request.multipart()
        message = ""
        images = []
        form: Dict[str, Any] = {}

        while True:
            field = await reader.next()
            if field is None:
                break
            if field.name == "message":
                message = (await field.read()).decode().strip()
            elif field.name in ("screen", "device"):
                form[field.name] = (await field.read()).decode().strip()
            elif field.name == "device_screen":
                form[field.name] = await _read_text_part(field, DEVICE_SCREEN_FIELD_MAX_BYTES)
            elif field.name == "image":
                mime_type = field.headers.get("Content-Type", "image/jpeg")
                image_bytes = bytearray()
                while True:
                    chunk = await field.read_chunk()
                    if not chunk:
                        break
                    image_bytes.extend(chunk)
                if image_bytes:
                    images.append(save_image(bytes(image_bytes), mime_type, user))

        if not message:
            message = "Please analyze this image."

        try:
            run = start_agent_task(user, session_id, message, is_voice=False, images=images or None,
                                   model_id=_requested_model_id(user, request.query.get("model")),
                                   device=form.get("device") or request.query.get("device") or "",
                                   screen=_requested_screen(form, query=request.query),
                                   device_screen=_requested_device_screen(form))
        except SessionBusyError as busy:
            return _busy_response(busy)
        return await relay_run(request, user, session_id, run)
    except Exception as e:
        logger.error(f"API assistant message with image error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


def setup(app: web.Application) -> None:
    """Register the assistant and image routes."""
    app.router.add_get("/api/assistant/models", assistant_models_api)
    app.router.add_post("/api/assistant/message", assistant_message_api)
    app.router.add_post("/api/assistant/message_with_image", assistant_message_with_image_api)
    app.router.add_post("/api/assistant/voice", assistant_voice_api)
    app.router.add_get("/api/assistant/voice_stream", assistant_voice_stream_api)
    app.router.add_get("/api/assistant/realtime", assistant_realtime_ws_api)
    app.router.add_post("/api/assistant/approve", assistant_approve_api)
    app.router.add_get("/api/images/{filename}", serve_image_api)
