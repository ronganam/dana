"""One user's Frigate, over that user's own connection (the ``url`` setting):
the dashboard's events rail, the activity strip, event / motion previews and
clips, and go2rtc WebRTC signaling. The handlers live in api.py and
turn what this returns into responses.

Every URL is the connection's address joined with a path
(:func:`integrations.framework.addresses.join`: never another host, never a
query smuggled in through the address). Only fixed Frigate paths are ever requested: camera names and event ids are
checked against :data:`CAMERA_RE` / :data:`EVENT_ID_RE` (an id made only of
dots is refused: it would be a path segment of its own), motion windows are
integers, and the go2rtc stream name travels as an encoded query parameter.

Every function takes the user's :class:`~integrations.framework.Connection`;
caches (events, activity, clips) are per :func:`signature`, never shared
between connections.
"""

import asyncio
import collections
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from integrations.framework import Connection, addresses
from integrations.cameras.config import base_url

logger = logging.getLogger(__name__)

Signature = Tuple[str, str]

EVENT_ID_RE = re.compile(r"^(?!\.+$)[0-9A-Za-z._-]{1,64}$")
CAMERA_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}$")
EVENTS_TTL = 5.0  # seconds; several viewers share one upstream call

# Optional plain-motion cards on the rail (motion with no detected object).
# Frigate's motion is camera-wide (zones only apply to objects);
# /api/review/activity/motion gives the % of the frame that moved per 30 s
# bucket, and buckets above the threshold are joined into segments. Off by
# default: on this camera it's mostly night IR noise and changing daylight.
try:
    MOTION_THRESHOLD = float(os.environ.get("FRIGATE_MOTION_THRESHOLD", "0"))
except ValueError:
    MOTION_THRESHOLD = 0.0
MOTION_BUCKET = 30          # seconds per activity bucket
MOTION_LOOKBACK = 24 * 3600  # continuous recordings are kept 1 day
MOTION_PAD = 10             # an object event within this many seconds swallows the segment
MOTION_MEDIA_MAX = 60       # preview / clip cover at most the first minute of a segment

ACTIVITY_BUCKET = 120   # seconds per cell of the dashboard activity strip
ACTIVITY_TTL = 30.0
ACTIVITY_LABEL_MAP = {"bark": "dog"}   # audio: a barking dog is an awake dog

CLIP_MAX_BYTES = 60 * 1024 * 1024
CLIP_CACHE_SIZE = 4  # clips kept in memory, all connections together (keyed per connection)

_events_cache: Dict[Tuple[Signature, str], Tuple[float, Dict[str, Any]]] = {}
_activity_cache: Dict[Tuple[Signature, str], Tuple[float, Dict[str, Any]]] = {}
_clip_cache: "collections.OrderedDict[Tuple[Signature, str], bytes]" = collections.OrderedDict()


class FrigateError(Exception):
    """Frigate could not answer: ``status`` and ``body`` are what the endpoint answers."""

    def __init__(self, status: int, body: Dict[str, Any]) -> None:
        super().__init__(body.get("error", "frigate error"))
        self.status = status
        self.body = body


def signature(conn: Connection) -> Signature:
    """What identifies one connection's Frigate (caches key on it)."""
    return (conn.user.id, base_url(conn))


def url(conn: Connection, path: str) -> str:
    """``path`` on the connection's Frigate, built so it cannot leave that
    address (docs/access.md, "Security rules").

    Raises:
        ValueError: the stored address is not valid (no address, a query, …).
    """
    return addresses.join(base_url(conn), path)


def _url(conn: Connection, path: str) -> str:
    """:func:`url`, or FrigateError 502 when the stored address is not valid."""
    try:
        return url(conn, path)
    except ValueError as e:
        logger.warning(f"Frigate address of user {conn.user.id} refused: {e}")
        raise FrigateError(502, {"error": "the Frigate address is not valid"}) from None


def valid_camera(name: str) -> bool:
    return bool(CAMERA_RE.match(name or ""))


def valid_event_id(event_id: str) -> bool:
    return bool(EVENT_ID_RE.match(event_id or ""))


def motion_range(camera: str, start: str, end: str) -> Optional[Tuple[str, int, int]]:
    """Validated (camera, start, end) of a motion media URL, or None."""
    try:
        s, e = int(start), int(end)
    except ValueError:
        return None
    if not valid_camera(camera) or not (0 < e - s <= MOTION_MEDIA_MAX):
        return None
    return camera, s, e


def motion_segments(buckets: List[Dict[str, Any]], threshold: float) -> List[List[int]]:
    """Join consecutive above-threshold buckets into [start, end] segments (oldest first)."""
    starts = sorted(int(b["start_time"]) for b in buckets
                    if (b.get("motion") or 0) > threshold and b.get("start_time") is not None)
    segs: List[List[int]] = []
    for t in starts:
        if segs and t <= segs[-1][1]:
            segs[-1][1] = max(segs[-1][1], t + MOTION_BUCKET)
        else:
            segs.append([t, t + MOTION_BUCKET])
    return segs


def _motion_media_window(start: int, end: int) -> Tuple[int, int]:
    return start, min(end, start + MOTION_MEDIA_MAX)


# ---------------------------------------------------------------------------
# Events rail and activity strip
# ---------------------------------------------------------------------------

async def events(conn: Connection, camera_name: str, limit: int) -> Dict[str, Any]:
    """``GET /api/frigate/events/{camera}``: the latest ``limit`` events, newest
    first (``object`` detections without audio ones, plus ``motion`` segments
    with no object around them when :data:`MOTION_THRESHOLD` > 0), with
    ``server_time``. Cached :data:`EVENTS_TTL` s per connection.

    Raises:
        FrigateError: 502 when Frigate's events cannot be read.
    """
    cache_key = (signature(conn), f"{camera_name}:{limit}")
    hit = _events_cache.get(cache_key)
    if hit and time.monotonic() - hit[0] < EVENTS_TTL:
        return hit[1]

    now = time.time()
    fetch_limit = limit * 4
    events_url, motion_url = _url(conn, "/api/events"), _url(conn, "/api/review/activity/motion")
    async with httpx.AsyncClient() as client:
        ev_req = client.get(events_url,
                            params={"camera": camera_name, "limit": fetch_limit}, timeout=8.0)
        if MOTION_THRESHOLD > 0:
            mo_req = client.get(motion_url,
                                params={"cameras": camera_name, "after": int(now - MOTION_LOOKBACK),
                                        "before": int(now) + MOTION_BUCKET, "scale": MOTION_BUCKET},
                                timeout=8.0)
            ev_res, mo_res = await asyncio.gather(ev_req, mo_req, return_exceptions=True)
        else:
            (ev_res,), mo_res = await asyncio.gather(ev_req, return_exceptions=True), None
    try:
        if isinstance(ev_res, BaseException):
            raise ev_res
        ev_res.raise_for_status()
        raw = ev_res.json()
    except Exception as e:
        logger.warning(f"Frigate events fetch failed for {camera_name}: {e}")
        raise FrigateError(502, {"error": "frigate unavailable", "events": []}) from e

    out = []
    for e in raw:
        if (e.get("data") or {}).get("type") == "audio" or e.get("label") == "speech":
            continue
        eid = e["id"]
        out.append({
            "id": eid,
            "type": "object",
            "label": e.get("label"),
            "sub_label": e.get("sub_label"),
            "zones": e.get("zones") or [],
            "start_time": e.get("start_time"),
            "end_time": e.get("end_time"),
            "preview_url": f"/api/frigate/event/{eid}/preview.gif",
            "clip_url": f"/api/frigate/event/{eid}/clip.mp4",
        })

    if mo_res is not None:
        try:
            if isinstance(mo_res, BaseException):
                raise mo_res
            mo_res.raise_for_status()
            segs = motion_segments(mo_res.json(), MOTION_THRESHOLD)
        except Exception as e:
            logger.warning(f"Frigate motion fetch failed for {camera_name}: {e}")
            segs = []
        spans = [(e.get("start_time") or 0, e.get("end_time") or now + 1e6) for e in raw]
        # A full event page doesn't reach back further than its oldest event,
        # so older motion can't be checked against objects (and wouldn't rank anyway).
        floor = min((s for s, _ in spans), default=0) if len(raw) >= fetch_limit else 0
        for s0, s1 in segs:
            if s0 < floor or any(a < s1 + MOTION_PAD and b > s0 - MOTION_PAD for a, b in spans):
                continue
            m0, m1 = _motion_media_window(s0, s1)
            base = f"/api/frigate/motion/{camera_name}/{m0}/{m1}"
            out.append({
                "id": f"motion-{camera_name}-{s0}",   # stable while the segment grows
                "type": "motion",
                "label": "motion",
                "sub_label": None,
                "zones": [],
                "start_time": s0,
                "end_time": min(s1, now),
                "preview_url": f"{base}/preview.gif",
                "clip_url": f"{base}/clip.mp4",
            })

    out.sort(key=lambda e: e.get("start_time") or 0, reverse=True)
    payload = {"server_time": now, "events": out[:limit]}
    _events_cache[cache_key] = (time.monotonic(), payload)
    return payload


async def activity(conn: Connection, camera_name: str, hours: int) -> Dict[str, Any]:
    """``GET /api/frigate/activity/{camera}``: seconds of activity per label per
    :data:`ACTIVITY_BUCKET`-second cell over the last ``hours`` (from Frigate's
    tracked-object events; ``bark`` folded into ``dog``), and each label's
    latest event end (None while one runs). Cached :data:`ACTIVITY_TTL` s per
    connection.

    Raises:
        FrigateError: 502 when Frigate's events cannot be read.
    """
    cache_key = (signature(conn), f"{camera_name}:{hours}")
    hit = _activity_cache.get(cache_key)
    if hit and time.monotonic() - hit[0] < ACTIVITY_TTL:
        return hit[1]

    now = time.time()
    n = hours * 3600 // ACTIVITY_BUCKET
    end = (int(now) // ACTIVITY_BUCKET + 1) * ACTIVITY_BUCKET
    start = end - n * ACTIVITY_BUCKET
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(_url(conn, "/api/events"),
                                 params={"camera": camera_name, "after": start - 3600, "limit": 2000},
                                 timeout=10.0)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        logger.warning(f"Frigate activity fetch failed for {camera_name}: {e}")
        raise FrigateError(502, {"error": "frigate unavailable"}) from e

    series: Dict[str, List[float]] = {}
    last: Dict[str, Optional[float]] = {}   # label -> latest end_time; None = moving now
    for e in raw:
        label = e.get("label")
        if not label or e.get("start_time") is None:
            continue
        label = ACTIVITY_LABEL_MAP.get(label, label)
        e_end = e.get("end_time")
        if label not in last or (last[label] is not None and (e_end is None or e_end > last[label])):
            last[label] = e_end
        a = max(float(e["start_time"]), start)
        b = min(float(e.get("end_time") or now), end)
        if b <= a:
            continue
        cells = series.setdefault(label, [0.0] * n)
        i = int((a - start) // ACTIVITY_BUCKET)
        while i < n:
            c0 = start + i * ACTIVITY_BUCKET
            overlap = min(b, c0 + ACTIVITY_BUCKET) - max(a, c0)
            if overlap <= 0:
                break
            # Overlapping events of one label can't make a cell more than full.
            cells[i] = min(ACTIVITY_BUCKET, cells[i] + overlap)
            i += 1
    payload = {"server_time": now, "start": start, "end": end, "bucket": ACTIVITY_BUCKET,
               "series": {k: [round(v) for v in vs] for k, vs in series.items()},
               "last": last}
    _activity_cache[cache_key] = (time.monotonic(), payload)
    return payload


# ---------------------------------------------------------------------------
# Previews and clips
# ---------------------------------------------------------------------------

async def _gif(conn: Connection, upstream: str, what: str) -> bytes:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(_url(conn, upstream), timeout=20.0)
    except Exception as e:
        logger.warning(f"Frigate preview fetch failed for {what}: {e}")
        raise FrigateError(502, {"error": "frigate unavailable"}) from e
    if r.status_code != 200:
        raise FrigateError(404 if r.status_code == 404 else 502, {"error": f"frigate returned {r.status_code}"})
    return r.content


async def event_gif(conn: Connection, event_id: str) -> bytes:
    """An event's preview GIF (``event_id`` already checked with :func:`valid_event_id`).

    Raises:
        FrigateError: Frigate unreachable (502) or not 200 (404 / 502).
    """
    return await _gif(conn, f"/api/events/{event_id}/preview.gif", event_id)


async def motion_gif(conn: Connection, camera: str, start: int, end: int) -> bytes:
    """A motion window's GIF (Frigate builds it from its low-res preview frames).

    Raises:
        FrigateError: see :func:`event_gif`.
    """
    return await _gif(conn, f"/api/{camera}/start/{start}/end/{end}/preview.gif", f"{camera} motion {start}")


async def _clip(conn: Connection, upstream: str, key: str) -> bytes:
    """Fetch a clip once and keep it in a tiny in-memory LRU (keyed by connection).

    Frigate generates clips on the fly as a chunked stream (no length, no
    ranges), which WebViews play badly and cannot seek. Finished clips never
    change, so the cache is safe.
    """
    cache_key = (signature(conn), key)
    data = _clip_cache.get(cache_key)
    if data is not None:
        _clip_cache.move_to_end(cache_key)
        return data
    try:
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", _url(conn, upstream), timeout=httpx.Timeout(10.0, read=60.0)) as r:
                if r.status_code != 200:
                    raise FrigateError(404 if r.status_code == 404 else 502,
                                       {"error": f"frigate returned {r.status_code}"})
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > CLIP_MAX_BYTES:
                        raise FrigateError(413, {"error": "clip too large"})
    except FrigateError:
        raise
    except Exception as e:
        logger.warning(f"Frigate clip fetch failed for {key}: {e}")
        raise FrigateError(502, {"error": "frigate unavailable"}) from e
    data = bytes(buf)
    _clip_cache[cache_key] = data
    while len(_clip_cache) > CLIP_CACHE_SIZE:
        _clip_cache.popitem(last=False)
    return data


async def event_clip(conn: Connection, event_id: str) -> bytes:
    """An event's clip (``event_id`` already checked).

    Raises:
        FrigateError: unreachable (502), not 200 (404 / 502), larger than :data:`CLIP_MAX_BYTES` (413).
    """
    return await _clip(conn, f"/api/events/{event_id}/clip.mp4", event_id)


async def motion_clip(conn: Connection, camera: str, start: int, end: int) -> bytes:
    """A motion window cut from the continuous recordings (a few seconds of lead-in).

    Raises:
        FrigateError: see :func:`event_clip`.
    """
    return await _clip(conn, f"/api/{camera}/start/{start - 3}/end/{end}/clip.mp4", f"motion:{camera}:{start}:{end}")


# ---------------------------------------------------------------------------
# go2rtc WebRTC signaling
# ---------------------------------------------------------------------------

async def webrtc_answer(conn: Connection, camera_name: str, offer: bytes) -> bytes:
    """Relay an SDP offer to the connection's go2rtc (through Frigate) and return its answer.

    Raises:
        FrigateError: go2rtc did not answer 201 (502 with its text).
        httpx.HTTPError: Frigate unreachable.
    """
    async with httpx.AsyncClient() as client:
        r = await client.post(_url(conn, "/api/go2rtc/webrtc"), params={"src": camera_name}, content=offer,
                              headers={"Content-Type": "application/sdp"}, timeout=10.0)
    if r.status_code != 201:
        error_text = r.text[:500]
        logger.error(f"WebRTC signaling for {camera_name}: go2rtc returned {r.status_code} — {error_text}")
        raise FrigateError(502, {"error": f"go2rtc returned {r.status_code}", "detail": error_text})
    return r.content


async def check(conn: Connection) -> Optional[str]:
    """``Integration(check=)``: one cheap request (``/api/version``): None
    when Frigate answers, else a short reason."""
    if not base_url(conn):
        return "No Frigate address is set."
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url(conn, "/api/version"), timeout=8.0)
    except Exception as e:
        return f"Could not reach Frigate at {base_url(conn)} ({type(e).__name__})."
    if r.status_code != 200:
        return f"Frigate answered HTTP {r.status_code}."
    return None
