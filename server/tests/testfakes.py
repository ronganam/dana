"""Fakes shared by the standalone test scripts (not a test script itself).

- :class:`FakeHA`: one in-process Home Assistant (aiohttp): REST states /
  services / config / camera, and the WebSocket auth and entity registry
  messages, with its own token and entities; it records every request. Used
  by test_homeassistant_users.py, test_addons_api.py, test_attention.py and
  the local environment's stubs. Its WebSocket also serves ``subscribe_events``
  for ``state_changed``, ``subscribe_entities`` (Home Assistant's compressed
  messages) and ``unsubscribe_events``, recording each command.
- :class:`FakeWAHA`: one WAHA instance.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from aiohttp import web
from aiohttp.test_utils import TestServer


class FakeHA:
    """One Home Assistant instance: its token, states, exposed entities; it
    records every request (path, token)."""

    def __init__(self, name: str, token: str, states: Dict[str, Tuple[str, Dict[str, Any]]],
                 exposed: Dict[str, str], tz: str) -> None:
        self.name, self.token, self.tz = name, token, tz
        self.states = {eid: {"entity_id": eid, "state": s, "attributes": dict(a)} for eid, (s, a) in states.items()}
        self.exposed = exposed  # entity id -> registry name
        self.requests: List[Tuple[str, str]] = []  # (path, bearer token or WS auth token)
        self.service_calls: List[Tuple[str, Dict[str, Any]]] = []
        self.subscribers: List[Tuple[Any, int]] = []  # (WebSocket, subscription id), any kind
        # (id(WebSocket), subscription id) -> the entity ids of a subscribe_entities (absent: state_changed)
        self.filters: Dict[Tuple[int, int], set] = {}
        self.commands: List[Dict[str, Any]] = []  # every WebSocket command after auth, in order
        self.refuse_subscribe_entities = False  # an old Home Assistant without it
        self.down = False
        app = web.Application()
        app.router.add_get("/api/websocket", self.ws)
        app.router.add_get("/api/states", self.all_states)
        app.router.add_get("/api/states/{eid}", self.one_state)
        app.router.add_post("/api/services/{domain}/{service}", self.service)
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
        return web.json_response([])

    async def config(self, request):
        return self._auth(request) or web.json_response({"time_zone": self.tz})

    async def camera(self, request):
        return self._auth(request) or web.Response(body=f"jpeg-{self.name}".encode(), content_type="image/jpeg")

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
        async for m in ws:
            data = json.loads(m.data)
            kind, mid = data.get("type"), data.get("id")
            self.commands.append(data)
            if kind == "homeassistant/expose_entity/list":
                await ws.send_json({"id": mid, "type": "result", "success": True, "result": {
                    "exposed_entities": {eid: {"conversation": True} for eid in self.exposed}}})
            elif kind == "config/entity_registry/get":
                await ws.send_json({"id": mid, "type": "result", "success": True, "result": {
                    "name": self.exposed.get(data["entity_id"]), "aliases": []}})
            elif kind == "get_states":
                await ws.send_json({"id": mid, "type": "result", "success": True, "result": list(self.states.values())})
            elif kind == "subscribe_events" and data.get("event_type") == "state_changed":
                await ws.send_json({"id": mid, "type": "result", "success": True, "result": None})
                self.subscribers.append((ws, mid))
            elif kind == "subscribe_entities":
                if self.refuse_subscribe_entities:
                    await ws.send_json({"id": mid, "type": "result", "success": False,
                                        "error": {"code": "unknown_command", "message": "Unknown command."}})
                    continue
                wanted = set(data.get("entity_ids") or [])
                await ws.send_json({"id": mid, "type": "result", "success": True, "result": None})
                await ws.send_json({"id": mid, "type": "event", "event": {"a": {
                    eid: {"s": s["state"], "a": s["attributes"], "c": "ctx", "lc": 1.0}
                    for eid, s in self.states.items() if eid in wanted}}})
                self.subscribers.append((ws, mid))
                self.filters[(id(ws), mid)] = wanted
            elif kind == "unsubscribe_events":
                sub = data.get("subscription")
                found = (ws, sub) in self.subscribers
                self.subscribers = [(s, i) for s, i in self.subscribers if (s, i) != (ws, sub)]
                self.filters.pop((id(ws), sub), None)
                await ws.send_json({"id": mid, "type": "result", "success": True, "result": None} if found else
                                   {"id": mid, "type": "result", "success": False,
                                    "error": {"code": "not_found", "message": "Subscription not found."}})
        self.subscribers = [(s, i) for s, i in self.subscribers if s is not ws]
        self.filters = {k: v for k, v in self.filters.items() if k[0] != id(ws)}
        return ws

    def subscriptions(self) -> List[Optional[set]]:
        """The live subscriptions: None for a state_changed one, else the entity ids of a subscribe_entities."""
        return [self.filters.get((id(ws), mid)) for ws, mid in self.subscribers]

    async def push(self, entity_id: str, state: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        """Set an entity's state (``attributes`` replace its own when given; a new entity starts with
        none) and send ``state_changed`` to every subscribed WebSocket, as Home Assistant does."""
        old = self.states.get(entity_id)
        attrs = dict(attributes if attributes is not None else (old or {}).get("attributes") or {})
        new = {"entity_id": entity_id, "state": state, "attributes": attrs}
        self.states[entity_id] = new
        for ws, mid in list(self.subscribers):
            wanted = self.filters.get((id(ws), mid))
            if wanted is None:
                await ws.send_json({"id": mid, "type": "event", "event": {
                    "event_type": "state_changed", "data": {"entity_id": entity_id, "old_state": old, "new_state": new}}})
                continue
            if entity_id not in wanted:
                continue
            if old is None:
                await ws.send_json({"id": mid, "type": "event", "event": {"a": {entity_id: {"s": state, "a": attrs}}}})
                continue
            plus: Dict[str, Any] = {"lu": 2.0}
            if old["state"] != state:
                plus.update(s=state, lc=2.0)
            changed = {k: v for k, v in attrs.items() if old["attributes"].get(k) != v}
            if changed:
                plus["a"] = changed
            diff: Dict[str, Any] = {"+": plus}
            removed = [k for k in old["attributes"] if k not in attrs]
            if removed:
                diff["-"] = {"a": removed}
            await ws.send_json({"id": mid, "type": "event", "event": {"c": {entity_id: diff}}})

    async def drop_sockets(self) -> None:
        """Close every open WebSocket (Home Assistant restarting)."""
        for ws, _ in list(self.subscribers):
            await ws.close()
        self.subscribers.clear()

    def tokens_seen(self) -> set:
        return {t for _, t in self.requests}


class FakeWAHA:
    """One WAHA instance: its API key, its sessions (name → ``me``), contacts,
    chats (``groups``: more chats, group id → name) and messages; it records
    every request as (method, path, key, session)."""

    def __init__(self, name: str, key: str, sessions: Dict[str, Dict[str, str]], contact: Tuple[str, str],
                 chat_name: str, groups: Optional[Dict[str, str]] = None) -> None:
        self.name, self.key, self.sessions = name, key, sessions
        self.contact_id, self.contact_name = contact
        self.chat_name = chat_name
        self.groups = dict(groups or {})
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
                            "_data": {"pushName": self.contact_name}}}]
            + [{"id": gid, "name": gname} for gid, gname in self.groups.items()])

    async def messages(self, request):
        """A text, an image and a PDF with a caption (listed by name only:
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
            {"timestamp": 1792271421, "media": {"url": f"{self.root}/api/files/quote", "mimetype": "application/pdf"}})

    async def edit(self, request):
        return await self._auth(request) or web.json_response({}, status=200)
