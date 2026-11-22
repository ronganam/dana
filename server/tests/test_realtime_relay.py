"""Offline Verification Test Suite for OpenAI Realtime API Relay.

Tests bidirectional WebSocket streaming, session configuration,
flat tool schema generation, audio PCM16 relay, transcript deltas,
VAD interruption signaling, and tool execution roundtrip using an
embedded mock OpenAI Realtime server.
"""

import asyncio
import base64
import json
import logging
import os
import sys
from typing import Any, Dict


import aiohttp
from aiohttp import web
from cryptography.fernet import Fernet

# A throwaway database: the relay stores the call's transcript in the caller's session.
import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("realtime_relay_test")
os.environ["DANA_INSECURE_DEV"] = "1"  # no API_TOKEN: local development, a request without credentials is the first admin
# A throwaway key: the startup import stores any addon credentials it finds in env encrypted.
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test_realtime_relay")

# Import system modules
from core.realtime import assistant_realtime_ws_api
from core import db, users
from core.auth import setup_auth

from addons.tasks.groups import ensure_default_groups_for_everyone
from addons import bootstrap as addons_bootstrap
from core.addons import registry

db.migrate()
users.bootstrap()
addons_bootstrap.bootstrap()  # the first admin's official addons (tasks: add_task)
ensure_default_groups_for_everyone()


class MockOpenAIServer:
    """Embedded mock of the OpenAI Realtime WebSocket API (wss://api.openai.com/v1/realtime)."""

    def __init__(self):
        self.app = web.Application()
        self.app.router.add_get("/v1/realtime", self.handle_realtime_ws)
        self.runner = None
        self.site = None
        self.port = None

        # Verification event flags & captures
        self.handshake_headers = {}
        self.received_model = None
        self.received_session_update = None
        self.received_audio_appends = []
        self.received_tool_output = None
        self.received_response_create = False

        self.server_ready = asyncio.Event()
        self.tool_roundtrip_event = asyncio.Event()

    async def start(self) -> int:
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        # Retrieve allocated dynamic port
        self.port = self.site._server.sockets[0].getsockname()[1]
        logger.info(f"Mock OpenAI Realtime server listening on 127.0.0.1:{self.port}")
        return self.port

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()

    async def handle_realtime_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        # 1. Verify handshake headers & query params
        self.handshake_headers = {k.lower(): v for k, v in request.headers.items()}
        self.received_model = request.query.get("model")

        # 2. Send initial session.created event
        await ws.send_json({
            "type": "session.created",
            "session": {
                "id": "mock_sess_xyz789",
                "model": self.received_model
            }
        })

        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                event = json.loads(msg.data)
                event_type = event.get("type")

                if event_type == "session.update":
                    self.received_session_update = event
                    await ws.send_json({"type": "session.updated", "session": event.get("session")})

                elif event_type == "input_audio_buffer.append":
                    b64_audio = event.get("audio", "")
                    raw_bytes = base64.b64decode(b64_audio)
                    self.received_audio_appends.append(raw_bytes)

                    # When audio chunk received, trigger test downstream responses:
                    # a. Audio delta (PCM bytes) — GA event name
                    test_pcm = b"\x01\x02\x03\x04\x05\x06\x07\x08"
                    await ws.send_json({
                        "type": "response.output_audio.delta",
                        "delta": base64.b64encode(test_pcm).decode("ascii")
                    })

                    # b. Assistant transcript deltas & done — GA event names
                    await ws.send_json({
                        "type": "response.output_audio_transcript.delta",
                        "delta": "Hello from "
                    })
                    await ws.send_json({
                        "type": "response.output_audio_transcript.delta",
                        "delta": "Levi Realtime!"
                    })
                    await ws.send_json({
                        "type": "response.output_audio_transcript.done",
                        "transcript": "Hello from Levi Realtime!"
                    })

                    # c. User transcription completed
                    await ws.send_json({
                        "type": "conversation.item.input_audio_transcription.completed",
                        "transcript": "Add a task to buy groceries"
                    })

                    # d. Speech started (interruption)
                    await ws.send_json({
                        "type": "input_audio_buffer.speech_started"
                    })

                    # e. Tool call: add_task
                    await ws.send_json({
                        "type": "response.output_item.added",
                        "item": {
                            "id": "item_fn_1",
                            "type": "function_call",
                            "name": "add_task",
                            "call_id": "call_mock_task_001"
                        }
                    })
                    await ws.send_json({
                        "type": "response.function_call_arguments.done",
                        "call_id": "call_mock_task_001",
                        "arguments": json.dumps({
                            "title": "Realtime Groceries Task",
                            "priority": "high"
                        })
                    })

                elif event_type == "conversation.item.create":
                    item = event.get("item", {})
                    if item.get("type") == "function_call_output":
                        self.received_tool_output = item

                elif event_type == "response.create":
                    self.received_response_create = True
                    self.tool_roundtrip_event.set()

            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                break

        return ws


async def run_verification():
    print("\n=======================================================")
    print("   OpenAI Realtime API Addon Offline Verification")
    print("=======================================================\n")

    # 1. Verify Tool Schemas Export
    print("[1/5] Verifying tool schema flattening...")
    flat_schemas = registry.realtime_tool_schemas(users.first_admin())
    assert len(flat_schemas) > 0, "No tools discovered in registry!"
    print(f"  -> Discovered {len(flat_schemas)} registered tools.")

    for s in flat_schemas:
        assert s.get("type") == "function", f"Tool {s} missing type: function"
        assert "name" in s, f"Tool {s} missing name"
        assert "description" in s, f"Tool {s} missing description"
        assert "parameters" in s, f"Tool {s} missing parameters"
        assert "function" not in s, f"Tool {s} has nested function key (unflattened)!"
    print("  -> All tool schemas correctly flattened for Realtime API.")

    # 2. Start Embedded Mock OpenAI Server
    print("\n[2/5] Starting embedded Mock OpenAI Realtime server...")
    mock_server = MockOpenAIServer()
    mock_port = await mock_server.start()

    # 3. Start Backend Relay Server
    print("\n[3/5] Starting Levi Assistant Realtime Relay server...")
    os.environ["OPENAI_REALTIME_URL"] = f"http://127.0.0.1:{mock_port}/v1/realtime"
    os.environ["OPENAI_API_KEY"] = "mock-sk-test-token"

    relay_app = web.Application()
    setup_auth(relay_app)  # no API_TOKEN: a request without credentials is the first admin
    relay_app.router.add_get("/api/assistant/realtime", assistant_realtime_ws_api)
    relay_runner = web.AppRunner(relay_app)
    await relay_runner.setup()
    relay_site = web.TCPSite(relay_runner, "127.0.0.1", 0)
    await relay_site.start()
    relay_port = relay_site._server.sockets[0].getsockname()[1]
    print(f"  -> Relay server running on 127.0.0.1:{relay_port}")

    # 4. Connect Mock Client & Run Test Suite
    print("\n[4/5] Testing Client <-> Relay <-> OpenAI roundtrip...")
    client_url = f"http://127.0.0.1:{relay_port}/api/assistant/realtime?model=gpt-realtime&session_id=test_sess_001"

    received_binary_audio = []
    received_events = []

    async with aiohttp.ClientSession() as client_session:
        async with client_session.ws_connect(client_url) as client_ws:
            print("  -> Client WebSocket connected.")

            # Send binary PCM16 audio frame (simulating Android mic recording)
            pcm_test_payload = b"\xAA\xBB\xCC\xDD" * 200
            await client_ws.send_bytes(pcm_test_payload)
            print(f"  -> Sent {len(pcm_test_payload)} bytes binary PCM16 audio.")

            # Receive downstream frames until tool roundtrip and hangup
            while True:
                msg = await asyncio.wait_for(client_ws.receive(), timeout=10.0)
                if msg.type == aiohttp.WSMsgType.BINARY:
                    received_binary_audio.append(msg.data)
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    evt = json.loads(msg.data)
                    received_events.append(evt)
                    evt_type = evt.get("type")
                    if evt_type == "tool_done":
                        # Tool execution completed, end session gracefully
                        await client_ws.send_json({"type": "hangup"})
                    elif evt_type == "call_ended":
                        break
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                    break

    # Wait for mock server to finish receiving tool output
    await asyncio.wait_for(mock_server.tool_roundtrip_event.wait(), timeout=5.0)

    # 5. Assertions and Verifications
    print("\n[5/5] Verifying test assertions...")

    # A. Handshake headers
    auth_header = mock_server.handshake_headers.get("authorization", "")
    assert auth_header == "Bearer mock-sk-test-token", f"Invalid auth header: {auth_header}"
    assert "openai-beta" not in mock_server.handshake_headers, "Must NOT send OpenAI-Beta header"
    assert mock_server.received_model == "gpt-realtime", f"Invalid model: {mock_server.received_model}"
    print("  [PASS] Upstream authentication headers & model query param verified.")

    # B. session.update payload
    assert mock_server.received_session_update is not None, "Upstream never received session.update!"
    sess = mock_server.received_session_update["session"]
    assert sess.get("type") == "realtime", "session.type must be 'realtime'"
    assert sess["output_modalities"] == ["audio"], "Output modality must be audio-only"
    audio_cfg = sess.get("audio", {})
    assert audio_cfg["input"]["format"] == {"type": "audio/pcm", "rate": 24000}, "Input format mismatch"
    assert audio_cfg["output"]["format"] == {"type": "audio/pcm", "rate": 24000}, "Output format mismatch"
    assert audio_cfg["output"].get("voice"), "Output voice missing"
    turn = audio_cfg["input"].get("turn_detection", {})
    assert turn.get("type") == "server_vad", "Turn detection must be server_vad"
    assert len(sess["tools"]) > 0, "session.update tools list is empty"
    print("  [PASS] Upstream session.update configuration verified.")

    # C. Audio streaming
    assert len(mock_server.received_audio_appends) > 0, "Upstream never received input_audio_buffer.append"
    assert mock_server.received_audio_appends[0] == pcm_test_payload, "Audio payload corrupted in relay"
    print(f"  [PASS] Upstream audio ingestion verified ({len(pcm_test_payload)} bytes).")

    assert len(received_binary_audio) > 0, "Client never received downstream binary PCM16 audio"
    assert received_binary_audio[0] == b"\x01\x02\x03\x04\x05\x06\x07\x08", "Downstream binary audio mismatch"
    print("  [PASS] Downstream binary PCM16 audio streaming verified.")

    # D. Transcripts and interruption
    event_types = [e.get("type") for e in received_events]
    assert "session_ready" in event_types, "Client missing session_ready event"
    assert "transcript_delta" in event_types, "Client missing transcript_delta"
    assert "transcript_done" in event_types, "Client missing transcript_done"
    assert "user_transcript_done" in event_types, "Client missing user_transcript_done"
    assert "interrupted" in event_types, "Client missing interrupted signal"
    print(f"  [PASS] Downstream events verified: {set(event_types)}")

    # E. Tool execution roundtrip
    assert "tool_start" in event_types, "Client missing tool_start"
    assert "tool_done" in event_types, "Client missing tool_done"
    tool_done_evt = next(e for e in received_events if e.get("type") == "tool_done")
    assert tool_done_evt.get("name") == "add_task", f"Wrong tool executed: {tool_done_evt}"
    assert tool_done_evt.get("status") == "completed", f"Tool failed: {tool_done_evt}"

    assert mock_server.received_tool_output is not None, "Upstream never received conversation.item.create"
    assert mock_server.received_tool_output.get("call_id") == "call_mock_task_001", "Tool call_id mismatch"
    output_content = mock_server.received_tool_output.get("output", "")
    assert "Successfully added task" in output_content or "Realtime Groceries Task" in output_content, (
        f"Unexpected tool execution output: {output_content}"
    )
    assert mock_server.received_response_create is True, "Upstream never received response.create"
    print("  [PASS] Tool execution roundtrip verified (add_task invoked & output relayed).")

    # 6. Model selection query param verification (mini model)
    print("\n[6/6] Verifying model selection query parameter...")
    mini_client_url = f"http://127.0.0.1:{relay_port}/api/assistant/realtime?model=gpt-realtime-mini&session_id=mini_test"
    async with aiohttp.ClientSession() as mini_client_session:
        async with mini_client_session.ws_connect(mini_client_url) as mini_ws:
            ready_msg = await asyncio.wait_for(mini_ws.receive_json(), timeout=5.0)
            assert ready_msg.get("model") == "gpt-realtime-mini", f"Model not reflected: {ready_msg}"
            await mini_ws.send_json({"type": "hangup"})
    print("  [PASS] Model selection (gpt-realtime-mini) verified.")

    # Cleanup
    await relay_runner.cleanup()
    await mock_server.stop()

    print("\n=======================================================")
    print("  ALL REALTIME RELAY VERIFICATION TESTS PASSED (100%)")
    print("=======================================================\n")


if __name__ == "__main__":
    asyncio.run(run_verification())
