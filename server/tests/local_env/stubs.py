"""Stub services of the local test environment (docs/local-testing.md).

One process, one port per service, on the environment's internal Docker
network (the app reaches them as ``http://stubs:<port>``):

====  =========================================================================
8123  Home Assistant A (the first admin's): ``testfakes.FakeHA``
8124  Home Assistant B (the second test user's): ``testfakes.FakeHA``
3000  WAHA with two sessions / WhatsApp accounts: ``testfakes.FakeWAHA``
5000  Frigate (events, motion, previews, clips, WebRTC, version)
9100  OpenAI-compatible model endpoint for LiteLLM (chat, streamed or not,
      structured output, audio transcription)
8081  Telegram Bot API (getUpdates long polling, sendMessage, …)
8095  ntfy (a UnifiedPush server: records the pings Dana POSTs to an endpoint)
9000  control API, used by the end-to-end script (tests/local_env/e2e.py)
====  =========================================================================

Every stub records what it was asked; the control API reads the records,
changes Home Assistant entities and scripts the model's replies. Nothing here
reaches the internet. Only aiohttp (already a dependency of the app).
"""

import asyncio
import json
import logging
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

from aiohttp import web

from testfakes import FakeHA, FakeWAHA

logging.basicConfig(level=logging.INFO, format="%(asctime)s stubs %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("stubs")

TZ = "Asia/Jerusalem"

# --- Identities of the environment (the end-to-end script uses the same values) ------------------

HA_A_TOKEN = "local-ha-token-a"
HA_B_TOKEN = "local-ha-token-b"
WAHA_KEY = "local-waha-key"
ADMIN_ACCOUNT = "972500000001@c.us"   # WhatsApp account of session "default" (the first admin's)
MEMBER_ACCOUNT = "972500000002@c.us"  # WhatsApp account of session "maya" (the second test user's)


def _ha_a() -> FakeHA:
    """The first admin's Home Assistant: their entities, zones, battery sensor and devices."""
    return FakeHA("A", HA_A_TOKEN, {
        "person.robin": ("home", {"friendly_name": "Robin"}),
        "zone.home": ("0", {"friendly_name": "Home", "latitude": 32.08, "longitude": 34.78, "radius": 100}),
        "zone.work": ("0", {"friendly_name": "Work", "latitude": 32.1, "longitude": 34.8, "radius": 150}),
        "zone.office": ("0", {"friendly_name": "Office", "latitude": 32.11, "longitude": 34.81, "radius": 50}),
        "sensor.robin_phone_battery": ("80", {"unit_of_measurement": "%", "device_class": "battery"}),
        "light.living_room": ("off", {"friendly_name": "Living room light"}),
        "cover.gate": ("closed", {"friendly_name": "Gate"}),
    }, exposed={"light.living_room": "Living room light", "cover.gate": "Gate"}, tz=TZ)


def _ha_b() -> FakeHA:
    """The second test user's own Home Assistant (other token, other entities)."""
    return FakeHA("B", HA_B_TOKEN, {
        "person.maya": ("home", {"friendly_name": "Maya"}),
        "zone.home": ("0", {"friendly_name": "Home", "latitude": 31.77, "longitude": 35.21, "radius": 100}),
        "zone.gym": ("0", {"friendly_name": "Gym", "latitude": 31.78, "longitude": 35.22, "radius": 80}),
        "media_player.maya_tv": ("off", {"friendly_name": "Maya's TV"}),
        "light.maya_lamp": ("off", {"friendly_name": "Maya's lamp"}),
    }, exposed={"light.maya_lamp": "Maya's lamp"}, tz=TZ)


# --- Frigate -------------------------------------------------------------------------------------

class FakeFrigate:
    """Frigate's few endpoints Dana proxies; records every request path."""

    def __init__(self) -> None:
        self.requests: List[str] = []
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.handle)
        self.app = app

    async def handle(self, request: web.Request) -> web.StreamResponse:
        path = request.path
        self.requests.append(f"{request.method} {path}")
        now = time.time()
        if path == "/api/version":
            return web.Response(text="0.14.1-local")
        if path == "/api/events":
            return web.json_response([
                {"id": f"{now - 600:.6f}-ev1", "camera": request.query.get("cameras", "yard"), "label": "dog",
                 "start_time": now - 600, "end_time": now - 590, "has_clip": True, "has_snapshot": True,
                 "top_score": 0.81, "data": {"score": 0.8}}])
        if path == "/api/review/activity/motion":
            return web.json_response([])
        if path.endswith(".gif"):
            return web.Response(body=b"GIF89a-local", content_type="image/gif")
        if path.endswith(".mp4"):
            return web.Response(body=b"\x00\x00\x00\x18ftypmp42-local", content_type="video/mp4")
        if path == "/api/go2rtc/webrtc":
            return web.json_response({"type": "answer", "sdp": "v=0 local"})
        return web.json_response({"message": "not found"}, status=404)


# --- OpenAI-compatible model endpoint -------------------------------------------------------------

def minimal_instance(schema: Any, defs: Optional[Dict[str, Any]] = None) -> Any:
    """The smallest value valid for a JSON Schema: required properties only,
    the first enum value, empty strings / lists, zero."""
    if not isinstance(schema, dict):
        return None
    defs = defs if defs is not None else (schema.get("$defs") or schema.get("definitions") or {})
    if "$ref" in schema:
        return minimal_instance(defs.get(schema["$ref"].split("/")[-1], {}), defs)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = schema[key]
            non_null = [o for o in options if o.get("type") != "null"]
            return minimal_instance((non_null or options)[0], defs) if not any(
                o.get("type") == "null" for o in options) else None
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    kind = schema.get("type")
    if isinstance(kind, list):
        if "null" in kind:
            return None
        kind = kind[0]
    if kind == "object" or "properties" in schema:
        props = schema.get("properties") or {}
        return {name: minimal_instance(props.get(name, {}), defs) for name in schema.get("required") or []}
    if kind == "array":
        return []
    if kind == "string":
        return ""
    if kind in ("number", "integer"):
        return 0
    if kind == "boolean":
        return False
    return None


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
    return ""


# The aisle lookup's answer: the first aisle with a word found in the name (English and Hebrew, as
# people type them), else pantry. Rough on purpose; enough for realistic demo lists.
AISLE_WORDS = {
    "produce": ("apple", "banana", "tomato", "cucumber", "lettuce", "onion", "garlic", "potato", "carrot", "lemon",
                "avocado", "pepper", "herb", "parsley", "coriander", "mint", "fruit", "grape", "orange", "berr",
                "עגבני", "מלפפון", "בצל", "שום", "תפוח", "בננה", "לימון", "אבוקדו", "פלפל", "חסה", "פטרוזיליה",
                "כוסברה", "נענע", "גזר", "תפו\"א", "ענבים", "תות"),
    "bakery": ("bread", "pita", "challah", "bagel", "bun", "baguette", "croissant", "לחם", "פיתה", "חלה", "לחמני",
               "בגט"),
    "dairy": ("milk", "yogurt", "yoghurt", "cheese", "butter", "cream", "egg", "labneh", "cottage", "חלב", "יוגורט",
              "גבינ", "חמאה", "שמנת", "ביצים", "לבנה", "קוטג", "מעדן"),
    "meat": ("chicken", "beef", "salmon", "fish", "tuna fillet", "steak", "turkey", "schnitzel", "עוף", "בקר", "סלמון",
             "דג", "שניצל", "הודו", "קציצות", "פרגית"),
    "snacks": ("chocolate", "chips", "cookie", "biscuit", "candy", "bamba", "bissli", "שוקולד", "במבה", "ביסלי",
               "עוגיות", "חטיף", "וופל"),
    "drinks": ("water", "juice", "cola", "soda", "beer", "wine", "coffee", "tea ", "מים", "מיץ", "קולה", "סודה",
               "בירה", "יין", "קפה", "תה"),
    "frozen": ("frozen", "ice cream", "peas", "קפוא", "גלידה", "אפונה", "בורקס"),
    "household": ("soap", "toilet", "paper towel", "detergent", "dish", "trash bag", "sponge", "bleach", "foil",
                  "battery", "batteries", "סבון", "נייר טואלט", "מגבות", "אקונומיקה", "שקיות", "ספוג", "נוזל כלים",
                  "טואלט", "סוללות", "אלומיניום"),
    "care": ("shampoo", "toothpaste", "toothbrush", "deodorant", "conditioner", "razor", "vitamin", "painkiller",
             "שמפו", "משחת שיניים", "מברשת", "דאודורנט", "מרכך", "אקמול", "ויטמין"),
    "baby": ("diaper", "wipes", "formula", "חיתול", "מגבונים", "מטרנה"),
    "pets": ("cat food", "dog food", "litter", "אוכל לחתול", "אוכל לכלב", "חול לחתול"),
}


def shopping_aisles(prompt: str, schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The shopping aisle lookup (addons/shopping/aisles.py): every item
    under "Items:" with an aisle picked by a few known words, else ``pantry``.
    None for any other structured request."""
    items = (schema.get("properties") or {}).get("items", {}).get("items", {})
    if "aisle" not in (items.get("properties") or {}) or "\nItems:\n" not in prompt:
        return None
    names = [line[2:] for line in prompt.split("\nItems:\n", 1)[1].splitlines() if line.startswith("- ")]
    pick = lambda n: next((a for a, words in AISLE_WORDS.items() if any(w in n.lower() for w in words)), "pantry")  # noqa: E731
    return {"items": [{"name": n, "aisle": pick(n)} for n in names]}


class FakeModel:
    """Chat completions in OpenAI's shape. Requests with tools are agent turns:
    they get the next scripted reply whose ``match`` text appears in the
    conversation (``POST /llm/script``), else a plain "Done." Requests without
    tools are background jobs: structured output gets the minimal instance of
    its schema, plain text a short neutral answer. Every request is recorded."""

    def __init__(self) -> None:
        self.script: List[Dict[str, Any]] = []
        self.requests: List[Dict[str, Any]] = []
        self.transcript = "Hello from the local fake transcriber."
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_post("/v1/chat/completions", self.chat)
        app.router.add_post("/v1/audio/transcriptions", self.transcriptions)
        app.router.add_get("/v1/models", self.models)
        self.app = app

    async def models(self, request: web.Request) -> web.Response:
        return web.json_response({"object": "list", "data": [{"id": "dana-local", "object": "model"}]})

    async def transcriptions(self, request: web.Request) -> web.Response:
        await request.read()
        self.requests.append({"kind": "transcription", "at": time.time()})
        return web.json_response({"text": self.transcript})

    def _pick(self, conversation: str) -> Optional[Dict[str, Any]]:
        for i, entry in enumerate(self.script):
            if entry.get("match", "") in conversation:
                return self.script.pop(i)
        return None

    async def chat(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        messages = body.get("messages") or []
        tools = [t.get("function", {}).get("name") for t in body.get("tools") or []]
        system = "\n".join(_content_text(m.get("content")) for m in messages if m.get("role") == "system")
        conversation = "\n".join(_content_text(m.get("content")) for m in messages if m.get("role") in ("user", "tool"))
        record: Dict[str, Any] = {"kind": "chat", "at": time.time(), "model": body.get("model"),
                                  "stream": bool(body.get("stream")), "tools": tools, "system": system,
                                  "conversation": conversation,
                                  "last_role": (messages[-1].get("role") if messages else None),
                                  "response_format": bool(body.get("response_format"))}
        reply: Dict[str, Any]
        if body.get("tools"):
            entry = self._pick(conversation)
            record["scripted"] = entry
            reply = (entry or {}).get("reply") or {"text": "Done."}
        elif isinstance(body.get("response_format"), dict) and body["response_format"].get("type") == "json_schema":
            schema = body["response_format"].get("json_schema", {}).get("schema", {})
            aisles = shopping_aisles(conversation, schema)
            reply = {"text": json.dumps(aisles if aisles is not None else minimal_instance(schema))}
        else:
            # Plain background helpers: the task emoji (max_tokens 5) wants an
            # emoji, the task list picker a list name; anything else short text.
            reply = {"text": "📝" if (body.get("max_tokens") or body.get("max_completion_tokens") or 99) <= 5
                     else "General"}
        self.requests.append(record)
        model = body.get("model") or "dana-local"
        if body.get("stream"):
            return await self._stream(request, model, reply)
        return web.json_response(self._whole(model, reply))

    @staticmethod
    def _tool_calls(reply: Dict[str, Any]) -> List[Dict[str, Any]]:
        calls = reply.get("tools") or ([reply["tool"]] if reply.get("tool") else [])
        return [{"id": c.get("id") or f"call_{uuid.uuid4().hex[:8]}", "type": "function",
                 "function": {"name": c["name"], "arguments": json.dumps(c.get("args") or {})}} for c in calls]

    def _whole(self, model: str, reply: Dict[str, Any]) -> Dict[str, Any]:
        calls = self._tool_calls(reply)
        message: Dict[str, Any] = {"role": "assistant", "content": None if calls else reply.get("text", "")}
        if calls:
            message["tool_calls"] = calls
        return {"id": f"chatcmpl-{uuid.uuid4().hex[:12]}", "object": "chat.completion", "created": int(time.time()),
                "model": model, "choices": [{"index": 0, "message": message,
                                             "finish_reason": "tool_calls" if calls else "stop"}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 12, "total_tokens": 132}}

    async def _stream(self, request: web.Request, model: str, reply: Dict[str, Any]) -> web.StreamResponse:
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await resp.prepare(request)
        cid, created = f"chatcmpl-{uuid.uuid4().hex[:12]}", int(time.time())

        async def send(delta: Dict[str, Any], finish: Optional[str] = None, usage: Optional[Dict] = None) -> None:
            chunk: Dict[str, Any] = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                                     "choices": [] if usage else [{"index": 0, "delta": delta,
                                                                   "finish_reason": finish}]}
            if usage:
                chunk["usage"] = usage
            await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())

        await send({"role": "assistant", "content": ""})
        for piece in reply.get("reasoning") or []:
            await send({"reasoning_content": piece})
        calls = self._tool_calls(reply)
        if calls:
            for i, call in enumerate(calls):
                await send({"tool_calls": [{"index": i, "id": call["id"], "type": "function",
                                            "function": {"name": call["function"]["name"], "arguments": ""}}]})
                await send({"tool_calls": [{"index": i, "function": {"arguments": call["function"]["arguments"]}}]})
            await send({}, "tool_calls")
        else:
            words = (reply.get("text") or "").split(" ")
            for i, word in enumerate(words):
                await send({"content": word + (" " if i < len(words) - 1 else "")})
            await send({}, "stop")
        await send({}, usage={"prompt_tokens": 120, "completion_tokens": 12, "total_tokens": 132})
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp


# --- Telegram Bot API ----------------------------------------------------------------------------

class FakeTelegram:
    """The Bot API methods aiogram uses (``/bot<token>/<method>``): getMe,
    getUpdates (long polling, fed by ``POST /telegram/update``), deleteWebhook,
    sendMessage and the other send / edit / answer methods (recorded)."""

    BOT = {"id": 900000001, "is_bot": True, "first_name": "Dana (local)", "username": "dana_local_bot",
           "can_join_groups": True, "can_read_all_group_messages": False, "supports_inline_queries": False}

    def __init__(self) -> None:
        self.sent: List[Dict[str, Any]] = []
        self.updates: List[Dict[str, Any]] = []
        self.next_update = 1
        self.next_message = 1000
        self.polls = 0
        self.arrived = asyncio.Event()
        app = web.Application()
        app.router.add_post("/bot{token}/{method}", self.method)
        app.router.add_get("/bot{token}/{method}", self.method)
        self.app = app

    @staticmethod
    async def _params(request: web.Request) -> Dict[str, Any]:
        params: Dict[str, Any] = dict(request.query)
        if request.content_type == "application/json":
            params.update(await request.json())
        elif request.can_read_body:
            form = await request.post()
            for k, v in form.items():
                params[k] = v if isinstance(v, str) else f"<file {getattr(v, 'filename', '')}>"
        for k in ("chat_id", "offset", "timeout", "message_id", "limit"):
            if isinstance(params.get(k), str) and params[k].lstrip("-").isdigit():
                params[k] = int(params[k])
        for k in ("reply_markup",):
            if isinstance(params.get(k), str):
                try:
                    params[k] = json.loads(params[k])
                except ValueError:
                    pass
        return params

    def _message(self, chat_id: Any, text: str = "") -> Dict[str, Any]:
        self.next_message += 1
        chat_type = "private" if isinstance(chat_id, int) and chat_id > 0 else "supergroup"
        chat: Dict[str, Any] = {"id": chat_id, "type": chat_type}
        if chat_type != "private":
            chat["title"] = "Group"
        return {"message_id": self.next_message, "date": int(time.time()), "chat": chat,
                "from": self.BOT, "text": text}

    async def method(self, request: web.Request) -> web.Response:
        name = request.match_info["method"]
        params = await self._params(request)
        lname = name.lower()
        if lname == "getme":
            return web.json_response({"ok": True, "result": self.BOT})
        if lname == "getupdates":
            self.polls += 1
            offset = int(params.get("offset") or 0)
            self.updates = [u for u in self.updates if u["update_id"] >= offset]
            if not self.updates:
                self.arrived.clear()
                try:
                    await asyncio.wait_for(self.arrived.wait(), timeout=min(float(params.get("timeout") or 0), 10))
                except asyncio.TimeoutError:
                    pass
            return web.json_response({"ok": True, "result": list(self.updates)})
        if lname.startswith("send") or lname.startswith("edit") or lname == "copymessage":
            record = {"method": name, "chat_id": params.get("chat_id"), "text": params.get("text") or
                      params.get("caption") or "", "reply_markup": params.get("reply_markup"), "at": time.time()}
            if lname != "sendchataction":
                self.sent.append(record)
            if lname == "sendchataction":
                return web.json_response({"ok": True, "result": True})
            return web.json_response({"ok": True, "result": self._message(params.get("chat_id"), record["text"])})
        # deleteWebhook, answerCallbackQuery, setMyCommands, deleteMessage, …
        return web.json_response({"ok": True, "result": True})

    def inject(self, update: Dict[str, Any]) -> int:
        update = {"update_id": self.next_update, **update}
        self.next_update += 1
        self.updates.append(update)
        self.arrived.set()
        return update["update_id"]

    def message_update(self, from_id: int, chat_id: int, text: str, first_name: str = "Test") -> int:
        self.next_message += 1
        chat_type = "private" if chat_id > 0 else "supergroup"
        chat: Dict[str, Any] = {"id": chat_id, "type": chat_type}
        if chat_type == "private":
            chat["first_name"] = first_name
        else:
            chat["title"] = "Group"
        msg: Dict[str, Any] = {"message_id": self.next_message, "date": int(time.time()), "chat": chat,
                               "from": {"id": from_id, "is_bot": False, "first_name": first_name}, "text": text}
        if text.startswith("/"):
            msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        return self.inject({"message": msg})

    def callback_update(self, from_id: int, chat_id: int, data: str, message_id: int) -> int:
        return self.inject({"callback_query": {
            "id": uuid.uuid4().hex[:12], "chat_instance": "local", "data": data,
            "from": {"id": from_id, "is_bot": False, "first_name": "Test"},
            "message": {"message_id": message_id, "date": int(time.time()), "chat": {"id": chat_id, "type": "private"},
                        "from": self.BOT, "text": "…"}}})


# --- ntfy (UnifiedPush) ---------------------------------------------------------------------------

class FakeNtfy:
    """A UnifiedPush server like ntfy: a POST to ``/<topic>`` is a ping for the
    app holding that endpoint; records topic, body and the TTL header. Topics
    starting ``gone-`` answer 404 (the app unregistered)."""

    def __init__(self) -> None:
        self.pings: List[Dict[str, Any]] = []
        app = web.Application()
        app.router.add_post("/{topic}", self.publish)
        self.app = app

    async def publish(self, request: web.Request) -> web.Response:
        topic = request.match_info["topic"]
        self.pings.append({"topic": topic, "body": await request.text(), "ttl": request.headers.get("TTL")})
        if topic.startswith("gone-"):
            return web.json_response({"code": 40401, "error": "topic not found"}, status=404)
        return web.json_response({"id": uuid.uuid4().hex[:12], "topic": topic, "event": "message"})


# --- Control API ---------------------------------------------------------------------------------

class Stubs:
    def __init__(self) -> None:
        self.ha = {"a": _ha_a(), "b": _ha_b()}
        self.waha = FakeWAHA("W", WAHA_KEY, {
            "default": {"id": ADMIN_ACCOUNT, "lid": "111000111@lid", "pushName": "Robin"},
            "maya": {"id": MEMBER_ACCOUNT, "lid": "222000222@lid", "pushName": "Maya"},
            # Stands in for a WAHA a member runs that reports the admin's account (one connection per account).
            "maya-lies": {"id": ADMIN_ACCOUNT, "lid": "111000111@lid", "pushName": "Robin"},
        }, ("972500000011@c.us", "Noa Example"), "Noa chat")
        self.waha.root = "http://stubs:3000"
        self.waha.url = self.waha.root + "/api/"
        self.frigate = FakeFrigate()
        self.model = FakeModel()
        self.telegram = FakeTelegram()
        self.ntfy = FakeNtfy()

    def control_app(self) -> web.Application:
        app = web.Application()
        r = app.router
        r.add_get("/health", lambda req: web.json_response({"ok": True}))
        r.add_post("/reset", self.reset)
        r.add_get("/ha/{which}/log", self.ha_log)
        r.add_get("/waha/log", self.waha_log)
        r.add_get("/frigate/log", self.frigate_log)
        r.add_post("/llm/script", self.llm_script)
        r.add_get("/llm/requests", self.llm_requests)
        r.add_post("/llm/transcript", self.llm_transcript)
        r.add_post("/telegram/message", self.tg_message)
        r.add_post("/telegram/callback", self.tg_callback)
        r.add_get("/telegram/log", self.tg_log)
        r.add_get("/ntfy/log", self.ntfy_log)
        return app

    async def reset(self, request: web.Request) -> web.Response:
        """Forget the records (not the state: entities keep their values)."""
        for ha in self.ha.values():
            ha.requests.clear()
            ha.service_calls.clear()
        self.waha.requests.clear()
        self.waha.sent.clear()
        self.frigate.requests.clear()
        self.model.requests.clear()
        self.model.script.clear()
        self.telegram.sent.clear()
        self.ntfy.pings.clear()
        return web.json_response({"ok": True})

    async def ntfy_log(self, request: web.Request) -> web.Response:
        return web.json_response({"pings": self.ntfy.pings})

    def _ha(self, request: web.Request) -> FakeHA:
        which = request.match_info["which"]
        if which not in self.ha:
            raise web.HTTPNotFound()
        return self.ha[which]

    async def ha_log(self, request: web.Request) -> web.Response:
        ha = self._ha(request)
        return web.json_response({
            "requests": [{"path": p, "token_ok": t == ha.token} for p, t in ha.requests],
            "service_calls": [{"service": s, "data": d} for s, d in ha.service_calls],
            "states": {eid: s["state"] for eid, s in ha.states.items()}})

    async def waha_log(self, request: web.Request) -> web.Response:
        return web.json_response({"requests": [{"method": m, "path": p, "key_ok": k == WAHA_KEY, "session": s}
                                               for m, p, k, s in self.waha.requests], "sent": self.waha.sent})

    async def frigate_log(self, request: web.Request) -> web.Response:
        return web.json_response({"requests": self.frigate.requests})

    async def llm_script(self, request: web.Request) -> web.Response:
        body = await request.json()
        entries = body if isinstance(body, list) else [body]
        self.model.script.extend(entries)
        return web.json_response({"queued": len(self.model.script)})

    async def llm_requests(self, request: web.Request) -> web.Response:
        match = request.query.get("match", "")
        rows = [r for r in self.model.requests if r["kind"] == "chat" and match in r["conversation"]]
        return web.json_response({"requests": rows, "transcriptions": sum(
            1 for r in self.model.requests if r["kind"] == "transcription"), "pending_script": self.model.script})

    async def llm_transcript(self, request: web.Request) -> web.Response:
        self.model.transcript = (await request.json())["text"]
        return web.json_response({"ok": True})

    async def tg_message(self, request: web.Request) -> web.Response:
        b = await request.json()
        uid = self.telegram.message_update(int(b["from_id"]), int(b.get("chat_id") or b["from_id"]), b["text"])
        return web.json_response({"update_id": uid})

    async def tg_callback(self, request: web.Request) -> web.Response:
        b = await request.json()
        uid = self.telegram.callback_update(int(b["from_id"]), int(b.get("chat_id") or b["from_id"]), b["data"],
                                            int(b.get("message_id") or 1))
        return web.json_response({"update_id": uid})

    async def tg_log(self, request: web.Request) -> web.Response:
        return web.json_response({"sent": self.telegram.sent, "polls": self.telegram.polls,
                                  "pending_updates": len(self.telegram.updates)})


async def main() -> None:
    stubs = Stubs()
    sites = [
        (stubs.ha["a"].server.app, 8123), (stubs.ha["b"].server.app, 8124), (stubs.waha.server.app, 3000),
        (stubs.frigate.app, 5000), (stubs.model.app, 9100), (stubs.telegram.app, 8081),
        (stubs.ntfy.app, 8095), (stubs.control_app(), 9000),
    ]
    for app, port in sites:
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("stubs listening: HA A 8123, HA B 8124, WAHA 3000, Frigate 5000, model 9100, Telegram 8081, ntfy 8095, control 9000")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
