"""Tests for inbound WhatsApp (WAHA) voice-message handling.

Standalone script (matches the repo's test convention):

    python test_waha_voice.py

Covers the filters (1:1 only, incoming only, voice notes only), dedup of WAHA
webhook redeliveries, and the download -> transcribe -> notify pipeline with all
network calls mocked. The events run for the first admin's whatsapp connection
(imported from the env values below), as integrations/whatsapp/events.py
does for a routed event (routing itself: test_whatsapp_users.py).
"""

import asyncio
import os
import sys

# core.paths reads DATABASE_PATH at import time -> isolate before any import.
import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("waha_test")
os.environ["HOMEASSISTANT_HOST"] = "https://ha.test/"
os.environ["HOMEASSISTANT_TOKEN"] = "test-token"
os.environ["WAHA_URL"] = "https://waha.test/api/"
os.environ["WAHA_API_KEY"] = "test-waha-key"
os.environ["WAHA_SESSION"] = "default"
os.environ["PHONE_NOTIFY_SERVICE"] = "mobile_app_test"
os.environ["WHATSAPP_VOICE_TELEGRAM_CHAT_ID"] = "555"
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake credentials

from unittest.mock import AsyncMock, patch
from collections import OrderedDict
from types import SimpleNamespace

from core import db, notify, users
from integrations import bootstrap as integrations_bootstrap
from integrations.framework import Connection, store
from integrations.whatsapp import config as wa_config, events as wa

db.migrate()
users.bootstrap()
integrations_bootstrap.bootstrap()
USER = users.first_admin()
CONN = store.require(USER, "whatsapp")


def make_payload(**overrides):
    payload = {
        "id": "false_137181406429317@lid_3A487EF20774EC9DEBEF",
        "timestamp": 1792271419,
        "from": "137181406429317@lid",
        "fromMe": False,
        "body": None,
        "hasMedia": True,
        "media": {"url": "https://waha.test/api/files/voice.ogg", "mimetype": "audio/ogg; codecs=opus", "filename": None},
        "_data": {
            "key": {"remoteJid": "137181406429317@lid", "fromMe": False},
            "pushName": "יפה גנם",
            "message": {"audioMessage": {"mimetype": "audio/ogg; codecs=opus", "ptt": True, "seconds": 31}},
        },
    }
    payload.update(overrides)
    return payload


def make_event(payload=None, event="message"):
    return {"event": event, "session": "default", "engine": "NOWEB", "payload": payload or make_payload()}


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


class _FakeClient:
    """Minimal stand-in for httpx.AsyncClient used as an async context manager."""

    def __init__(self, response):
        self._response = response
        self.get_calls = []
        self.post_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self._response

    async def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self._response


def _patch_pipeline(transcript="שלום מה קורה"):
    """Patch the network-dependent steps of process_event."""
    return (
        patch.object(wa, "download_media_bytes", new=AsyncMock(return_value=b"fake-audio")),
        patch.object(wa.llm, "transcribe", new=AsyncMock(return_value=transcript)),
        patch.object(wa, "get_chat_id_to_name_map", new=AsyncMock(return_value={})),
        patch.object(wa, "_notify_phone", new=AsyncMock(return_value=True)),
    )


class _FakeBot:
    """Stands in for core.telegram_bot.TelegramBot in the WAHA pipeline tests."""

    def __init__(self, result=True, error=None):
        self.notify_whatsapp_voice = AsyncMock(return_value=result, side_effect=error)


async def test_direct_voice_is_transcribed_and_notified():
    wa._SEEN_MESSAGE_IDS.clear()
    p_dl, p_tr, p_map, p_notify = _patch_pipeline("תזכיר לי לקנות חלב")
    with p_dl, p_tr as tr, p_map, p_notify as notify:
        await wa.process_event(CONN, make_event())
    tr.assert_awaited_once()
    # The model gets a cleaned mimetype (no codecs parameter).
    assert tr.await_args.args[1] == "audio/ogg", tr.await_args
    notify.assert_awaited_once()
    args = notify.await_args.args
    assert args[0] is CONN, args
    assert args[1] == "יפה גנם", args
    assert args[2] == "תזכיר לי לקנות חלב", args
    print("✓ 1:1 voice note -> transcribed + notified")


async def test_transcript_also_sent_to_telegram():
    wa._SEEN_MESSAGE_IDS.clear()
    bot = _FakeBot()
    p_dl, p_tr, p_map, p_notify = _patch_pipeline("תזכיר לי לקנות חלב")
    notify.register_telegram(bot)
    try:
        with p_dl, p_tr, p_map, p_notify as phone:
            await wa.process_event(CONN, make_event())
    finally:
        notify.register_telegram(None)
    bot.notify_whatsapp_voice.assert_awaited_once()
    kwargs = bot.notify_whatsapp_voice.await_args.kwargs
    assert kwargs["sender"] == "יפה גנם", kwargs
    assert kwargs["transcript"] == "תזכיר לי לקנות חלב", kwargs
    assert kwargs["message_id"] == make_payload()["id"], kwargs
    # For the connection's user, to the chat their whatsapp setting names.
    assert kwargs["user"].id == USER.id and kwargs["chat_id"] == 555, kwargs
    # The phone notification is still sent alongside the Telegram one.
    phone.assert_awaited_once()
    print("✓ transcript also pushed to Telegram")


async def test_telegram_failure_does_not_break_pipeline():
    wa._SEEN_MESSAGE_IDS.clear()
    bot = _FakeBot(error=RuntimeError("telegram down"))
    p_dl, p_tr, p_map, p_notify = _patch_pipeline()
    notify.register_telegram(bot)
    try:
        with p_dl, p_tr as tr, p_map, p_notify as phone:
            await wa.process_event(CONN, make_event())  # must never raise
    finally:
        notify.register_telegram(None)
    tr.assert_awaited_once()
    phone.assert_awaited_once()
    print("✓ Telegram failure doesn't break the WhatsApp pipeline")


async def test_group_voice_is_ignored():
    wa._SEEN_MESSAGE_IDS.clear()
    p_dl, p_tr, p_map, p_notify = _patch_pipeline()
    payload = make_payload()
    payload["from"] = "1234-5678@g.us"
    payload["id"] = "false_1234-5678@g.us_GROUPID"
    with p_dl, p_tr as tr, p_map, p_notify as notify:
        await wa.process_event(CONN, make_event(payload))
    tr.assert_not_awaited()
    notify.assert_not_awaited()
    print("✓ group voice note ignored")


async def test_outgoing_message_is_ignored():
    wa._SEEN_MESSAGE_IDS.clear()
    p_dl, p_tr, p_map, p_notify = _patch_pipeline()
    with p_dl, p_tr as tr, p_map, p_notify as notify:
        await wa.process_event(CONN, make_event(make_payload(fromMe=True)))
    tr.assert_not_awaited()
    notify.assert_not_awaited()
    print("✓ outgoing message ignored")


async def test_non_voice_media_is_ignored():
    wa._SEEN_MESSAGE_IDS.clear()
    p_dl, p_tr, p_map, p_notify = _patch_pipeline()
    payload = make_payload(media={"url": "https://waha.test/api/files/pic.jpg", "mimetype": "image/jpeg"})
    payload["_data"]["message"] = {"imageMessage": {}}
    with p_dl, p_tr as tr, p_map, p_notify as notify:
        await wa.process_event(CONN, make_event(payload))
    tr.assert_not_awaited()
    notify.assert_not_awaited()
    print("✓ non-voice media ignored")


async def test_audio_file_without_ptt_is_ignored():
    wa._SEEN_MESSAGE_IDS.clear()
    p_dl, p_tr, p_map, p_notify = _patch_pipeline()
    payload = make_payload()
    payload["_data"]["message"]["audioMessage"]["ptt"] = False
    with p_dl, p_tr as tr, p_map, p_notify as notify:
        await wa.process_event(CONN, make_event(payload))
    tr.assert_not_awaited()
    notify.assert_not_awaited()
    print("✓ forwarded audio file (ptt=false) ignored")


async def test_missing_ptt_flag_is_permissive():
    wa._SEEN_MESSAGE_IDS.clear()
    p_dl, p_tr, p_map, p_notify = _patch_pipeline()
    payload = make_payload()
    payload["_data"]["message"]["audioMessage"].pop("ptt")
    with p_dl, p_tr as tr, p_map, p_notify as notify:
        await wa.process_event(CONN, make_event(payload))
    tr.assert_awaited_once()
    notify.assert_awaited_once()
    print("✓ missing ptt flag treated as voice note")


async def test_duplicate_redelivery_is_deduped():
    wa._SEEN_MESSAGE_IDS.clear()
    p_dl, p_tr, p_map, p_notify = _patch_pipeline()
    with p_dl, p_tr as tr, p_map, p_notify as notify:
        await wa.process_event(CONN, make_event())
        await wa.process_event(CONN, make_event())
    tr.assert_awaited_once()
    notify.assert_awaited_once()
    print("✓ duplicate webhook redelivery deduped")


async def test_non_message_event_is_ignored():
    wa._SEEN_MESSAGE_IDS.clear()
    p_dl, p_tr, p_map, p_notify = _patch_pipeline()
    with p_dl, p_tr as tr, p_map, p_notify as notify:
        await wa.process_event(CONN, make_event(event="session.status"))
    tr.assert_not_awaited()
    notify.assert_not_awaited()
    print("✓ non-message event ignored")


async def test_media_url_refetched_when_absent():
    wa._SEEN_MESSAGE_IDS.clear()
    payload = make_payload(media={"url": None, "mimetype": "audio/ogg; codecs=opus", "filename": None})
    client = _FakeClient(_FakeResponse(json_data={"media": {"url": "https://waha.test/api/files/refetched.ogg"}}))
    p_dl, p_tr, p_map, p_notify = _patch_pipeline()
    with patch.object(wa.httpx, "AsyncClient", return_value=client), \
         patch.object(wa, "download_media_bytes", new=AsyncMock(return_value=b"audio")), \
         p_tr as tr, p_map, p_notify as notify:
        await wa.process_event(CONN, make_event(payload))
    # Refetch hit /chats/all/messages/<rawId> with downloadMedia=true.
    assert client.get_calls, "expected a refetch GET for missing media URL"
    url, kwargs = client.get_calls[0]
    assert url.endswith("/default/chats/all/messages/3A487EF20774EC9DEBEF"), url
    assert kwargs["params"] == {"downloadMedia": "true"}, kwargs
    tr.assert_awaited_once()
    notify.assert_awaited_once()
    print("✓ missing media.url refetched by message id")


async def test_notify_pushes_to_phone():
    push = AsyncMock(return_value=True)
    with patch.object(wa.phone_push, "notify", new=push):
        ok = await wa._notify_phone(CONN, "דנה", "שלום עולם", "false_1@lid_ABC")
    assert ok is True
    push.assert_awaited_once()
    user, title, text = push.await_args.args
    assert user is CONN.user, push.await_args
    assert "דנה" in title and text == "שלום עולם", push.await_args
    assert push.await_args.kwargs["tag"] == "wa_voice_false_1@lid_ABC", push.await_args
    print("✓ transcript pushed to the phone app")


async def _make_telegram_bot(**attrs):
    """Bare TelegramBot (skips __init__/network) with just what these tests need."""
    from core.telegram_bot import TelegramBot

    bot = TelegramBot.__new__(TelegramBot)
    bot.bot = AsyncMock()
    bot.wa_voice_transcripts = OrderedDict()
    for key, value in attrs.items():
        setattr(bot, key, value)
    return bot


async def test_telegram_notify_builds_summarize_button():
    bot = await _make_telegram_bot()
    ok = await bot.notify_whatsapp_voice(
        user=USER, chat_id=424242, sender="דנה <x>", transcript="שלום, תזכורת לקנות חלב", message_id="false_1@lid_ABC"
    )
    assert ok is True
    kwargs = bot.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 424242, kwargs
    assert kwargs["parse_mode"] == "HTML", kwargs
    # Sender/transcript are escaped for HTML parse mode.
    assert "דנה &lt;x&gt;" in kwargs["text"], kwargs["text"]
    assert "תזכורת לקנות חלב" in kwargs["text"], kwargs["text"]

    data = kwargs["reply_markup"].inline_keyboard[0][0].callback_data
    assert data.startswith("wa_sum:"), data
    assert len(data.encode()) <= 64, data
    key = data.split(":", 1)[1]
    assert bot.wa_voice_transcripts[key]["transcript"] == "שלום, תזכורת לקנות חלב"
    print("✓ Telegram message carries the transcript + Summarize button")


async def test_telegram_chat_id_comes_from_the_setting():
    """One explicit source: the user's whatsapp setting voice_telegram_chat_id. No guessing, no fallbacks."""
    def chat(value):
        return wa_config.voice_chat_id(Connection(user=USER, kind="whatsapp",
                                                  settings={"voice_telegram_chat_id": value}))

    assert wa_config.voice_chat_id(CONN) == 555  # imported from WHATSAPP_VOICE_TELEGRAM_CHAT_ID
    assert chat(496321884) == 496321884
    assert chat("  -1001234567890  ") == -1001234567890
    assert chat("") is None
    assert chat(None) is None
    assert chat("not-a-number") is None
    print("✓ voice_telegram_chat_id parsed strictly (valid / missing / malformed)")


async def test_telegram_notify_skipped_without_target_chat():
    bot = await _make_telegram_bot()
    ok = await bot.notify_whatsapp_voice(user=USER, chat_id=None, sender="דנה", transcript="היי", message_id="m1")
    assert ok is False
    bot.bot.send_message.assert_not_awaited()
    print("✓ no Telegram send when voice_telegram_chat_id is unset")


async def test_summarize_callback_edits_message_with_summary():
    import core.telegram_bot as tb

    bot = await _make_telegram_bot()
    await bot.notify_whatsapp_voice(user=USER, chat_id=555, sender="דנה", transcript="תזכורת לקנות חלב", message_id="m2")
    key = next(iter(bot.wa_voice_transcripts))

    callback = SimpleNamespace(
        data=f"wa_sum:{key}",
        answer=AsyncMock(),
        message=SimpleNamespace(chat=SimpleNamespace(id=555), edit_text=AsyncMock()),
    )
    with patch.object(tb, "summarize_text", new=AsyncMock(return_value="לקנות חלב")):
        await bot.handle_whatsapp_summary_callback(callback, USER)

    callback.answer.assert_awaited_once()
    call = callback.message.edit_text.await_args
    text = call.args[0] if call.args else call.kwargs["text"]
    assert "תזכורת לקנות חלב" in text, text
    assert "✨ Summary" in text, text
    assert "לקנות חלב" in text, text
    # Button is gone after a successful summary.
    assert "reply_markup" not in call.kwargs
    print("✓ Summarize button appends the AI summary and drops itself")


async def test_summarize_callback_reports_expired_transcript():
    bot = await _make_telegram_bot()
    callback = SimpleNamespace(
        data="wa_sum:deadbeef1234",
        answer=AsyncMock(),
        message=SimpleNamespace(chat=SimpleNamespace(id=555), edit_text=AsyncMock()),
    )
    await bot.handle_whatsapp_summary_callback(callback, USER)
    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs.get("show_alert") is True
    callback.message.edit_text.assert_not_awaited()
    print("✓ expired transcript reports gracefully")


async def test_telegram_omits_summarize_button_for_silent_note():
    bot = await _make_telegram_bot()
    await bot.notify_whatsapp_voice(user=USER, chat_id=555, sender="דנה", transcript="", message_id="silent")
    kwargs = bot.bot.send_message.await_args.kwargs
    assert kwargs["reply_markup"] is None, kwargs
    assert "(no speech detected)" in kwargs["text"], kwargs["text"]
    print("✓ silent voice note gets no Summarize button")


async def test_wa_voice_cache_is_bounded():
    import core.telegram_bot as tb

    bot = await _make_telegram_bot()
    for i in range(tb._WA_VOICE_CACHE_MAX + 7):
        await bot.notify_whatsapp_voice(user=USER, chat_id=555, sender="דנה", transcript=f"msg {i}", message_id=f"m{i}")
    assert len(bot.wa_voice_transcripts) == tb._WA_VOICE_CACHE_MAX
    print("✓ transcript cache is bounded")


async def test_long_transcript_is_truncated_for_telegram():
    import core.telegram_bot as tb

    bot = await _make_telegram_bot()
    await bot.notify_whatsapp_voice(user=USER, chat_id=555, sender="דנה", transcript="א" * 5000, message_id="long")
    text = bot.bot.send_message.await_args.kwargs["text"]
    assert len(text) < 4096, len(text)
    assert "truncated" in text, text
    print("✓ over-long transcript truncated below Telegram's limit")


async def test_direct_chat_detection():
    assert wa._is_direct_chat("972500000000@c.us")
    assert wa._is_direct_chat("137181406429317@lid")
    assert wa._is_direct_chat("972500000000@s.whatsapp.net")
    assert not wa._is_direct_chat("1234-5678@g.us")
    assert not wa._is_direct_chat("status@broadcast")
    assert not wa._is_direct_chat("")
    print("✓ 1:1 chat detection (c.us / lid / s.whatsapp.net)")


async def main():
    tests = [
        test_direct_voice_is_transcribed_and_notified,
        test_transcript_also_sent_to_telegram,
        test_telegram_failure_does_not_break_pipeline,
        test_group_voice_is_ignored,
        test_outgoing_message_is_ignored,
        test_non_voice_media_is_ignored,
        test_audio_file_without_ptt_is_ignored,
        test_missing_ptt_flag_is_permissive,
        test_duplicate_redelivery_is_deduped,
        test_non_message_event_is_ignored,
        test_media_url_refetched_when_absent,
        test_notify_pushes_to_phone,
        test_telegram_notify_builds_summarize_button,
        test_telegram_chat_id_comes_from_the_setting,
        test_telegram_notify_skipped_without_target_chat,
        test_summarize_callback_edits_message_with_summary,
        test_summarize_callback_reports_expired_transcript,
        test_telegram_omits_summarize_button_for_silent_note,
        test_wa_voice_cache_is_bounded,
        test_long_transcript_is_truncated_for_telegram,
        test_direct_chat_detection,
    ]
    for test in tests:
        await test()
    print(f"\nAll {len(tests)} WAHA voice-message tests passed")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
