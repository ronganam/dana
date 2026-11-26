"""WhatsApp events for the catalog (addons/whatsapp/event_types.py): whatsapp.message.received once per new message WAHA delivers, for the user whose connection owns the session — 1:1, group, own (from_me), media, redeliveries deduped, two users isolated, no history.

Two users with WhatsApp (Ann on fake WAHA A, Bob on fake WAHA B: tests/testfakes.py)
and one without (Carl). Webhook bodies go through the real routing
(``events.handle_webhook``); a tap collects what is emitted. Every name,
number and message is made up.

Run: bin/test test_whatsapp_events
"""

import asyncio
import os
import sys
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("whatsapp_events_test")
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake credentials

from core import db, events, llm, users  # noqa: E402
from core.addons import registry, store  # noqa: E402
from core.users import User  # noqa: E402
from addons.whatsapp import client as wa_client, event_types, events as wa_events  # noqa: E402
from testfakes import FakeWAHA  # noqa: E402

db.migrate()
users.bootstrap()

results: List[bool] = []


def check(name: str, cond: Any, detail: Any = "") -> None:
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


A_ACCOUNT, B_ACCOUNT = "972500000101@c.us", "972500000102@c.us"
NOA, DINA = "972500000111@c.us", "972500000122@c.us"
FAMILY = "120363000000000001@g.us"
WA_A = FakeWAHA("A", "key-a", {"default": {"id": A_ACCOUNT, "pushName": "Ann"}}, (NOA, "Noa Example"),
                "Noa chat", groups={FAMILY: "Family Example"})
WA_B = FakeWAHA("B", "key-b", {"bob": {"id": B_ACCOUNT, "pushName": "Bob"}}, (DINA, "Dina Example"), "Dina chat")

ANN = users.create("Ann")
BOB = users.create("Bob")
CARL = users.create("Carl")

SEEN: List[Tuple[str, str, str, Dict[str, Any]]] = []  # (event, user id, acting user id, data)


async def collect(name: str, user: User, data: Dict[str, Any]) -> None:
    if name.startswith("whatsapp."):
        SEEN.append((name, user.id, users.current().id, data))


def body(me: Optional[str], session: str, **payload: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"event": "message", "session": session, "engine": "NOWEB",
                           "payload": {"timestamp": 1792271419, "fromMe": False, "hasMedia": False, **payload}}
    if me:
        out["me"] = {"id": me, "pushName": "x"}
    return out


async def deliver(data: Dict[str, Any]) -> List[Tuple[str, str, str, Dict[str, Any]]]:
    SEEN.clear()
    await wa_events.handle_webhook(data)
    await events.drain()
    return list(SEEN)


async def test_messages() -> None:
    got = await deliver(body(A_ACCOUNT, "default", id=f"false_{NOA}_T1", **{"from": NOA}, body="Can we meet Sunday at 10?",
                             _data={"pushName": "Noa Example"}))
    check("1:1 text: one event for Ann, as Ann, with the contract's data",
          got == [("whatsapp.message.received", ANN.id, ANN.id,
                   {"message_id": f"false_{NOA}_T1", "chat": "Noa Example", "sender": "Noa Example",
                    "is_group": False, "from_me": False, "text": "Can we meet Sunday at 10?", "has_media": False,
                    "media_type": ""})], got)

    again = await deliver(body(A_ACCOUNT, "default", id=f"false_{NOA}_T1", **{"from": NOA}, body="Can we meet Sunday at 10?",
                               _data={"pushName": "Noa Example"}))
    check("redelivery of the same message id: nothing emitted", again == [], again)

    got = await deliver(body(A_ACCOUNT, "default", id=f"false_{FAMILY}_G1_{NOA}", **{"from": FAMILY},
                             participant=NOA, body="Who is bringing the cake to the wedding?",
                             _data={"pushName": "Noa Example"}))
    data = got[0][3] if got else {}
    check("group text: the group's name as chat, the writer as sender, is_group",
          len(got) == 1 and (data["chat"], data["sender"], data["is_group"], data["from_me"])
          == ("Family Example", "Noa Example", True, False), got)

    got = await deliver(body(A_ACCOUNT, "default", id=f"true_{NOA}_M1", **{"from": A_ACCOUNT, "to": NOA},
                             fromMe=True, body="On my way"))
    data = got[0][3] if got else {}
    check("own message (from another device): emitted with from_me, sender Me, the recipient's chat",
          len(got) == 1 and (data["from_me"], data["sender"], data["chat"], data["text"])
          == (True, "Me", "Noa chat", "On my way"), got)

    got = await deliver(body(A_ACCOUNT, "default", id=f"false_{NOA}_P1", **{"from": NOA}, body="The invitation",
                             hasMedia=True, media={"url": None, "mimetype": "image/jpeg"},
                             _data={"pushName": "Noa Example", "message": {"imageMessage": {}}}))
    data = got[0][3] if got else {}
    check("photo with a caption: has_media, media_type image, the caption as text",
          (data.get("has_media"), data.get("media_type"), data.get("text")) == (True, "image", "The invitation"), got)

    got = await deliver(body(A_ACCOUNT, "default", id=f"false_{NOA}_D1", **{"from": NOA},
                             hasMedia=True, media={"url": None, "mimetype": "application/pdf", "filename": "quote.pdf"},
                             _data={"pushName": "Noa Example"}))
    check("a PDF: media_type document, empty text", got and (got[0][3]["media_type"], got[0][3]["text"])
          == ("document", ""), got)

    transcribe = AsyncMock(side_effect=llm.TranscriptionUnavailable("no transcription model in this test"))
    with patch.object(wa_events.llm, "transcribe", transcribe):
        got = await deliver(body(A_ACCOUNT, "default", id=f"false_{NOA}_V1", **{"from": NOA}, hasMedia=True,
                                 media={"url": f"{WA_A.root}/api/files/voice.ogg", "mimetype": "audio/ogg; codecs=opus"},
                                 _data={"pushName": "Noa Example", "message": {"audioMessage": {"ptt": True}}}))
    check("voice note: announced (media_type voice) and still handed to the voice pipeline",
          got and got[0][3]["media_type"] == "voice" and transcribe.await_count == 1, (got, transcribe.await_count))

    got = await deliver(body(B_ACCOUNT, "bob", id=f"false_{NOA}_T1", **{"from": DINA}, body="Dinner at 8",
                             _data={"pushName": "Dina Example"}))
    check("Bob's account: his own event only, as Bob (the same id as Ann's earlier message is not a duplicate)",
          [(u, acting, d["chat"], d["text"]) for _, u, acting, d in got] == [(BOB.id, BOB.id, "Dina Example",
                                                                               "Dinner at 8")], got)
    b_keys = {c[2] for c in WA_B.calls()}
    check("Bob's names come from his WAHA with his key only", b_keys == {"key-b"}
          and not any(c[2] == "key-b" for c in WA_A.calls()), b_keys)

    got = await deliver(body("972599999999@c.us", "default", id=f"false_{NOA}_X1", **{"from": NOA}, body="hi"))
    check("an account nobody has: nothing emitted", got == [], got)

    got = await deliver({"event": "session.status", "session": "default", "me": {"id": A_ACCOUNT},
                         "payload": {"status": "WORKING"}})
    check("a session.status event: nothing emitted", got == [], got)


def test_catalog() -> None:
    ann = {e.name: e for e in registry.event_types(ANN)}
    carl = {e.name for e in registry.event_types(CARL)}
    et = ann.get("whatsapp.message.received")
    check("catalog: whatsapp.message.received for a user with WhatsApp, no history (Dana keeps no messages)",
          et is not None and et.history is None and set(et.fields) == {
              "message_id", "chat", "sender", "is_group", "from_me", "text", "has_media", "media_type"}, ann.keys())
    check("catalog: the from_me field says watches usually want from_me eq false",
          et is not None and "from_me eq false" in et.fields["from_me"])
    check("catalog: none for a user without WhatsApp", "whatsapp.message.received" not in carl, carl)


def test_media_type() -> None:
    cases = [({}, ""), ({"hasMedia": True, "media": {"mimetype": "video/mp4"}}, "video"),
             ({"hasMedia": True, "media": {"mimetype": "audio/mpeg"},
               "_data": {"message": {"audioMessage": {"ptt": False}}}}, "audio"),
             ({"hasMedia": True, "media": {"mimetype": "image/webp"},
               "_data": {"message": {"stickerMessage": {}}}}, "sticker"),
             ({"hasMedia": True, "media": {}}, "document")]
    got = [event_types.media_type(p) for p, _ in cases]
    check("media_type: video, forwarded audio file, sticker, unknown file", got == [w for _, w in cases], got)


async def main() -> None:
    await WA_A.start()
    await WA_B.start()
    store.connect(ANN, "whatsapp", credentials={"url": WA_A.url, "api_key": "key-a", "session": "default"},
                  account=A_ACCOUNT)
    store.connect(BOB, "whatsapp", credentials={"url": WA_B.url, "api_key": "key-b", "session": "bob"},
                  account=B_ACCOUNT)
    if store.is_connected(CARL, "whatsapp"):
        store.disconnect(CARL, "whatsapp")
    await asyncio.sleep(0.1)
    wa_client._name_maps.clear()
    WA_A.calls(), WA_B.calls()
    events.tap(collect)
    try:
        await test_messages()
        test_catalog()
        test_media_type()
    finally:
        events.untap(collect)
        await WA_A.server.close()
        await WA_B.server.close()


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
if not all(results):
    sys.exit(1)
