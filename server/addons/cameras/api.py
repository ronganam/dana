"""HTTP routes of the cameras addon: the caller's Frigate behind ``/api/frigate/*``
and HA camera snapshots (docs/dashboard.md; the Frigate logic is client.py).

- ``GET /api/camera/{entity_id}/proxy``, ``GET /api/frigate/{camera_name}`` (MJPEG) — through
  the caller's Home Assistant: need ``homeassistant`` too
- ``POST /api/frigate/webrtc/{camera_name}`` — go2rtc SDP signaling
- ``GET /api/frigate/events/{camera_name}``
- ``GET /api/frigate/event/{event_id}/preview.gif|clip.mp4``,
  ``GET /api/frigate/motion/{camera_name}/{start}/{end}/preview.gif|clip.mp4``

403 ``not_connected`` without ``cameras``.
"""

import asyncio
import logging
import re
from typing import Optional

import httpx
from aiohttp import web

from core.auth import request_user
from addons.cameras import client as cameras_client, config as cameras_config
from core.addons import store
from addons.homeassistant import client as ha_client, config as ha_config

logger = logging.getLogger(__name__)


def _ha_conn(request: web.Request) -> store.Connection:
    """The caller's Home Assistant connection (NotConnected → 403 by the middleware)."""
    return store.require(request_user(request), ha_config.ADDON)


def _cameras_conn(request: web.Request) -> store.Connection:
    """The caller's Frigate connection (NotConnected → 403 by the middleware)."""
    return store.require(request_user(request), cameras_config.ADDON)


async def ha_camera_proxy_api(request: web.Request) -> web.Response:
    """GET /api/camera/{entity_id}/proxy — proxy camera snapshot from the caller's HA
    (needs both the homeassistant and the cameras addons)."""
    entity_id = request.match_info.get("entity_id")
    if not entity_id:
        return web.json_response({"error": "entity_id is required"}, status=400)

    conn = _ha_conn(request)
    _cameras_conn(request)  # a camera route: needs cameras as well as Home Assistant
    if not ha_client.valid_entity_id(entity_id):
        return web.json_response({"error": "not an entity id"}, status=400)
    try:
        r = await ha_client.get(conn, f"/api/camera_proxy/{entity_id}", timeout=10.0)
        if r.status_code != 200:
            return web.json_response({"error": f"HA returned {r.status_code}"}, status=502)
        return web.Response(
            body=r.content,
            content_type=r.headers.get("Content-Type", "image/jpeg"),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    except Exception as e:
        logger.error(f"Error proxying camera {entity_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def frigate_camera_proxy_api(request: web.Request) -> web.StreamResponse:
    """GET /api/frigate/{camera_name} — proxy MJPEG stream via HA camera_proxy_stream.

    Frigate is not directly reachable from the Dana pod, so we route
    through the caller's HA camera_proxy_stream endpoint which supports MJPEG
    streaming (needs both the homeassistant and the cameras addons).
    Includes a watchdog that drops the stream if no frame arrives in 15s.
    """
    camera_name = request.match_info.get("camera_name")
    if not camera_name:
        return web.json_response({"error": "camera_name is required"}, status=400)

    conn = _ha_conn(request)
    _cameras_conn(request)  # a camera route: needs cameras as well as Home Assistant
    if not cameras_client.valid_camera(camera_name):
        return web.json_response({"error": "not a camera name"}, status=400)
    entity_id = f"camera.{camera_name}"
    try:
        stream_url = ha_client.url(conn, f"/api/camera_proxy_stream/{entity_id}")
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=502)
    headers = ha_client.headers(conn)
    WATCHDOG_TIMEOUT = 15.0  # drop stream if no frame in 15s

    try:
        client = httpx.AsyncClient()

        # Prepare response immediately — content-type is always multipart MJPEG
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "multipart/x-mixed-replace; boundary=frameboundary",
                "Cache-Control": "no-cache, no-store, must-revalidate",
            },
        )
        await response.prepare(request)

        # Stream directly — no probe request (non-streaming GET on MJPEG hangs forever)
        async with client.stream("GET", stream_url, headers=headers, timeout=WATCHDOG_TIMEOUT) as stream:
            async for chunk in stream.aiter_bytes():
                await response.write(chunk)

        await response.write_eof()
        await client.aclose()
        return response
    except asyncio.CancelledError:
        logger.info(f"MJPEG stream for {camera_name} cancelled by client")
        try:
            await client.aclose()
        except Exception:
            pass
        raise
    except (ConnectionResetError, BrokenPipeError):
        logger.info(f"MJPEG stream for {camera_name} — client disconnected")
        try:
            await client.aclose()
        except Exception:
            pass
        return response
    except Exception as e:
        logger.error(f"Error streaming camera {camera_name} via HA: {e}", exc_info=True)
        try:
            await client.aclose()
        except Exception:
            pass
        return web.json_response({"error": str(e)}, status=500)


async def frigate_webrtc_signaling_api(request: web.Request) -> web.Response:
    """POST /api/frigate/webrtc/{camera_name} — WebRTC SDP signaling proxy.

    Browser cannot POST cross-origin to Frigate (CORS). This proxy relays
    the SDP offer to the caller's Frigate go2rtc and returns the SDP answer.
    """
    conn = _cameras_conn(request)
    camera_name = request.match_info.get("camera_name")
    if not camera_name:
        return web.json_response({"error": "camera_name is required"}, status=400)

    try:
        body = await request.read()
        if not body:
            return web.json_response({"error": "SDP offer body is required"}, status=400)
        answer = await cameras_client.webrtc_answer(conn, camera_name, body)
        return web.Response(body=answer, content_type="application/sdp", headers={"Cache-Control": "no-cache"})
    except cameras_client.FrigateError as e:
        return web.json_response(e.body, status=e.status)
    except Exception as e:
        logger.error(f"WebRTC signaling error for {camera_name}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def frigate_events_api(request: web.Request) -> web.Response:
    """GET /api/frigate/events/{camera_name}?limit=6 — latest events, newest first.

    Two kinds, merged by start time: ``object`` (a Frigate detection; audio
    events such as speech are dropped — the rail shows what the camera saw)
    and ``motion`` (motion with no object event around it). Each item carries
    its ``preview_url`` / ``clip_url``. ``server_time`` lets the client compute
    relative ages without trusting the tablet's clock.
    """
    conn = _cameras_conn(request)
    camera_name = request.match_info["camera_name"]
    if not cameras_client.valid_camera(camera_name):
        return web.json_response({"error": "invalid camera_name"}, status=400)
    try:
        limit = max(1, min(20, int(request.query.get("limit", "6"))))
    except ValueError:
        limit = 6
    try:
        payload = await cameras_client.events(conn, camera_name, limit)
    except cameras_client.FrigateError as e:
        return web.json_response(e.body, status=e.status)
    return web.json_response(payload, headers={"Cache-Control": "no-store"})


def _gif_response(request: web.Request, data: bytes) -> web.Response:
    """A finished event's GIF never changes: the client passes ``v=<end_time>``
    and gets a long-lived cache; ``v=live`` (event still running) is not cached."""
    cache = "no-store" if request.query.get("v", "live") == "live" else "private, max-age=86400, immutable"
    return web.Response(body=data, content_type="image/gif", headers={"Cache-Control": cache})


async def frigate_event_gif_api(request: web.Request) -> web.Response:
    """GET /api/frigate/event/{event_id}/preview.gif[?v=live|<end_time>].

    Frigate is only reachable server-side, so the GIF is relayed.
    """
    conn = _cameras_conn(request)
    event_id = request.match_info["event_id"]
    if not cameras_client.valid_event_id(event_id):
        return web.json_response({"error": "invalid event_id"}, status=400)
    try:
        return _gif_response(request, await cameras_client.event_gif(conn, event_id))
    except cameras_client.FrigateError as e:
        return web.json_response(e.body, status=e.status)


def _motion_range(request: web.Request) -> Optional[tuple]:
    """Validated (camera, start, end) of a motion media URL, or None."""
    return cameras_client.motion_range(request.match_info["camera_name"], request.match_info["start"],
                                       request.match_info["end"])


async def frigate_motion_gif_api(request: web.Request) -> web.Response:
    """GET /api/frigate/motion/{camera}/{start}/{end}/preview.gif — GIF of a motion window
    (Frigate builds it from its low-res preview frames, so it's cheap)."""
    conn = _cameras_conn(request)
    rng = _motion_range(request)
    if not rng:
        return web.json_response({"error": "invalid motion range"}, status=400)
    try:
        return _gif_response(request, await cameras_client.motion_gif(conn, *rng))
    except cameras_client.FrigateError as e:
        return web.json_response(e.body, status=e.status)


def _clip_response(request: web.Request, data: bytes) -> web.Response:
    """Serve a clip with Content-Length / Range (WebViews seek with ranges)."""
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=300"}
    total = len(data)
    m = re.match(r"^bytes=(\d*)-(\d*)$", request.headers.get("Range", ""))
    if m and (m.group(1) or m.group(2)):
        if m.group(1):
            start = int(m.group(1))
            end = min(int(m.group(2)), total - 1) if m.group(2) else total - 1
        else:  # suffix range: last N bytes
            start, end = max(0, total - int(m.group(2))), total - 1
        if start > end or start >= total:
            return web.Response(status=416, headers={"Content-Range": f"bytes */{total}"})
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"
        return web.Response(status=206, body=data[start:end + 1], content_type="video/mp4", headers=headers)
    return web.Response(body=data, content_type="video/mp4", headers=headers)


async def frigate_event_clip_api(request: web.Request) -> web.Response:
    """GET /api/frigate/event/{event_id}/clip.mp4 — the event's clip, with Range support."""
    conn = _cameras_conn(request)
    event_id = request.match_info["event_id"]
    if not cameras_client.valid_event_id(event_id):
        return web.json_response({"error": "invalid event_id"}, status=400)
    try:
        return _clip_response(request, await cameras_client.event_clip(conn, event_id))
    except cameras_client.FrigateError as e:
        return web.json_response(e.body, status=e.status)


async def frigate_motion_clip_api(request: web.Request) -> web.Response:
    """GET /api/frigate/motion/{camera}/{start}/{end}/clip.mp4 — the window cut from the
    continuous recordings (a few seconds of lead-in), with Range support."""
    conn = _cameras_conn(request)
    rng = _motion_range(request)
    if not rng:
        return web.json_response({"error": "invalid motion range"}, status=400)
    try:
        return _clip_response(request, await cameras_client.motion_clip(conn, *rng))
    except cameras_client.FrigateError as e:
        return web.json_response(e.body, status=e.status)


def setup(app: web.Application) -> None:
    """Register the camera routes."""
    app.router.add_get("/api/camera/{entity_id}/proxy", ha_camera_proxy_api)
    app.router.add_get("/api/frigate/{camera_name}", frigate_camera_proxy_api)
    app.router.add_post("/api/frigate/webrtc/{camera_name}", frigate_webrtc_signaling_api)
    app.router.add_get("/api/frigate/events/{camera_name}", frigate_events_api)
    app.router.add_get("/api/frigate/event/{event_id}/preview.gif", frigate_event_gif_api)
    app.router.add_get("/api/frigate/event/{event_id}/clip.mp4", frigate_event_clip_api)
    app.router.add_get("/api/frigate/motion/{camera_name}/{start}/{end}/preview.gif", frigate_motion_gif_api)
    app.router.add_get("/api/frigate/motion/{camera_name}/{start}/{end}/clip.mp4", frigate_motion_clip_api)
