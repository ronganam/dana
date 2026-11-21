"""Frigate cameras per user (docs/access.md, "Addons"; docs/dashboard.md).
Run: bin/test test_cameras_users

Two in-process fake Frigates (aiohttp: events, motion activity, previews,
clips, go2rtc WebRTC; every request recorded, anything else answered 404 and
recorded too) and a fake Home Assistant for the two proxies that go through
it. The first admin connects an address and is then pointed at Frigate A; Bob has
Frigate B; Carl has no cameras; Erin has Home Assistant but no cameras.
Checked: nothing reads ``FRIGATE_URL`` or holds the owner's address; every camera
endpoint reaches only the caller's own Frigate, with the first admin's
payloads as before; caches (events, activity, clips) per connection; no addon
→ 403 on every camera route (streaming and WebRTC included); the two Home
Assistant proxies need both addons; the upstream paths stay fixed (an id made
only of dots refused, the WebRTC stream name sent encoded). No network beyond
localhost.
"""

import asyncio
import json
import os
import pathlib
import re
import sys
import time
from typing import Any, Dict, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("cameras_users_test")
os.environ["API_TOKEN"] = "cam-users-test-env-token"  # a test value: with it set, DB tokens are honoured
from cryptography.fernet import Fernet  # noqa: E402
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key (Erin's Home Assistant token)
os.environ["FRIGATE_URL"] = "https://frigate.example.com/"  # the old env import's name: read by nothing now
os.environ["FRIGATE_MOTION_THRESHOLD"] = "5"  # a tuning value (env): motion cards on, to cover that path

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core import db, users  # noqa: E402

db.migrate()
users.bootstrap()
from addons import bootstrap as addons_bootstrap  # noqa: E402
addons_bootstrap.bootstrap()
testenv.connect_admin("cameras", settings={"url": "https://frigate.example.com"})

from addons.cameras import api as cameras_api  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from addons.cameras import client as cam_client  # noqa: E402
from core.addons import store  # noqa: E402

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


NOW = int(time.time())


class FakeFrigate:
    """One Frigate: its events and motion, previews / clips named after the
    instance; every request recorded as (method, path, query)."""

    def __init__(self, name: str, camera: str) -> None:
        self.name, self.camera = name, camera
        self.requests: List[Tuple[str, str, Dict[str, str]]] = []
        self.events = [
            {"id": f"{name}-e1", "label": "dog", "camera": camera, "start_time": NOW - 600, "end_time": NOW - 540,
             "zones": ["yard"], "sub_label": None, "data": {"type": "object"}},
            {"id": f"{name}-e2", "label": "speech", "camera": camera, "start_time": NOW - 300, "end_time": NOW - 290,
             "data": {"type": "audio"}},
            {"id": "shared-id", "label": "person", "camera": camera, "start_time": NOW - 3000,
             "end_time": NOW - 2990, "data": {"type": "object"}},
        ]
        app = web.Application()
        r = app.router
        r.add_get("/api/events", self.list_events)
        r.add_get("/api/review/activity/motion", self.motion)
        r.add_get("/api/events/{eid}/preview.gif", self.media)
        r.add_get("/api/events/{eid}/clip.mp4", self.media)
        r.add_get("/api/{camera}/start/{s}/end/{e}/preview.gif", self.media)
        r.add_get("/api/{camera}/start/{s}/end/{e}/clip.mp4", self.media)
        r.add_post("/api/go2rtc/webrtc", self.webrtc)
        r.add_route("*", "/{tail:.*}", self.other)
        self.server = TestServer(app)

    async def start(self) -> None:
        await self.server.start_server()
        self.url = str(self.server.make_url("")).rstrip("/")

    def _log(self, request: web.Request) -> None:
        self.requests.append((request.method, request.path, dict(request.query)))

    def calls(self) -> List[Tuple[str, str, Dict[str, str]]]:
        out, self.requests = self.requests, []
        return out

    async def list_events(self, request):
        self._log(request)
        return web.json_response([e for e in self.events if e["camera"] == request.query.get("camera")])

    async def motion(self, request):
        self._log(request)
        # A motion segment two hours ago, far from every object event.
        return web.json_response([{"start_time": NOW - 7200, "motion": 40}, {"start_time": NOW - 7170, "motion": 30}])

    async def media(self, request):
        self._log(request)
        kind = "gif" if request.path.endswith(".gif") else "mp4"
        return web.Response(body=f"{kind} of {self.name} {request.path}".encode(),
                            content_type="image/gif" if kind == "gif" else "video/mp4")

    async def webrtc(self, request):
        self._log(request)
        offer = await request.read()
        return web.Response(status=201, body=b"answer from " + self.name.encode() + b" to " + offer,
                            content_type="application/sdp")

    async def other(self, request):
        self._log(request)
        return web.Response(status=404)


class FakeHA:
    """A Home Assistant with the two camera endpoints the proxies use."""

    def __init__(self, name: str, token: str) -> None:
        self.name, self.token = name, token
        self.requests: List[str] = []
        app = web.Application()
        app.router.add_get("/api/camera_proxy/{eid}", self.snapshot)
        app.router.add_get("/api/camera_proxy_stream/{eid}", self.stream)
        self.server = TestServer(app)

    async def start(self) -> None:
        await self.server.start_server()
        self.url = str(self.server.make_url("")).rstrip("/")

    def _ok(self, request) -> bool:
        self.requests.append(request.path)
        return request.headers.get("Authorization") == f"Bearer {self.token}"

    async def snapshot(self, request):
        if not self._ok(request):
            return web.Response(status=401)
        return web.Response(body=f"jpeg-{self.name}".encode(), content_type="image/jpeg")

    async def stream(self, request):
        if not self._ok(request):
            return web.Response(status=401)
        return web.Response(body=f"--frameboundary mjpeg-{self.name}".encode(),
                            content_type="multipart/x-mixed-replace; boundary=frameboundary")


FR_A, FR_B = FakeFrigate("A", "yard"), FakeFrigate("B", "porch")
HA_B = FakeHA("B", "tok-b")

ADMIN = users.first_admin()
assert ADMIN is not None
BOB = users.create("Bob Levi", "member")
CARL = users.create("Carl Cohen", "member")
ERIN = users.create("Erin Katz", "member")
TOKENS = {u.id: users.issue_token(u.id, "test")[0] for u in (BOB, CARL, ERIN)}
AUTH = {ADMIN.id: {"Authorization": f"Bearer {os.environ['API_TOKEN']}"},
        **{uid: {"Authorization": f"Bearer {tok}"} for uid, tok in TOKENS.items()}}


def not_connected(addon: str) -> Dict[str, str]:
    return {"error": "not_connected", "addon": addon}


# --- 1. No env import: the old env name ---------------------------------------------------------

def test_import() -> None:
    cams = store.require(ADMIN, "cameras")
    check("connection: the address the admin connected (FRIGATE_URL ignored)",
          dict(cams.settings) == {"url": "https://frigate.example.com"}, dict(cams.settings))
    os.environ.pop("FRIGATE_URL")
    check("addon: no default address", all(f.default is None for f in store.get_addon("cameras").settings))
    root = testenv.SERVER
    readers, holders = [], []
    for path in list((root / "core").rglob("*.py")) + [p for p in (root / "addons").rglob("*.py") if "tests" not in p.parts] + [root / "main.py"]:
        text = path.read_text(encoding="utf-8")
        if re.search(r'["\']FRIGATE_URL["\']', text):
            readers.append(str(path.relative_to(root)))
        if "frigate.example.com" in text:
            holders.append(str(path.relative_to(root)))
    check("old env name: FRIGATE_URL read by nothing", not readers, readers)
    check("the owner's Frigate address: in no source file", not holders, holders)


# --- 2. Endpoints -------------------------------------------------------------------------------

def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    r = app.router
    r.add_get("/api/camera/{entity_id}/proxy", cameras_api.ha_camera_proxy_api)
    r.add_get("/api/frigate/{camera_name}", cameras_api.frigate_camera_proxy_api)
    r.add_post("/api/frigate/webrtc/{camera_name}", cameras_api.frigate_webrtc_signaling_api)
    r.add_get("/api/frigate/events/{camera_name}", cameras_api.frigate_events_api)
    r.add_get("/api/frigate/event/{event_id}/preview.gif", cameras_api.frigate_event_gif_api)
    r.add_get("/api/frigate/event/{event_id}/clip.mp4", cameras_api.frigate_event_clip_api)
    r.add_get("/api/frigate/activity/{camera_name}", cameras_api.frigate_activity_api)
    r.add_get("/api/frigate/motion/{camera_name}/{start}/{end}/preview.gif", cameras_api.frigate_motion_gif_api)
    r.add_get("/api/frigate/motion/{camera_name}/{start}/{end}/clip.mp4", cameras_api.frigate_motion_clip_api)
    return app


MOTION_S0 = NOW - 7200
CAMERA_ROUTES = [
    ("GET", "/api/frigate/events/yard"),
    ("GET", "/api/frigate/activity/yard"),
    ("GET", "/api/frigate/event/A-e1/preview.gif"),
    ("GET", "/api/frigate/event/A-e1/clip.mp4"),
    ("GET", f"/api/frigate/motion/yard/{MOTION_S0}/{MOTION_S0 + 60}/preview.gif"),
    ("GET", f"/api/frigate/motion/yard/{MOTION_S0}/{MOTION_S0 + 60}/clip.mp4"),
    ("POST", "/api/frigate/webrtc/yard"),
]
HA_ROUTES = [("GET", "/api/camera/camera.porch/proxy"), ("GET", "/api/frigate/porch")]


async def test_endpoints() -> None:
    store.update(ADMIN, "cameras", settings={"url": FR_A.url + "/"})
    store.connect(BOB, "cameras", settings={"url": FR_B.url})
    store.connect(ERIN, "homeassistant", credentials={"host": HA_B.url, "token": "tok-b"})
    async with TestClient(TestServer(build_app())) as http:
        async def call(user, method, path, **kw):
            r = await http.request(method, path, headers=AUTH[user.id], **kw)
            body = await r.read()
            return r.status, body, r.headers

        # Events rail: the admin's payload exactly as before, from Frigate A only.
        status, body, headers = await call(ADMIN, "GET", "/api/frigate/events/yard?limit=6")
        p = json.loads(body)
        expected = [
            {"id": "A-e1", "type": "object", "label": "dog", "sub_label": None, "zones": ["yard"],
             "start_time": NOW - 600, "end_time": NOW - 540,
             "preview_url": "/api/frigate/event/A-e1/preview.gif", "clip_url": "/api/frigate/event/A-e1/clip.mp4"},
            {"id": "shared-id", "type": "object", "label": "person", "sub_label": None, "zones": [],
             "start_time": NOW - 3000, "end_time": NOW - 2990,
             "preview_url": "/api/frigate/event/shared-id/preview.gif",
             "clip_url": "/api/frigate/event/shared-id/clip.mp4"},
            {"id": f"motion-yard-{MOTION_S0}", "type": "motion", "label": "motion", "sub_label": None, "zones": [],
             "start_time": MOTION_S0, "end_time": MOTION_S0 + 60,
             "preview_url": f"/api/frigate/motion/yard/{MOTION_S0}/{MOTION_S0 + 60}/preview.gif",
             "clip_url": f"/api/frigate/motion/yard/{MOTION_S0}/{MOTION_S0 + 60}/clip.mp4"},
        ]
        check("events: the admin's payload as before (audio dropped, motion segment, urls), no-store",
              status == 200 and p["events"] == expected and set(p) == {"server_time", "events"}
              and headers.get("Cache-Control") == "no-store", p)
        a_calls = FR_A.calls()
        check("events: asked of Frigate A only (events + motion), nothing of B",
              {c[1] for c in a_calls} == {"/api/events", "/api/review/activity/motion"} and not FR_B.calls(), a_calls)

        status, body, _ = await call(BOB, "GET", "/api/frigate/events/porch")
        p = json.loads(body)
        check("events: Bob's from his own Frigate",
              status == 200 and [e["id"] for e in p["events"] if e["type"] == "object"] == ["B-e1", "shared-id"]
              and FR_B.calls() and not FR_A.calls(), p)
        status, body, _ = await call(BOB, "GET", "/api/frigate/events/yard?limit=6")
        objects = [e for e in json.loads(body)["events"] if e["type"] == "object"]
        check("events: the admin's camera name asked of Bob's Frigate (no objects there), not the admin's cache",
              status == 200 and objects == [] and FR_B.calls() and not FR_A.calls(), body)

        await call(ADMIN, "GET", "/api/frigate/events/yard?limit=6")
        check("events: the admin's second read within 5 s comes from his cache", not FR_A.calls())

        status, body, _ = await call(ADMIN, "GET", "/api/frigate/activity/yard?hours=6")
        p = json.loads(body)
        check("activity: the admin's strip from Frigate A (dog seconds, last end)",
              status == 200 and p["last"] == {"dog": NOW - 540, "speech": NOW - 290, "person": NOW - 2990}
              and sum(p["series"]["dog"]) == 60 and {c[1] for c in FR_A.calls()} == {"/api/events"}
              and not FR_B.calls(), p)
        status, body, _ = await call(BOB, "GET", "/api/frigate/activity/porch")
        check("activity: Bob's from Frigate B", status == 200 and FR_B.calls() and not FR_A.calls())

        status, body, headers = await call(ADMIN, "GET", "/api/frigate/event/shared-id/preview.gif?v=123")
        status_b, body_b, _ = await call(BOB, "GET", "/api/frigate/event/shared-id/preview.gif")
        check("event GIF: the same event id gives each user their own Frigate's GIF (cache headers as before)",
              body == b"gif of A /api/events/shared-id/preview.gif" and body_b == b"gif of B /api/events/shared-id/preview.gif"
              and headers.get("Cache-Control") == "private, max-age=86400, immutable", (body, body_b))
        status, body, headers = await call(ADMIN, "GET", "/api/frigate/event/shared-id/clip.mp4")
        status_b, body_b, _ = await call(BOB, "GET", "/api/frigate/event/shared-id/clip.mp4")
        check("event clip: each user's own clip although the id is the same (cache per connection)",
              body == b"mp4 of A /api/events/shared-id/clip.mp4" and body_b == b"mp4 of B /api/events/shared-id/clip.mp4"
              and headers.get("Accept-Ranges") == "bytes", (body, body_b))
        FR_A.calls(), FR_B.calls()
        r = await http.get("/api/frigate/event/shared-id/clip.mp4", headers={**AUTH[ADMIN.id], "Range": "bytes=0-2"})
        check("event clip: Range served from the admin's cached clip (no new upstream call)",
              r.status == 206 and await r.read() == b"mp4" and r.headers["Content-Range"].startswith("bytes 0-2/")
              and not FR_A.calls())

        status, body, _ = await call(ADMIN, "GET", f"/api/frigate/motion/yard/{MOTION_S0}/{MOTION_S0 + 60}/preview.gif")
        status2, body2, _ = await call(ADMIN, "GET", f"/api/frigate/motion/yard/{MOTION_S0}/{MOTION_S0 + 60}/clip.mp4")
        check("motion GIF / clip: cut by the admin's Frigate (lead-in on the clip as before)",
              body == f"gif of A /api/yard/start/{MOTION_S0}/end/{MOTION_S0 + 60}/preview.gif".encode()
              and body2 == f"mp4 of A /api/yard/start/{MOTION_S0 - 3}/end/{MOTION_S0 + 60}/clip.mp4".encode()
              and not FR_B.calls(), (body, body2))
        FR_A.calls()

        status, body, _ = await call(BOB, "POST", "/api/frigate/webrtc/porch", data=b"v=0 offer")
        calls = FR_B.calls()
        check("WebRTC: Bob's offer goes to his go2rtc, the answer comes back",
              status == 200 and body == b"answer from B to v=0 offer"
              and calls == [("POST", "/api/go2rtc/webrtc", {"src": "porch"})] and not FR_A.calls(), (body, calls))

        # Upstream paths stay fixed.
        status, _, _ = await call(ADMIN, "GET", "/api/frigate/event/.../preview.gif")
        status_c, _, _ = await call(ADMIN, "GET", "/api/frigate/event/.../clip.mp4")
        check("paths: an event id made only of dots → 400, never reaches Frigate (dot segments: refused by the check)",
              status == 400 and status_c == 400 and not FR_A.calls()
              and not cam_client.valid_event_id("..") and not cam_client.valid_event_id(".")
              and cam_client.valid_event_id("1731700000.123456-ab12cd"), (status, status_c))
        status, _, _ = await call(ADMIN, "GET", "/api/frigate/events/ya%26rd")
        check("paths: a camera name outside [A-Za-z0-9_-] → 400 as before", status == 400 and not FR_A.calls())
        status, body, _ = await call(ADMIN, "POST", "/api/frigate/webrtc/yard%26dst%3Dx", data=b"offer")
        calls = FR_A.calls()
        check("paths: the WebRTC stream name is sent encoded (one src parameter, nothing injected)",
              calls == [("POST", "/api/go2rtc/webrtc", {"src": "yard&dst=x"})], calls)
        check("paths: no request reached any other Frigate path",
              not [c for f in (FR_A, FR_B) for c in f.calls()])

        # No addon: every camera route refused, nothing reaches any Frigate.
        for method, path in CAMERA_ROUTES:
            status, body, _ = await call(CARL, method, path, data=b"offer")
            check(f"no addon: {method} {path.split('/')[3]}/… → 403 not_connected",
                  status == 403 and json.loads(body) == not_connected("cameras"), (path, status, body))
            status, body, _ = await call(ERIN, method, path, data=b"offer")
            check(f"Home Assistant only: {method} {path.split('/')[3]}/… → 403 not_connected cameras",
                  status == 403 and json.loads(body) == not_connected("cameras"), (path, status, body))
        status, body, _ = await call(CARL, "GET", "/api/frigate/events/bad!name")
        check("no addon: refused before anything else (even a bad camera name)", status == 403)
        check("no addon: no Frigate was asked", not FR_A.calls() and not FR_B.calls())

        # The two Home Assistant proxies need both addons.
        for method, path in HA_ROUTES:
            status, body, _ = await call(ERIN, method, path)
            check(f"HA proxy {path}: Home Assistant without cameras → 403 not_connected cameras",
                  status == 403 and json.loads(body) == not_connected("cameras"), (status, body))
            status, body, _ = await call(BOB, method, path)
            check(f"HA proxy {path}: cameras without Home Assistant → 403 not_connected homeassistant",
                  status == 403 and json.loads(body) == not_connected("homeassistant"), (status, body))
            status, body, _ = await call(CARL, method, path)
            check(f"HA proxy {path}: neither → 403 not_connected", status == 403
                  and json.loads(body)["error"] == "not_connected", (status, body))
        check("HA proxies: the Home Assistant was never asked while an addon was missing", not HA_B.requests)
        store.connect(ERIN, "cameras", settings={"url": FR_B.url})
        status, body, _ = await call(ERIN, "GET", "/api/camera/camera.porch/proxy")
        check("HA proxy: snapshot with both addons, through the caller's Home Assistant",
              status == 200 and body == b"jpeg-B", (status, body))
        status, body, headers = await call(ERIN, "GET", "/api/frigate/porch")
        check("HA proxy: MJPEG stream with both addons, through the caller's Home Assistant",
              status == 200 and body == b"--frameboundary mjpeg-B"
              and headers["Content-Type"].startswith("multipart/x-mixed-replace"), (status, body))
        check("HA proxies: the streams went to the caller's HA camera entity",
              HA_B.requests == ["/api/camera_proxy/camera.porch", "/api/camera_proxy_stream/camera.porch"],
              HA_B.requests)

    keys = {k[0] for k in cam_client._events_cache} | {k[0] for k in cam_client._activity_cache} \
        | {k[0] for k in cam_client._clip_cache}
    check("caches: keyed per connection (user and Frigate address)",
          keys == {(ADMIN.id, FR_A.url), (BOB.id, FR_B.url)}, keys)


async def main() -> None:
    await FR_A.start()
    await FR_B.start()
    await HA_B.start()
    test_import()
    await test_endpoints()
    for s in (FR_A.server, FR_B.server, HA_B.server):
        await s.close()


if __name__ == "__main__":
    asyncio.run(main())
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    raise SystemExit(0 if passed == len(results) else 1)
