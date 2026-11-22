"""Transcription with one LLM key: the order that picks the transcription model (MODEL_<PURPOSE>,
TRANSCRIBE_MODEL, the chosen provider's audio model when its key is set, OPENAI_API_KEY, WHISPER_URL),
a local Whisper server (an OpenAI-compatible fake: the model downloaded once on a 404, then the
transcript), and the unavailable path: a typed error before anything is sent, the admins'
`transcription` self-check, and each caller saying it needs a transcription model (the web voice
upload's 503, the voice stream's error frame, the Telegram reply, a call recording marked failed,
a WhatsApp voice note skipped or read as not transcribed).

Run: bin/test test_transcription
"""
import asyncio
import io
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("transcription_test")
from cryptography.fernet import Fernet
os.environ["API_TOKEN"] = "transcription-test-env-token"  # a test value: the first admin's token
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake credentials
os.environ["ANTHROPIC_API_KEY"] = "test-key"  # a test value: only Claude, which hears no audio

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import db, server_settings, users
from addons import bootstrap as addons_bootstrap

db.migrate()
users.bootstrap()
addons_bootstrap.bootstrap()
from core import assistant_api, checks, llm, llm_provider, paths
from core.auth import setup_auth
from core.addons import store
from core.telegram_bot import TelegramBot
from addons.phone.calls import processing as calls_processing, store as calls_store, upload as calls_upload
from addons.whatsapp import events as wa_events, messages as wa_messages

ROBIN = users.first_admin()
GAL = users.create("Gal")
ROBIN_TOKEN, _ = users.issue_token(ROBIN.id, "test")
testenv.connect_admin("phone")
testenv.connect_admin("whatsapp", credentials={"url": "https://waha.test/api/", "api_key": "test-waha-key",
                                               "session": "default"}, settings={"voice_telegram_chat_id": 555})

results = []
ENV = ("TRANSCRIBE_MODEL", "MODEL_VOICE_TRANSCRIPT", "MODEL_CALL_TRANSCRIPT", "OPENAI_API_KEY", "GEMINI_API_KEY",
       "WHISPER_URL", "WHISPER_MODEL", "MODEL")


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


def env(**values):
    """Exactly these transcription settings in env (the rest removed), no provider chosen."""
    for name in ENV:
        os.environ.pop(name, None)
    os.environ.update({k: v for k, v in values.items()})
    server_settings.delete(llm_provider.SETTING)
    llm_provider.forget_cache()


def choose(provider):
    server_settings.put(llm_provider.SETTING, {"provider": provider})
    llm_provider.forget_cache()


def test_order():
    env()
    check("order: Claude alone transcribes with nothing", llm.transcriber() is None)
    env(TRANSCRIBE_MODEL="deepgram/nova-3", OPENAI_API_KEY="test-key")
    check("order: TRANSCRIBE_MODEL first", llm.transcriber() == llm.Transcriber("deepgram/nova-3", "TRANSCRIBE_MODEL"))
    env(TRANSCRIBE_MODEL="deepgram/nova-3", MODEL_CALL_TRANSCRIPT="openai/whisper-1")
    check("order: MODEL_<PURPOSE> above it, for that purpose alone",
          llm.transcriber("call_transcript").model == "openai/whisper-1"
          and llm.transcriber("voice_transcript").model == "deepgram/nova-3")
    try:
        llm.transcriber("no_such_purpose")
        check("order: an unknown purpose is refused", False)
    except Exception as e:
        check("order: an unknown purpose is refused", type(e).__name__ == "UnknownPurpose", repr(e))

    env(GEMINI_API_KEY="test-key", OPENAI_API_KEY="test-key", WHISPER_URL="http://whisper.test:8000")
    choose("gemini")
    check("order: the chosen provider's own audio model next (Gemini, its key set)",
          llm.transcriber() == llm.Transcriber("gemini/gemini-3.1-flash-lite", "provider"))
    env(OPENAI_API_KEY="test-key")
    choose("gemini")
    check("order: the chosen provider without its key is passed over (→ OPENAI_API_KEY)",
          llm.transcriber() == llm.Transcriber("openai/gpt-4o-transcribe", "OPENAI_API_KEY"))
    env(WHISPER_URL="http://whisper.test:8000")
    choose("claude")
    check("order: Claude chosen hears no audio (→ WHISPER_URL)", llm.transcriber().why == "WHISPER_URL")
    env(MODEL="gemini/gemini-3.5-flash", GEMINI_API_KEY="test-key")
    check("order: no choice: the chat model's provider when it hears audio",
          llm.transcriber() == llm.Transcriber("gemini/gemini-3.1-flash-lite", "provider"))
    env(OPENAI_API_KEY="test-key", WHISPER_URL="http://whisper.test:8000")
    check("order: OPENAI_API_KEY before a Whisper server", llm.transcriber().model == "openai/gpt-4o-transcribe")
    env(WHISPER_URL="http://whisper.test:8000/")
    check("order: WHISPER_URL → the default Whisper model on its /v1", llm.transcriber() == llm.Transcriber(
        "openai/Systran/faster-whisper-small", "WHISPER_URL", "http://whisper.test:8000/v1"))
    env(WHISPER_URL="http://whisper.test:8000/v1", WHISPER_MODEL="Systran/faster-whisper-large-v3")
    t = llm.transcriber()
    check("order: WHISPER_MODEL, and a /v1 address kept as it is",
          t.model == "openai/Systran/faster-whisper-large-v3" and t.api_base == "http://whisper.test:8000/v1", t)
    env()


async def test_whisper():
    """A fake speaches: 404 until the model is downloaded, then a transcript."""
    seen = {"installs": [], "transcriptions": 0, "model": None}

    async def install(request):
        seen["installs"].append(request.match_info["model"])
        return web.json_response({})

    async def transcribe(request):
        form = await request.post()
        seen["model"] = form.get("model")
        if not seen["installs"]:
            return web.json_response({"detail": "Model is not installed locally"}, status=404)
        seen["transcriptions"] += 1
        return web.json_response({"text": " hello from whisper "})

    app = web.Application()
    app.router.add_post("/v1/models/{model:.*}", install)
    app.router.add_post("/v1/audio/transcriptions", transcribe)
    server = TestServer(app)
    await server.start_server()
    try:
        env(WHISPER_URL=str(server.make_url("/")))
        with users.acting_as(ROBIN):
            text = await llm.transcribe(b"\x00" * 200, "audio/ogg", purpose="voice_transcript")
        check("whisper: the transcript comes back from the Whisper server", text == "hello from whisper", text)
        check("whisper: the model it lacked was downloaded once, then used",
              seen["installs"] == ["Systran/faster-whisper-small"] and seen["transcriptions"] == 1
              and seen["model"] == "Systran/faster-whisper-small", seen)
        await llm.transcribe(b"\x00" * 200, "audio/ogg")
        check("whisper: the next call needs no download", len(seen["installs"]) == 1 and seen["transcriptions"] == 2, seen)
    finally:
        await server.close()
        env()


async def test_unavailable():
    env()
    c = {x["key"]: x for x in checks.for_user(ROBIN)}
    check("unavailable: before anything asked, the check stays quiet", c["transcription"]["ok"], c.get("transcription"))
    with patch.object(llm.litellm, "acompletion", new=AsyncMock()) as sent, \
            patch.object(llm.litellm, "atranscription", new=AsyncMock()) as sent_stt:
        try:
            await llm.transcribe(b"\x00" * 100, "audio/ogg", purpose="voice_transcript")
            check("unavailable: transcribe raises TranscriptionUnavailable", False)
        except llm.TranscriptionUnavailable as e:
            check("unavailable: a typed error with its code, nothing sent",
                  e.code == "no_transcription_model" and "transcription model" in str(e)
                  and not sent.await_count and not sent_stt.await_count, repr(e))
            check("unavailable: not worth a retry", not llm.retryable(e))
    c = {x["key"]: x for x in checks.for_user(ROBIN)}
    check("unavailable: the admin's transcription check turns red",
          not c["transcription"]["ok"] and c["transcription"]["detail"] == "needs a transcription model"
          and "WHISPER_URL" in c["transcription"]["why"], c.get("transcription"))
    check("unavailable: a member has no transcription check", "transcription" not in {x["key"] for x in checks.for_user(GAL)})
    os.environ["OPENAI_API_KEY"] = "test-key"
    c = {x["key"]: x for x in checks.for_user(ROBIN)}
    check("unavailable: with a model set, the check is ok and names it",
          c["transcription"]["ok"] and c["transcription"]["detail"] == "openai/gpt-4o-transcribe", c.get("transcription"))
    env()


async def test_voice_endpoints():
    env()
    app = web.Application()
    setup_auth(app)
    assistant_api.setup(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        headers = {"Authorization": f"Bearer {ROBIN_TOKEN}"}
        fd = aiohttp.FormData()
        fd.add_field("audio", b"\x00\x01" * 800, filename="a.webm", content_type="audio/webm")
        r = await client.post("/api/assistant/voice?session_id=s_voice", data=fd, headers=headers)
        body = await r.json()
        check("voice upload: 503 with code no_transcription_model",
              r.status == 503 and body.get("code") == "no_transcription_model" and "transcription model" in body["error"], body)
        ws = await client.ws_connect("/api/assistant/voice_stream?session_id=s_voice_ws", headers=headers)
        await ws.send_bytes(b"\x00\x01" * 800)
        await ws.send_str('{"event": "stop", "sample_rate": 16000}')
        frame = await asyncio.wait_for(ws.receive_json(), 10)
        check("voice stream: an error frame with code no_transcription_model",
              frame.get("type") == "error" and frame.get("code") == "no_transcription_model", frame)
        state = await asyncio.wait_for(ws.receive_json(), 10)
        check("voice stream: then the state, the socket stays open", state.get("type") == "state" and not ws.closed, state)
        await ws.close()
    finally:
        await client.close()


async def test_telegram():
    env()
    bot = TelegramBot.__new__(TelegramBot)

    async def download(file, destination):
        destination.write(b"OggS-not-really")

    bot.bot = SimpleNamespace(get_file=AsyncMock(return_value=SimpleNamespace(file_path="voice.ogg")),
                              download=AsyncMock(side_effect=download), send_chat_action=AsyncMock())
    status = SimpleNamespace(edit_text=AsyncMock())
    message = SimpleNamespace(from_user=SimpleNamespace(id=7), text=None, caption=None, photo=None,
                              voice=SimpleNamespace(file_id="voice-1"), chat=SimpleNamespace(id=77),
                              reply=AsyncMock(return_value=status))
    with patch.object(TelegramBot, "_send_debug_file", new=AsyncMock()) as debug:
        await bot.handle_message(message, ROBIN)
    said = status.edit_text.await_args.args[0] if status.edit_text.await_args else ""
    check("telegram: the voice note's reply says it needs a transcription model (no debug file)",
          "transcription model" in said and not debug.await_count, said)


async def test_calls():
    env()
    paths.ensure_data_dirs()
    call_id, _dup = await calls_upload.receive(ROBIN, b"\x00" * 4000, "Call with Robin.m4a", "audio/mp4",
                                               {"caller": "Robin", "receiver": "Me", "duration_seconds": "40"})
    for _ in range(100):
        row = calls_store.get_row(ROBIN, call_id)
        if row and row["status"] != "processing":
            break
        await asyncio.sleep(0.05)
    await asyncio.gather(*list(calls_processing._tasks), return_exceptions=True)
    row = calls_store.get_row(ROBIN, call_id)
    check("calls: a recording without a transcription model is failed with the reason",
          row["status"] == "failed" and row["error"] == calls_processing.NO_TRANSCRIPTION_MODEL
          and not row["transcript"], dict(row))


async def test_whatsapp():
    env()
    conn = store.require(ROBIN, "whatsapp")
    payload = {"id": "false_100200300@lid_ABC", "timestamp": 1792271419, "from": "100200300@lid", "fromMe": False,
               "hasMedia": True, "media": {"url": "https://waha.test/api/files/voice.ogg",
                                           "mimetype": "audio/ogg; codecs=opus"},
               "_data": {"key": {"remoteJid": "100200300@lid", "fromMe": False}, "pushName": "Maya",
                         "message": {"audioMessage": {"mimetype": "audio/ogg; codecs=opus", "ptt": True}}}}
    with patch.object(wa_events, "download_media_bytes", new=AsyncMock(return_value=b"fake-audio")), \
            patch.object(wa_events, "_notify_phone", new=AsyncMock()) as phone, \
            patch.object(wa_events.notify, "whatsapp_voice", new=AsyncMock()) as telegram:
        await wa_events.process_event(conn, {"event": "message", "session": "default", "payload": payload})
    check("whatsapp: a voice note without a transcription model is skipped (nothing sent)",
          not phone.await_count and not telegram.await_count)
    with patch.object(wa_messages.waha, "download_media_bytes", new=AsyncMock(return_value=b"fake-audio")):
        text = await wa_messages._media(conn, {"media": {"mimetype": "audio/ogg", "url": "https://waha.test/f.ogg"}})
    check("whatsapp: a voice note read through the tools says why it has no text",
          "not transcribed" in text and "transcription model" in text, text)


async def main():
    test_order()
    await test_whisper()
    await test_unavailable()
    await test_voice_endpoints()
    await test_telegram()
    await test_calls()
    await test_whatsapp()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
    sys.exit(1 if failed else 0)


asyncio.run(main())
