"""WhatsApp per user (docs/access.md, "Addons", "Inbound"; docs/whatsapp.md).
Run: bin/test test_whatsapp_users

Two in-process fake WAHA instances (aiohttp: sessions with their ``me``,
contacts, chats, messages, files, send / forward / edit / delete), each with its own API
key, session and WhatsApp account. The first admin's addon is connected by
the operator and is then pointed at instance A; Bob has instance B; Carl has
no WhatsApp. Checked: nothing reads the old env names; inbound events only
when WAHA signed them; every tool hits only the user's own WAHA with their own key and
session; name maps / "me" caches / dedupe are per connection; Carl has no
tools; saved contacts and the call pipeline's known people per user; a saved
image is served only to its user; the account is learned at connect,
by the startup loop (WAHA down → retried) and lazily; inbound events routed by
``me.id`` to the right user (transcript to that user's Telegram chat only,
usage row theirs, phone push addressed to the routed user), an unknown
account dropped, no ``me`` with one / several candidate connections; reading
shows each message's id and files by name (only voice notes downloaded),
read_whatsapp_file reads a file by id (untrusted; a made-up id refused), the
read tools are untrusted, forwarding by id from the user's own session (a
made-up or unknown id refused) and its approval card; ✨
Summarize only for the transcript's user. LiteLLM runs offline
(``mock_response``); Telegram and phone push are mocked; no network beyond
localhost.
"""

import asyncio
import json
import logging
import os
import pathlib
import re
import sys
from collections import OrderedDict
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("whatsapp_users_test")
os.environ["API_TOKEN"] = "wa-users-test-env-token"  # a test value: the first admin's token (a headless install)
os.environ["WAHA_WEBHOOK_SECRET"] = "wa-users-test-waha-secret"  # a test value: WAHA's HMAC key
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake credentials
# The first admin's env values, as production has them (fake).
os.environ["WAHA_URL"] = "http://env-waha.invalid/api/"  # the old env import's names: read by nothing now
os.environ["WHATSAPP_VOICE_TELEGRAM_CHAT_ID"] = "1001"
os.environ["TRANSCRIBE_MODEL"] = "gemini/gemini-3.1-flash-lite"  # a model that hears audio (LiteLLM is mocked)

import litellm
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import db, users

db.migrate()
users.bootstrap()
from addons import bootstrap as addons_bootstrap  # noqa: E402
addons_bootstrap.bootstrap()
testenv.connect_admin("whatsapp", credentials={"url": "http://admin-waha.invalid/api/", "api_key": "admin-key",
                                               "session": "default"},
                      settings={"voice_telegram_chat_id": 1001})

from core import assistant_api  # noqa: E402
from addons.whatsapp import api as whatsapp_api  # noqa: E402
import core.telegram_bot as tb  # noqa: E402
from core import images, notify, usage  # noqa: E402
from addons.phone.calls import context as call_context  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from core.agent import for_model  # noqa: E402
from core.text import carries_untrusted  # noqa: E402
from core.addons import registry, scheduler, store  # noqa: E402
from addons.whatsapp import accounts, client as wa_client, events, messages as messages_fmt  # noqa: E402
from testfakes import FakeWAHA  # noqa: E402

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


async def until(cond, timeout: float = 5.0) -> bool:
    for _ in range(int(timeout / 0.02)):
        if cond():
            return True
        await asyncio.sleep(0.02)
    return bool(cond())


# --- LiteLLM offline: transcripts recorded by the real usage callback ---------------------------

_real_acompletion = litellm.acompletion
MODEL_USERS: List[str] = []


async def fake_acompletion(**kwargs):
    current = users.current()
    MODEL_USERS.append(current.id if current else "")
    kwargs["mock_response"] = f"transcript for {current.name if current else 'nobody'}"
    return await _real_acompletion(**kwargs)


litellm.acompletion = fake_acompletion
litellm.suppress_debug_info = True


# --- A fake WAHA ---------------------------------------------------------------------------------

A_ACCOUNT, B_ACCOUNT = "972500000001@c.us", "972500000002@c.us"
WA_A = FakeWAHA("A", "key-a", {"default": {"id": A_ACCOUNT, "lid": "111@lid", "pushName": "Robin"}},
                ("972500000011@c.us", "Noa A"), "Noa chat A")
WA_B = FakeWAHA("B", "key-b", {"bob": {"id": B_ACCOUNT, "lid": "222@lid", "pushName": "Bob"}},
                ("972500000022@c.us", "Dina B"), "Dina chat B")

ADMIN = users.first_admin()
assert ADMIN is not None
BOB = users.create("Bob Levi", "member")
CARL = users.create("Carl Cohen", "member")
BOB_TOKEN, _ = users.issue_token(BOB.id, "test")
CARL_TOKEN, _ = users.issue_token(CARL.id, "test")
ADMIN_AUTH = {"Authorization": f"Bearer {os.environ['API_TOKEN']}"}
BOB_AUTH = {"Authorization": f"Bearer {BOB_TOKEN}"}
CARL_AUTH = {"Authorization": f"Bearer {CARL_TOKEN}"}
SECRET = os.environ["WAHA_WEBHOOK_SECRET"]


def signed(body: bytes) -> Dict[str, str]:
    """WAHA's headers for ``body`` signed with the shared secret."""
    return {"X-Webhook-Hmac": whatsapp_api.signature(SECRET, body), "X-Webhook-Hmac-Algorithm": "sha512",
            "Content-Type": "application/json"}


# --- 1. No env import: the old env names --------------------------------------------------------

def test_import() -> None:
    wa = store.require(ADMIN, "whatsapp")
    # the address is stored normalised (no trailing slash)
    check("connection: url / key / session as connected, the voice chat setting",
          dict(wa.credentials) == {"url": "http://admin-waha.invalid/api", "api_key": "admin-key", "session": "default"}
          and wa.settings["voice_telegram_chat_id"] == 1001, (dict(wa.credentials), dict(wa.settings)))
    old_names = ("WAHA_URL", "WAHA_API_KEY", "WAHA_TOKEN", "WAHA_SESSION", "WHATSAPP_VOICE_TELEGRAM_CHAT_ID")
    root = testenv.SERVER
    readers = []
    for path in list((root / "core").rglob("*.py")) + [p for p in (root / "addons").rglob("*.py") if "tests" not in p.parts] + [root / "main.py"]:
        text = path.read_text(encoding="utf-8")
        readers += [f"{path.relative_to(root)}: {n}" for n in old_names if re.search(rf'["\']{n}["\']', text)]
    check("old env names: read by nothing", not readers, readers)
    check("no owner-specific defaults in the addon (no default address, no voice chat)",
          all(f.default is None for f in store.get_addon("whatsapp").credentials + store.get_addon("whatsapp").settings)
          and not re.search(r"https?://waha\.", "".join(p.read_text(encoding="utf-8")
                                               for p in (root / "addons" / "whatsapp").glob("*.py"))))
    source = (root / "addons" / "whatsapp" / "api.py").read_text(encoding="utf-8")
    handler = source[source.index("async def handle_waha_webhook"):source.index("def setup(")]
    check("webhook handler: no interim owner, routed by the package",
          "owner_of_unscoped_data" not in handler and "whatsapp_events.handle_webhook(data)" in handler)


# --- 2. Connections and the account -------------------------------------------------------------

async def test_connect() -> None:
    store.update(ADMIN, "whatsapp", account=A_ACCOUNT)
    store.update(ADMIN, "whatsapp", credentials={"url": WA_A.url, "api_key": "key-a"})
    check("credentials change: the stored account is forgotten",
          store.require(ADMIN, "whatsapp").account in (None, ""))
    store.connect(BOB, "whatsapp", credentials={"url": WA_B.url, "api_key": "key-b", "session": "bob"},
                  settings={"voice_telegram_chat_id": 2002})
    for addon in ("tasks", "people"):
        store.connect(BOB, addon)
    learned = await until(lambda: store.require(ADMIN, "whatsapp").account == A_ACCOUNT
                          and store.require(BOB, "whatsapp").account == B_ACCOUNT)
    check("account learned from WAHA at connect / credentials change (each from their own session)", learned,
          (store.require(ADMIN, "whatsapp").account, store.require(BOB, "whatsapp").account))
    check("account: asked with each one's own key", {c[2] for c in WA_A.calls()} == {"key-a"}
          and {c[2] for c in WA_B.calls()} == {"key-b"})

    # The job: WAHA down only delays it; learned on a later pass.
    store.update(BOB, "whatsapp", account="")
    WA_B.down = True
    job = registry.job("whatsapp.learn_account")
    with patch.object(accounts, "RETRY_SECONDS", 0.0):
        await scheduler.run_once(job)
        check("account job: WAHA down → no account yet", store.require(BOB, "whatsapp").account == "" and WA_B.requests)
        WA_B.down = False
        out = await scheduler.run_once(job)
        check("account job: learned once WAHA is back", store.require(BOB, "whatsapp").account == B_ACCOUNT, out)
    check("account job: a connection that has its account is not asked again",
          not [c for c in WA_A.calls() if c[1] == "/api/sessions"])
    WA_B.calls()


# --- 3. Tools ----------------------------------------------------------------------------------

TOOL_ARGS = {
    "find_last_chats": {},
    "search_whatsapp_contact_or_chat": {"query": "chat"},
    "read_chat_messages": {"chat_id": "CONTACT"},
    "send_whatsapp_message": {"chat_id": "CONTACT", "text": "hi"},
    "check_whatsapp_number": {"phone": "0501234567"},
    "send_whatsapp_file": {"chat_id": "CONTACT", "file_url": "https://example.invalid/f.pdf",
                           "mimetype": "application/pdf"},
    "edit_whatsapp_message": {"chat_id": "CONTACT", "message_id": "true_x@c.us_1", "new_text": "hey"},
    "delete_whatsapp_message": {"chat_id": "CONTACT", "message_id": "true_x@c.us_1"},
    "forward_whatsapp_message": {"chat_id": "CONTACT", "message_id": "false_CONTACT_m3"},
    "get_latest_messages": {},
}


async def test_tools() -> None:
    wa_names = set(TOOL_ARGS)
    check("tools: Carl (no WhatsApp) is offered none and has none",
          not wa_names & {t["function"]["name"] for t in registry.tool_schemas(CARL)}
          and not any(registry.has_tool(CARL, n) for n in wa_names))
    check("tools: both users have all of them", all(registry.has_tool(u, n) for u in (ADMIN, BOB) for n in wa_names))
    WA_A.calls(), WA_B.calls()
    outputs: Dict[str, Dict[str, str]] = {}
    for user, mine, other, key, sess in ((ADMIN, WA_A, WA_B, "key-a", "default"), (BOB, WA_B, WA_A, "key-b", "bob")):
        for name, args in TOOL_ARGS.items():
            args = {k: v.replace("CONTACT", mine.contact_id) for k, v in args.items()}
            out = await registry.execute(user, name, args)
            outputs.setdefault(user.id, {})[name] = str(out)
            calls = mine.calls()
            leaked = other.calls()
            sessions = {c[3] for c in calls if c[3]}
            check(f"tools: {user.name.split()[0]}'s {name} hits only their WAHA with their key and session",
                  calls and not leaked and {c[2] for c in calls} == {key} and sessions <= {sess},
                  (calls, leaked))
    a, b = outputs[ADMIN.id], outputs[BOB.id]
    check("tools: each sees their own chats and names",
          "Noa chat A" in a["find_last_chats"] and "Dina" not in a["find_last_chats"]
          and "Dina chat B" in b["find_last_chats"] and "Noa" not in b["find_last_chats"], (a["find_last_chats"],))
    check("tools: messages sent from each one's own session",
          [m["session"] for m in WA_A.sent] == ["default", "default"] and [m["session"] for m in WA_B.sent] == ["bob", "bob"],
          (WA_A.sent, WA_B.sent))
    sigs = {s[0] for s in wa_client._name_maps}
    check("caches: name maps per connection",
          sigs == {ADMIN.id, BOB.id}
          and all(("Dina" not in v) for s, (_, m) in wa_client._name_maps.items() if s[0] == ADMIN.id for v in m.values()))
    check("caches: 'me' per connection", {k[0]: v[0] for k, v in wa_client._me_cache.items()}
          == {ADMIN.id: A_ACCOUNT, BOB.id: B_ACCOUNT})

    # A saved image belongs to its user.
    check("images: a chat's photo is listed by type, not downloaded", "[Image (image/jpeg)]" in a["read_chat_messages"],
          a["read_chat_messages"])
    url = images.save_image(b"bytes of A", "image/jpeg", ADMIN)
    app = web.Application()
    setup_auth(app)
    app.router.add_get("/api/images/{filename}", assistant_api.serve_image_api)
    async with TestClient(TestServer(app)) as http:
        r_admin = await http.get(url, headers=ADMIN_AUTH)
        body = await r_admin.read()
        r_bob = await http.get(url, headers=BOB_AUTH)
        r_carl = await http.get(url, headers=CARL_AUTH)
    check("images: served to the user whose WhatsApp produced it", r_admin.status == 200 and body == b"bytes of A")
    check("images: not reachable by another user (404, as a missing file)", r_bob.status == 404 and r_carl.status == 404)
    check("images: another user's image URL is dropped from a message and never reaches the model",
          assistant_api._decode_data_uri_images([url], BOB) == []
          and assistant_api._decode_data_uri_images([url], ADMIN) == [url]
          and images.image_url_to_data_uri(url, BOB) == "" and images.image_url_to_data_uri(url, ADMIN))


async def test_read_and_forward() -> None:
    from core import approval_text
    WA_A.calls(), WA_B.calls()
    WA_A.message_queries.clear()
    out = str(await registry.execute(ADMIN, "read_chat_messages", {"chat_id": WA_A.contact_id}))
    pdf_id = f"false_{WA_A.contact_id}_m3"
    check("read: every message ends with its id",
          all(f"(id: `false_{WA_A.contact_id}_{m}`)" in out for m in ("m1", "m2", "m3")), out)
    check("read: a file by name and type, with its caption",
          "[File: quote-A.pdf (application/pdf)] quote from A" in out, out)
    check("read: the model reads the messages framed as others' text",
          registry.returns_untrusted("read_chat_messages") and carries_untrusted(for_model("read_chat_messages", out)), out)
    check("read: WAHA downloads only voice notes",
          WA_A.message_queries and WA_A.message_queries[-1].get("downloadMediaMimetypes") == ["audio/"]
          and WA_A.message_queries[-1].get("downloadMedia") == ["true"], WA_A.message_queries)
    latest = str(await registry.execute(BOB, "get_latest_messages", {}))
    check("latest: ids and files too, from Bob's own WAHA",
          f"(id: `false_{WA_B.contact_id}_m3`)" in latest and "[File: quote-B.pdf (application/pdf)]" in latest
          and "quote-A" not in latest, latest)
    check("untrusted: the reading tools mark the turn, the writes do not",
          all(registry.returns_untrusted(n) for n in ("read_chat_messages", "get_latest_messages", "find_last_chats"))
          and not registry.returns_untrusted("forward_whatsapp_message"))
    check("forward: asks first", registry.needs_confirmation("forward_whatsapp_message", {}))
    read = str(await registry.execute(ADMIN, "read_whatsapp_file", {"chat_id": WA_A.contact_id, "message_id": pdf_id}))
    check("read file: the file's text, read by the model, from the admin's own WAHA",
          read.startswith("Noa ") and "application/pdf" in read and "transcript for" in read
          and registry.returns_untrusted("read_whatsapp_file") and not registry.needs_confirmation("read_whatsapp_file", {}),
          read)
    read = str(await registry.execute(ADMIN, "read_whatsapp_file", {"chat_id": WA_A.contact_id, "message_id": "the pdf"}))
    check("read file: a made-up id is refused", read.startswith("Error: message_id"), read)

    WA_A.forwarded.clear(), WA_B.forwarded.clear(), WA_A.calls(), WA_B.calls()
    res = str(await registry.execute(ADMIN, "forward_whatsapp_message",
                                     {"chat_id": "972509999999@c.us", "message_id": f"`{pdf_id}`"}))
    check("forward: the admin's message forwarded from their session, by id",
          WA_A.forwarded == [{"session": "default", "chatId": "972509999999@c.us", "messageId": pdf_id}]
          and not WA_B.forwarded and "Successfully forwarded" in res and not WA_B.calls(), (res, WA_A.forwarded))
    WA_A.forwarded.clear()
    res = str(await registry.execute(ADMIN, "forward_whatsapp_message",
                                     {"chat_id": "972509999999@c.us", "message_id": f"false_{WA_A.contact_id}_nope"}))
    check("forward: an id WAHA does not have is reported, nothing forwarded",
          "no message with id" in res and not WA_A.forwarded, res)
    WA_A.calls()
    res = str(await registry.execute(ADMIN, "forward_whatsapp_message",
                                     {"chat_id": "972509999999@c.us", "message_id": "the pdf"}))
    check("forward: a made-up id is refused without a WAHA request",
          res.startswith("Error: message_id") and not WA_A.calls(), res)
    try:
        await registry.execute(CARL, "forward_whatsapp_message", {"chat_id": "972509999999@c.us", "message_id": pdf_id})
        refused = False
    except registry.ToolNotAvailable:
        refused = True
    check("forward: Carl (no WhatsApp) cannot", refused and not WA_A.forwarded)

    with users.acting_as(ADMIN):
        card = approval_text.describe("forward_whatsapp_message",
                                      {"chat_id": "Gal", "message_id": "false_972501234567@c.us_3EB0", "what": "the quote PDF"})
        lid_card = approval_text.describe("forward_whatsapp_message",
                                          {"chat_id": "Gal", "message_id": "false_123@lid_3EB0"})
    check("forward: the approval card says to whom, what and from where",
          card["title"] == "Forward a WhatsApp message to Gal" and card["body"] == "the quote PDF"
          and card["facts"] == [("From", "+972501234567")], card)
    check("forward: a LID source is not shown as a number", lid_card["facts"] == [], lid_card)


def test_contacts() -> None:
    with get_db_connection() as conn:
        conn.execute("INSERT INTO whatsapp_contacts (user_id, key, value, category, updated_at) VALUES "
                     "(?, 'mom', '972500000077@c.us', 'contact', 'x'), (?, 'gal_contact', '0501112222', 'general', 'x')",
                     (ADMIN.id, ADMIN.id))
        conn.commit()
    check("known people: the admin's contact rows for the call prompt",
          call_context.known_people(ADMIN) == ["mom: 972500000077@c.us", "gal_contact: 0501112222"],
          call_context.known_people(ADMIN))
    check("known people: none for Bob (none saved) or Carl (no WhatsApp / people)",
          call_context.known_people(BOB) == [] and call_context.known_people(CARL) == [])


async def test_saved_contacts_in_tools() -> None:
    WA_A.calls(), WA_B.calls()
    a = await registry.execute(ADMIN, "search_whatsapp_contact_or_chat", {"query": "mom"})
    b = await registry.execute(BOB, "search_whatsapp_contact_or_chat", {"query": "mom"})
    check("saved contacts: the admin's nickname found in their search, not in Bob's",
          "972500000077@c.us" in str(a) and "972500000077@c.us" not in str(b), (a, b))
    await registry.execute(ADMIN, "read_chat_messages", {"chat_id": "mom"})
    await registry.execute(BOB, "read_chat_messages", {"chat_id": "mom"})
    a_paths = [c[1] for c in WA_A.calls()]
    b_paths = [c[1] for c in WA_B.calls()]
    check("saved contacts: 'mom' resolves to the admin's saved chat id only for the admin",
          "/api/default/chats/972500000077@c.us/messages" in a_paths
          and not any("972500000077" in p for p in b_paths), (a_paths, b_paths))


# --- 4. Inbound events ---------------------------------------------------------------------------

def voice_event(me: Optional[str], session: str, mid: str, media_root: str) -> Dict[str, Any]:
    body: Dict[str, Any] = {"event": "message", "session": session, "engine": "NOWEB", "payload": {
        "id": f"false_137181406429317@lid_{mid}", "timestamp": 1792271419, "from": "137181406429317@lid",
        "fromMe": False, "body": None, "hasMedia": True,
        "media": {"url": f"{media_root}/api/files/voice.ogg", "mimetype": "audio/ogg; codecs=opus"},
        "_data": {"pushName": "Yaffa", "message": {"audioMessage": {"ptt": True}}}}}
    if me is not None:
        body["me"] = {"id": me, "pushName": "x"}
    return body


def make_bot() -> tb.TelegramBot:
    bot = tb.TelegramBot.__new__(tb.TelegramBot)
    bot.bot = AsyncMock()
    bot.wa_voice_transcripts = OrderedDict()
    return bot


def sent_chats(bot) -> List[int]:
    return [c.kwargs["chat_id"] for c in bot.bot.send_message.await_args_list]


def usage_rows() -> List[Tuple[str, str, str]]:
    usage.flush_sync()
    with get_db_connection() as conn:
        return [(r["user_id"], r["source"], r["ref"]) for r in conn.execute(
            "SELECT user_id, source, ref FROM usage_events WHERE purpose = 'voice_transcript' ORDER BY id")]


async def test_inbound() -> None:
    bot = make_bot()
    notify.register_telegram(bot)
    phone = AsyncMock(return_value=True)
    app = web.Application()
    setup_auth(app)
    app.router.add_post("/webhook/waha", whatsapp_api.handle_waha_webhook)
    try:
        async with TestClient(TestServer(app)) as http:
            phone_patch = patch.object(events.notify, "push_notify", phone)
            phone_patch.start()
            async def post(body) -> int:
                n = len(bot.bot.send_message.await_args_list)
                raw = json.dumps(body).encode()
                r = await http.post("/webhook/waha", headers=signed(raw), data=raw)
                assert r.status == 200
                await until(lambda: len(bot.bot.send_message.await_args_list) > n, timeout=1.5)
                await asyncio.sleep(0.05)
                return r.status

            WA_A.calls(), WA_B.calls()
            await post(voice_event(A_ACCOUNT, "default", "A1", WA_A.root))
            check("inbound: the admin's account → transcript to the admin's chat only (with ✨ Summarize)",
                  sent_chats(bot) == [1001] and "transcript for" in bot.bot.send_message.await_args.kwargs["text"]
                  and bot.bot.send_message.await_args.kwargs["reply_markup"] is not None, sent_chats(bot))
            check("inbound: the admin's voice note also pushed to the phone (as today)", phone.await_count == 1)
            check("inbound: downloaded from the admin's WAHA with their key only",
                  {c[2] for c in WA_A.calls()} == {"key-a"} and not WA_B.calls())
            check("inbound: usage row carries the admin", usage_rows() == [(ADMIN.id, "whatsapp",
                                                                            "whatsapp:false_137181406429317@lid_A1")],
                  usage_rows())

            bot.bot.send_message.reset_mock()
            await post(voice_event("972500000002:7@s.whatsapp.net", "bob", "B1", WA_B.root))
            check("inbound: Bob's account (device-suffixed id) → his chat only", sent_chats(bot) == [2002],
                  sent_chats(bot))
            check("inbound: each push addressed to its own user's phone (Bob's to Bob, who has no phone addon)",
                  phone.await_count == 2 and phone.await_args_list[0].args[0].id == ADMIN.id
                  and phone.await_args_list[1].args[0].id == BOB.id)
            check("inbound: Bob's from his WAHA only", {c[2] for c in WA_B.calls()} == {"key-b"} and not WA_A.calls())
            check("inbound: usage row carries Bob", usage_rows()[-1][0] == BOB.id, usage_rows())

            bot.bot.send_message.reset_mock()
            await post(voice_event(A_ACCOUNT, "default", "A1", WA_A.root))
            check("inbound: redelivery deduped (per connection)", sent_chats(bot) == [])
            await post(voice_event(B_ACCOUNT, "bob", "A1", WA_B.root))
            check("inbound: the same message id for another connection is not a duplicate", sent_chats(bot) == [2002])

            bot.bot.send_message.reset_mock()
            WA_A.calls(), WA_B.calls()
            n_usage = len(usage_rows())
            await post(voice_event("972599999999@c.us", "default", "U1", WA_A.root))
            check("inbound: an account nobody has → dropped (nothing sent, nothing transcribed, WAHA not asked)",
                  sent_chats(bot) == [] and len(usage_rows()) == n_usage and not WA_A.calls() and not WA_B.calls())

            await post(voice_event(None, "bob", "N1", WA_B.root))
            check("inbound: no me, one connection with that session → that user", sent_chats(bot) == [2002],
                  sent_chats(bot))
            bot.bot.send_message.reset_mock()
            await post(voice_event(None, "nobody", "N2", WA_B.root))
            check("inbound: no me, no connection with that session → dropped", sent_chats(bot) == [])
            store.connect(CARL, "whatsapp", credentials={"url": WA_B.url, "api_key": "key-b", "session": "default"},
                          settings={"voice_telegram_chat_id": 3003})
            await asyncio.sleep(0.1)
            await post(voice_event(None, "default", "N3", WA_A.root))
            check("inbound: no me, several connections with that session → dropped", sent_chats(bot) == [])
            store.disconnect(CARL, "whatsapp")
            WA_A.calls(), WA_B.calls()

            # Lazily: an event for an account no connection has yet.
            store.update(BOB, "whatsapp", account="")
            accounts._last_attempt.clear()
            await post(voice_event(B_ACCOUNT, "bob", "L1", WA_B.root))
            check("inbound: account learned lazily, the event then routed to Bob",
                  sent_chats(bot) == [2002] and store.require(BOB, "whatsapp").account == B_ACCOUNT, sent_chats(bot))

            # ✨ Summarize: only the transcript's user.
            key = next(k for k, e in bot.wa_voice_transcripts.items() if e["user_id"] == ADMIN.id)

            def callback():
                return SimpleNamespace(data=f"wa_sum:{key}", answer=AsyncMock(),
                                       message=SimpleNamespace(chat=SimpleNamespace(id=1001), edit_text=AsyncMock()))
            summarize = AsyncMock(return_value="short")
            with patch.object(tb, "summarize_text", summarize):
                cb = callback()
                await bot.handle_whatsapp_summary_callback(cb, BOB)
                check("summarize: refused for another user (alert, nothing summarized, message untouched)",
                      summarize.await_count == 0 and cb.answer.await_args.kwargs.get("show_alert") is True
                      and not cb.message.edit_text.await_count)
                cb = callback()
                await bot.handle_whatsapp_summary_callback(cb, ADMIN)
                check("summarize: works for the transcript's user", summarize.await_count == 1
                      and "✨ Summary" in cb.message.edit_text.await_args.args[0])

            # A session WhatsApp signed out: Bob's connection needs reconnecting, nobody else's.
            def status_event(status: str) -> Dict[str, Any]:
                return {"event": "session.status", "session": "bob", "engine": "NOWEB",
                        "me": {"id": B_ACCOUNT, "pushName": "x"}, "payload": {"status": status}}
            await post(status_event("SCAN_QR_CODE"))
            marked = await until(lambda: bool(store.reconnects(BOB)))
            check("session.status: signed out → Bob's WhatsApp needs reconnecting (scan the QR code), the admin's not",
                  marked and [r.addon for r in store.reconnects(BOB)] == ["whatsapp"]
                  and "QR code" in store.reconnects(BOB)[0].reason and store.reconnects(ADMIN) == [],
                  store.reconnects(BOB))
            await post(status_event("WORKING"))
            check("session.status: working again clears it", await until(lambda: store.reconnects(BOB) == []),
                  store.reconnects(BOB))
            phone_patch.stop()
    finally:
        notify.register_telegram(None)


def test_startup_report() -> None:
    records: List[logging.LogRecord] = []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(record)

    from addons.whatsapp import hooks as wa_hooks
    handler = Grab()
    level = wa_hooks.logger.level
    wa_hooks.logger.setLevel(logging.INFO)
    wa_hooks.logger.addHandler(handler)
    try:
        # a value stored before chat settings were validated (store.update refuses it now)
        with get_db_connection() as conn:
            conn.execute("UPDATE connections SET settings = json_set(settings, '$.voice_telegram_chat_id', 'nope') "
                         "WHERE user_id = ? AND addon = 'whatsapp'", (BOB.id,))
            conn.commit()
        store.invalidate()
        for u in (ADMIN, BOB):
            wa_hooks.report_voice_chat(u)
    finally:
        wa_hooks.logger.removeHandler(handler)
        wa_hooks.logger.setLevel(level)
        store.update(BOB, "whatsapp", settings={"voice_telegram_chat_id": 2002})
    text = [r.getMessage() for r in records]
    check("startup: where each user's transcripts go is reported per connection",
          any(ADMIN.id in t and "chat 1001" in t for t in text)
          and any(BOB.id in t and "NOT" in t and r.levelno == logging.WARNING for t, r in zip(text, records))
          and not any(CARL.id in t for t in text), text)


async def main() -> None:
    await WA_A.start()
    await WA_B.start()
    test_import()
    await test_connect()
    await test_tools()
    await test_read_and_forward()
    test_contacts()
    await test_saved_contacts_in_tools()
    await test_inbound()
    test_startup_report()
    await WA_A.server.close()
    await WA_B.server.close()


if __name__ == "__main__":
    asyncio.run(main())
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    raise SystemExit(0 if passed == len(results) else 1)
