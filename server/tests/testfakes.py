"""Fakes shared by the standalone test scripts (not a test script itself).

- :class:`FakeHA`: one in-process Home Assistant (aiohttp): REST states /
  services / history / config / camera, and the WebSocket auth, entity
  registry and ``subscribe_entities`` messages, with its own token and
  entities; it records every request. Used by test_homeassistant_users.py
  and test_addons_api.py.
- :class:`FakeWAHA`: one WAHA instance.
- :class:`FakeBankWorker`: the bank-worker sidecar (bank-worker/README.md)
  with scripted outcomes per profile. Used by test_money.py.
"""

import asyncio
import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from aiohttp import web
from aiohttp.test_utils import TestServer


class FakeHA:
    """One Home Assistant instance: its token, states, exposed entities; it
    records every request (path, token) and pushes state changes to its
    ``subscribe_entities`` subscribers as Home Assistant does (``a`` / ``c``
    with ``+`` / ``-`` diffs / ``r``)."""

    def __init__(self, name: str, token: str, states: Dict[str, Tuple[str, Dict[str, Any]]],
                 exposed: Dict[str, str], tz: str) -> None:
        self.name, self.token, self.tz = name, token, tz
        self.states = {eid: {"entity_id": eid, "state": s, "attributes": dict(a)} for eid, (s, a) in states.items()}
        self.exposed = exposed  # entity id -> registry name
        self.requests: List[Tuple[str, str]] = []  # (path, bearer token or WS auth token)
        self.service_calls: List[Tuple[str, Dict[str, Any]]] = []
        self.subscriptions: List[List[str]] = []
        self.subs: Dict[web.WebSocketResponse, Tuple[int, List[str]]] = {}
        self.sockets: set = set()
        self.down = False
        self.history: List[Dict[str, Any]] = []
        app = web.Application()
        app.router.add_get("/api/websocket", self.ws)
        app.router.add_get("/api/states", self.all_states)
        app.router.add_get("/api/states/{eid}", self.one_state)
        app.router.add_post("/api/services/{domain}/{service}", self.service)
        app.router.add_get("/api/history/period/{since}", self.history_api)
        app.router.add_get("/api/config", self.config)
        app.router.add_get("/api/camera_proxy/{eid}", self.camera)
        self.server = TestServer(app)

    async def start(self) -> None:
        await self.server.start_server()
        self.url = str(self.server.make_url("")).rstrip("/")

    def _auth(self, request: web.Request) -> Optional[web.Response]:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        self.requests.append((request.path, token))
        if self.down:
            return web.Response(status=503)
        if token != self.token:
            return web.json_response({"message": "unauthorized"}, status=401)
        return None

    async def all_states(self, request):
        return self._auth(request) or web.json_response(list(self.states.values()))

    async def one_state(self, request):
        refused = self._auth(request)
        if refused:
            return refused
        s = self.states.get(request.match_info["eid"])
        return web.json_response(s) if s else web.json_response({"message": "not found"}, status=404)

    async def service(self, request):
        refused = self._auth(request)
        if refused:
            return refused
        body = await request.json()
        name = f"{request.match_info['domain']}.{request.match_info['service']}"
        self.service_calls.append((name, body))
        if name == "weather.get_forecasts":
            return web.json_response({"service_response": {body["entity_id"]: {"forecast": [
                {"temperature": 30 if self.name == "B" else 20, "templow": 10, "precipitation_probability": 5}]}}})
        return web.json_response([])

    async def history_api(self, request):
        refused = self._auth(request)
        if refused:
            return refused
        self.service_calls.append(("history", {"entity": request.query.get("filter_entity_id")}))
        return web.json_response([self.history])

    async def config(self, request):
        return self._auth(request) or web.json_response({"time_zone": self.tz})

    async def camera(self, request):
        return self._auth(request) or web.Response(body=f"jpeg-{self.name}".encode(), content_type="image/jpeg")

    @staticmethod
    def _compressed(state: Dict[str, Any]) -> Dict[str, Any]:
        return {"s": state["state"], "a": dict(state["attributes"]), "c": "ctx", "lc": 1.0}

    async def ws(self, request):
        if self.down:
            self.requests.append(("/api/websocket", ""))
            return web.Response(status=503)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required"})
        auth = await ws.receive_json()
        self.requests.append(("/api/websocket", auth.get("access_token", "")))
        if auth.get("access_token") != self.token:
            await ws.send_json({"type": "auth_invalid"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok"})
        self.sockets.add(ws)
        try:
            async for m in ws:
                data = json.loads(m.data)
                kind, mid = data.get("type"), data.get("id")
                if kind == "homeassistant/expose_entity/list":
                    await ws.send_json({"id": mid, "type": "result", "success": True, "result": {
                        "exposed_entities": {eid: {"conversation": True} for eid in self.exposed}}})
                elif kind == "config/entity_registry/get":
                    await ws.send_json({"id": mid, "type": "result", "success": True, "result": {
                        "name": self.exposed.get(data["entity_id"]), "aliases": []}})
                elif kind == "subscribe_entities":
                    ids = list(data["entity_ids"])
                    self.subscriptions.append(ids)
                    self.subs[ws] = (mid, ids)
                    await ws.send_json({"id": mid, "type": "result", "success": True, "result": None})
                    await ws.send_json({"id": mid, "type": "event", "event": {
                        "a": {eid: self._compressed(self.states[eid]) for eid in ids if eid in self.states}}})
        finally:
            self.sockets.discard(ws)
            self.subs.pop(ws, None)
        return ws

    async def set_state(self, eid: str, state: str, attrs: Optional[Dict[str, Any]] = None, push: bool = True) -> None:
        old = self.states.get(eid)
        new_attrs = dict(attrs) if attrs is not None else dict((old or {}).get("attributes") or {})
        self.states[eid] = {"entity_id": eid, "state": state, "attributes": new_attrs}
        if not push:
            return
        if old is None:
            event = {"a": {eid: self._compressed(self.states[eid])}}
        else:
            plus: Dict[str, Any] = {"lu": 2.0}
            if old["state"] != state:
                plus["s"], plus["lc"] = state, 2.0
            changed = {k: v for k, v in new_attrs.items() if old["attributes"].get(k) != v}
            if changed:
                plus["a"] = changed
            diff: Dict[str, Any] = {"+": plus}
            removed = [k for k in old["attributes"] if k not in new_attrs]
            if removed:
                diff["-"] = {"a": removed}
            event = {"c": {eid: diff}}
        await self._push(eid, event)

    async def remove(self, eid: str) -> None:
        self.states.pop(eid, None)
        await self._push(eid, {"r": [eid]})

    async def _push(self, eid: str, event: Dict[str, Any]) -> None:
        for ws, (mid, ids) in list(self.subs.items()):
            if eid in ids:
                await ws.send_json({"id": mid, "type": "event", "event": event})

    async def drop_connections(self) -> None:
        for ws in list(self.sockets):
            await ws.close()

    def tokens_seen(self) -> set:
        return {t for _, t in self.requests}


class FakeWAHA:
    """One WAHA instance: its API key, its sessions (name → ``me``), contacts,
    chats and messages; it records every request as (method, path, key, session)."""

    def __init__(self, name: str, key: str, sessions: Dict[str, Dict[str, str]], contact: Tuple[str, str],
                 chat_name: str) -> None:
        self.name, self.key, self.sessions = name, key, sessions
        self.contact_id, self.contact_name = contact
        self.chat_name = chat_name
        self.requests: List[Tuple[str, str, str, str]] = []
        self.sent: List[Dict[str, Any]] = []
        self.forwarded: List[Dict[str, Any]] = []
        self.message_queries: List[Dict[str, List[str]]] = []
        self.down = False
        app = web.Application()
        r = app.router
        r.add_get("/api/sessions", self.sessions_api)
        r.add_get("/api/contacts/all", self.contacts)
        r.add_get("/api/contacts/check-exists", self.check_exists)
        r.add_post("/api/sendText", self.send)
        r.add_post("/api/sendFile", self.send)
        r.add_post("/api/forwardMessage", self.forward)
        r.add_get("/api/files/{name}", self.file)
        r.add_get("/api/{session}/chats/overview", self.chats)
        r.add_get("/api/{session}/chats", self.chats)
        r.add_get("/api/{session}/chats/{chat}/messages", self.messages)
        r.add_get("/api/{session}/chats/{chat}/messages/{mid}", self.one_message)
        r.add_put("/api/{session}/chats/{chat}/messages/{mid}", self.edit)
        r.add_delete("/api/{session}/chats/{chat}/messages/{mid}", self.edit)
        self.server = TestServer(app)

    async def start(self) -> None:
        await self.server.start_server()
        self.root = str(self.server.make_url("")).rstrip("/")
        self.url = self.root + "/api/"

    async def _auth(self, request: web.Request) -> Optional[web.Response]:
        session = request.match_info.get("session") or request.query.get("session") or ""
        if request.method == "POST":
            session = (await request.json()).get("session", "")
        self.requests.append((request.method, request.path, request.headers.get("X-Api-Key", ""), session))
        if self.down:
            return web.Response(status=503)
        if request.headers.get("X-Api-Key") != self.key:
            return web.json_response({"message": "unauthorized"}, status=401)
        return None

    def calls(self) -> List[Tuple[str, str, str, str]]:
        out, self.requests = self.requests, []
        return out

    async def sessions_api(self, request):
        return await self._auth(request) or web.json_response(
            [{"name": n, "status": "WORKING", "me": me} for n, me in self.sessions.items()])

    async def contacts(self, request):
        return await self._auth(request) or web.json_response([{"id": self.contact_id, "name": self.contact_name}])

    async def check_exists(self, request):
        return await self._auth(request) or web.json_response(
            {"numberExists": True, "chatId": f"{request.query.get('phone')}@c.us"})

    async def send(self, request):
        refused = await self._auth(request)
        if refused:
            return refused
        self.sent.append(await request.json())
        return web.json_response({"id": f"true_{self.name}_MSG"}, status=201)

    async def forward(self, request):
        """Forwards a message it lists (422 for any other id, as WAHA's NOWEB store)."""
        refused = await self._auth(request)
        if refused:
            return refused
        body = await request.json()
        if body.get("messageId") not in {f"false_{self.contact_id}_{m}" for m in ("m1", "m2", "m3")}:
            return web.json_response({"message": f"Message with id '{body.get('messageId')}' not found"}, status=422)
        self.forwarded.append(body)
        return web.json_response({"id": f"true_{body.get('chatId')}_FWD"}, status=201)

    async def file(self, request):
        return await self._auth(request) or web.Response(body=f"bytes of {self.name}".encode(),
                                                         content_type="image/jpeg")

    async def chats(self, request):
        return await self._auth(request) or web.json_response([{
            "id": self.contact_id, "name": self.chat_name,
            "lastMessage": {"body": f"hello from {self.name}", "fromMe": False, "ack": 3, "timestamp": 1792271419,
                            "_data": {"pushName": self.contact_name}}}])

    async def messages(self, request):
        """A text, an image (downloaded) and a PDF with a caption (listed by name only:
        WAHA downloads just the mimetypes asked in ``downloadMediaMimetypes``)."""
        refused = await self._auth(request)
        if refused:
            return refused
        self.message_queries.append({k: request.query.getall(k) for k in request.query})
        mid = lambda m: f"false_{self.contact_id}_{m}"  # noqa: E731
        return web.json_response([
            {"id": mid("m1"), "timestamp": 1792271419, "from": self.contact_id, "fromMe": False,
             "body": f"text from {self.name}", "_data": {"pushName": self.contact_name}},
            {"id": mid("m2"), "timestamp": 1792271420, "from": self.contact_id, "fromMe": False, "body": None,
             "hasMedia": True, "media": {"url": f"{self.root}/api/files/pic.jpg", "mimetype": "image/jpeg"},
             "_data": {"pushName": self.contact_name}},
            {"id": mid("m3"), "timestamp": 1792271421, "from": self.contact_id, "fromMe": False,
             "body": f"quote from {self.name}", "hasMedia": True,
             "media": {"url": None, "mimetype": "application/pdf", "filename": f"quote-{self.name}.pdf"},
             "_data": {"pushName": self.contact_name}}])

    async def one_message(self, request):
        return await self._auth(request) or web.json_response(
            {"media": {"url": f"{self.root}/api/files/voice.ogg"}})

    async def edit(self, request):
        return await self._auth(request) or web.json_response({}, status=200)


@dataclass
class BankOutcome:
    """What a job of one profile does: sign in (asking for ``code`` first when
    set), then end with ``accounts`` (and Psagot's ``device`` / ``device_dropped``),
    or fail with ``error_type``; with ``unreachable`` the worker drops the
    connection when the job is started."""
    accounts: List[Dict[str, Any]] = field(default_factory=list)
    code: Optional[str] = None
    error_type: Optional[str] = None
    error: str = ""
    unreachable: bool = False
    device: Optional[Dict[str, str]] = None
    device_dropped: bool = False


class FakeBankWorker:
    """The bank-worker sidecar's HTTP contract, in process: jobs move one state
    per poll (``starting`` → ``logging_in`` → [``needs_code`` until the expected
    code is posted] → ``scraping`` → ``done`` / ``failed``). Every call but
    ``/health`` must carry the token read from ``token_file`` (401 otherwise).
    It records the jobs it started (company, profile, start_date, credentials, device),
    the codes posted, the jobs cancelled and the profiles deleted."""

    MAX_WAIT = 2.0  # long polls are capped here to keep tests fast

    def __init__(self, token_file: Union[str, Path]) -> None:
        self.token_file = Path(token_file)
        self.outcomes: Dict[str, BankOutcome] = {}
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self.started: List[Dict[str, Any]] = []
        self.codes: List[Tuple[str, str]] = []
        self.cancelled: List[str] = []
        self.deleted_profiles: List[str] = []
        self.unauthorized = 0
        self._ids = itertools.count(1)
        app = web.Application()
        app.router.add_get("/health", self.health)
        app.router.add_post("/jobs", self.start_job)
        app.router.add_get("/jobs/{id}", self.get_job)
        app.router.add_post("/jobs/{id}/code", self.post_code)
        app.router.add_delete("/jobs/{id}", self.cancel_job)
        app.router.add_delete("/profiles/{profile}", self.delete_profile)
        self.server = TestServer(app)

    async def start(self) -> None:
        await self.server.start_server()
        self.url = str(self.server.make_url("")).rstrip("/")

    async def close(self) -> None:
        await self.server.close()

    def script(self, profile: str, **outcome: Any) -> BankOutcome:
        """Set what the next jobs of ``profile`` do (:class:`BankOutcome` fields);
        profile ``"*"``: every profile without its own script."""
        self.outcomes[profile] = BankOutcome(**outcome)
        return self.outcomes[profile]

    def _authorized(self, request: web.Request) -> bool:
        try:
            expected = self.token_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            expected = ""
        ok = bool(expected) and request.headers.get("Authorization", "") == f"Bearer {expected}"
        if not ok:
            self.unauthorized += 1
        return ok

    @staticmethod
    def _denied() -> web.Response:
        return web.json_response({"error": "unauthorized"}, status=401)

    def _view(self, job: Dict[str, Any]) -> Dict[str, Any]:
        out = {k: job[k] for k in ("id", "company", "state", "error_type", "error", "accounts", "started_at",
                                   "updated_at")}
        done = job["state"] == "done"
        out["device"] = job["outcome"].device if done else None
        out["device_dropped"] = job["outcome"].device_dropped if done else False
        return out

    def _set(self, job: Dict[str, Any], state: str) -> None:
        job["state"] = state
        job["updated_at"] = "2026-11-18T07:00:09.554Z"
        job["changed"].set()
        job["changed"] = asyncio.Event()

    def _advance(self, job: Dict[str, Any]) -> None:
        outcome: BankOutcome = job["outcome"]
        state = job["state"]
        if state == "starting":
            self._set(job, "logging_in")
        elif state == "logging_in":
            if outcome.code and not job["code_ok"]:
                self._set(job, "needs_code")
            elif outcome.error_type:
                job["error_type"], job["error"] = outcome.error_type, outcome.error or outcome.error_type.lower()
                self._set(job, "failed")
            else:
                self._set(job, "scraping")
        elif state == "scraping":
            job["accounts"] = json.loads(json.dumps(outcome.accounts))
            self._set(job, "done")

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "version": "fake", "running": sum(
            1 for j in self.jobs.values() if j["state"] not in ("done", "failed", "cancelled"))})

    async def start_job(self, request: web.Request) -> web.StreamResponse:
        if not self._authorized(request):
            return self._denied()
        body = await request.json()
        profile = str(body.get("profile") or "")
        outcome = self.outcomes.get(profile) or self.outcomes.get("*") or BankOutcome()
        if outcome.unreachable:
            assert request.transport is not None
            request.transport.close()
            return web.Response(status=500)
        busy = next((j for j in self.jobs.values()
                     if j["profile"] == profile and j["state"] not in ("done", "failed", "cancelled")), None)
        if busy is not None:
            return web.json_response({"error": "busy", "job": busy["id"]}, status=409)
        job_id = f"job{next(self._ids)}"
        self.started.append({"id": job_id, "company": body.get("company"), "profile": profile,
                             "start_date": body.get("start_date"), "credentials": body.get("credentials"),
                             "device": body.get("device")})
        self.jobs[job_id] = {"id": job_id, "company": body.get("company"), "profile": profile, "state": "starting",
                             "error_type": None, "error": None, "accounts": None, "outcome": outcome,
                             "code_ok": False, "changed": asyncio.Event(),
                             "started_at": "2026-11-18T07:00:03.120Z", "updated_at": "2026-11-18T07:00:03.120Z"}
        return web.json_response({"id": job_id, "state": "starting"}, status=201)

    async def get_job(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return self._denied()
        job = self.jobs.get(request.match_info["id"])
        if job is None:
            return web.json_response({"error": "not_found"}, status=404)
        seen = request.query.get("seen")
        if job["state"] == seen:
            self._advance(job)
        if job["state"] == seen:  # waiting (for a code): a long poll until it changes
            wait = min(float(request.query.get("wait") or 0), self.MAX_WAIT)
            try:
                await asyncio.wait_for(job["changed"].wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
        return web.json_response(self._view(job))

    async def post_code(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return self._denied()
        job = self.jobs.get(request.match_info["id"])
        if job is None:
            return web.json_response({"error": "not_found"}, status=404)
        code = str((await request.json()).get("code") or "")
        if job["state"] != "needs_code":
            return web.json_response({"error": "not_waiting"}, status=409)
        self.codes.append((job["id"], code))
        if code == job["outcome"].code:
            job["code_ok"] = True
            self._set(job, "logging_in")
        else:
            job["error_type"], job["error"] = "CODE_REJECTED", "the bank refused the code"
            self._set(job, "failed")
        return web.json_response({"ok": True}, status=202)

    async def cancel_job(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return self._denied()
        job = self.jobs.get(request.match_info["id"])
        if job is None:
            return web.json_response({"error": "not_found"}, status=404)
        self.cancelled.append(job["id"])
        if job["state"] not in ("done", "failed", "cancelled"):
            self._set(job, "cancelled")
        return web.json_response({"ok": True})

    async def delete_profile(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return self._denied()
        profile = request.match_info["profile"]
        if any(j["profile"] == profile and j["state"] not in ("done", "failed", "cancelled")
               for j in self.jobs.values()):
            return web.json_response({"error": "busy"}, status=409)
        self.deleted_profiles.append(profile)
        return web.json_response({"ok": True})

