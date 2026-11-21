"""OpenAI Realtime API WebSocket relay handler.

Bridges continuous bidirectional audio and tool execution between
the client (Android app) and OpenAI's Realtime WebSocket API.

A relay session belongs to the request's user: tools run with that user
current (the auth middleware binds them; the relay's tasks copy the context),
usage is recorded for them, and the transcript is saved to their session.
"""

import asyncio
import base64
import json
import logging
import os
import urllib.parse
from typing import Any, Dict, List

import aiohttp
from aiohttp import web

from core import clock, session_runner
from core.session import append_history, owned_by_someone_else
from core import usage
from core.agent import limit_tool_output
from core.auth import request_user
from core.users import User
from integrations.framework import registry

logger = logging.getLogger(__name__)


# GA defaults per https://developers.openai.com/api/docs/guides/realtime
DEFAULT_REALTIME_MODEL = "gpt-realtime"


def build_realtime_session_instructions(user: User) -> str:
    """The instructions of a realtime voice call for ``user``: the agent's
    instructions for them (core/agent.py), the date and time, voice guidelines."""
    system_instruction = session_runner.agent().instructions_for(user)

    current_time_str = clock.describe_now()

    voice_guidelines = (
        "\n\n--- Realtime Voice Guidelines ---\n"
        "You are in a live, continuous voice call with the user. "
        "Keep responses extremely concise, direct, and natural. "
        "Get straight to the point: when asked to do something, execute it and confirm briefly (e.g. 'Done.', 'Opened.'). "
        "Do NOT use conversational filler, preamble, or unprompted follow-up offers. "
        "Do NOT use markdown syntax (such as asterisks, bullet points, headers, or brackets) "
        "because your response is synthesized directly into speech."
    )

    return (
        f"{system_instruction}\n\n"
        f"Active Context:\n"
        f"- Current Date and Time: {current_time_str}"
        f"{voice_guidelines}"
    )


def build_session_update_payload(user: User, instructions: str, voice: str = "alloy") -> Dict[str, Any]:
    """Construct the session.update payload for the OpenAI Realtime GA API.

    See https://developers.openai.com/api/docs/guides/realtime-conversations
    and https://developers.openai.com/api/docs/guides/realtime-vad

    ``tools``: the realtime tool list of ``user``.
    """
    tools = registry.realtime_tool_schemas(user)

    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "output_modalities": ["audio"],
            "instructions": instructions,
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "transcription": {
                        "model": "whisper-1"
                    },
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 500,
                        "create_response": True,
                        "interrupt_response": True
                    }
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "voice": voice
                }
            },
            "tools": tools,
            "tool_choice": "auto"
        }
    }


async def assistant_realtime_ws_api(request: web.Request) -> web.WebSocketResponse:
    """GET /api/assistant/realtime

    Multiplexed bidirectional WebSocket endpoint connecting client to OpenAI Realtime GA API.
    Query parameters:
      - model: Target GA model ('gpt-realtime' [default] or 'gpt-realtime-mini').
      - session_id: Client session UUID
      - voice: Spoken voice (default 'alloy')
    """
    user = request_user(request)
    model = request.query.get("model", DEFAULT_REALTIME_MODEL) or DEFAULT_REALTIME_MODEL
    session_id = request.query.get("session_id", "default")
    voice = request.query.get("voice", "alloy")
    if owned_by_someone_else(user.id, session_id):
        return web.json_response({"error": "Session not found"}, status=404)

    client_ws = web.WebSocketResponse()
    await client_ws.prepare(request)

    logger.info(f"Realtime WebSocket connected: model={model}, session_id={session_id}")

    # Resolve upstream OpenAI Realtime endpoint and credentials
    openai_url = os.getenv("OPENAI_REALTIME_URL", "wss://api.openai.com/v1/realtime")
    parsed = urllib.parse.urlparse(openai_url)
    query_dict = urllib.parse.parse_qs(parsed.query)
    if "model" not in query_dict:
        sep = "&" if parsed.query else "?"
        upstream_url = f"{openai_url}{sep}model={model}"
    else:
        upstream_url = openai_url

    openai_api_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_api_key and "OPENAI_REALTIME_URL" in os.environ:
        openai_api_key = "mock-key"

    if not openai_api_key:
        logger.error("OPENAI_API_KEY environment variable is not configured.")
        await client_ws.send_json({
            "type": "error",
            "message": "OPENAI_API_KEY is not configured on the server."
        })
        await client_ws.close()
        return client_ws

    headers = {
        "Authorization": f"Bearer {openai_api_key}",
    }

    pending_tool_calls: Dict[str, str] = {}
    user_transcripts: List[str] = []
    assistant_transcripts: List[str] = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(upstream_url, headers=headers) as openai_ws:
                logger.info(f"Connected upstream to OpenAI Realtime API at {upstream_url}")

                # Send GA session.update with nested audio config, VAD, and voice instructions
                instructions = build_realtime_session_instructions(user)
                session_update = build_session_update_payload(user, instructions, voice=voice)
                await openai_ws.send_json(session_update)

                async def client_to_openai():
                    """Relay client audio and control frames to OpenAI."""
                    try:
                        async for msg in client_ws:
                            if msg.type == aiohttp.WSMsgType.BINARY:
                                b64_audio = base64.b64encode(msg.data).decode("ascii")
                                await openai_ws.send_json({
                                    "type": "input_audio_buffer.append",
                                    "audio": b64_audio
                                })
                            elif msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    data = json.loads(msg.data)
                                    event_type = data.get("type")
                                    if event_type == "hangup":
                                        logger.info(f"Client requested hangup for session {session_id}")
                                        break
                                    elif event_type == "text_input":
                                        text = data.get("text", "")
                                        await openai_ws.send_json({
                                            "type": "conversation.item.create",
                                            "item": {
                                                "type": "message",
                                                "role": "user",
                                                "content": [{"type": "input_text", "text": text}]
                                            }
                                        })
                                        await openai_ws.send_json({"type": "response.create"})
                                    else:
                                        await openai_ws.send_str(msg.data)
                                except json.JSONDecodeError:
                                    logger.warning(f"Invalid JSON received from client: {msg.data}")
                            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                                break
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                logger.error(f"Client WebSocket error: {client_ws.exception()}")
                                break
                    except (ConnectionResetError, asyncio.CancelledError):
                        pass
                    except Exception as e:
                        logger.error(f"Error in client_to_openai relay: {e}", exc_info=True)

                async def openai_to_client():
                    """Relay OpenAI audio deltas, transcripts, and handle tool execution."""
                    try:
                        async for msg in openai_ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    event = json.loads(msg.data)
                                    event_type = event.get("type", "")

                                    if event_type == "response.output_audio.delta":
                                        delta_b64 = event.get("delta", "")
                                        if delta_b64:
                                            pcm_bytes = base64.b64decode(delta_b64)
                                            await client_ws.send_bytes(pcm_bytes)

                                    elif event_type in (
                                        "response.output_audio_transcript.delta",
                                        "response.output_text.delta",
                                    ):
                                        delta = event.get("delta", event.get("text", ""))
                                        await client_ws.send_json({
                                            "type": "transcript_delta",
                                            "delta": delta
                                        })

                                    elif event_type in (
                                        "response.output_audio_transcript.done",
                                        "response.output_text.done",
                                    ):
                                        transcript = event.get("transcript", event.get("text", ""))
                                        await client_ws.send_json({
                                            "type": "transcript_done",
                                            "transcript": transcript,
                                            "text": transcript
                                        })
                                        if transcript:
                                            assistant_transcripts.append(transcript)

                                    elif event_type == "response.done":
                                        # Billing: one usage record per model response (core/usage.py).
                                        usage.record_realtime(model, (event.get("response") or {}).get("usage") or {},
                                                              session_id=session_id)

                                    elif event_type == "conversation.item.input_audio_transcription.completed":
                                        usage.record_realtime(model, event.get("usage") or {}, session_id=session_id,
                                                              transcription=True)
                                        transcript = event.get("transcript", "")
                                        await client_ws.send_json({
                                            "type": "user_transcript_done",
                                            "transcript": transcript,
                                            "text": transcript
                                        })
                                        if transcript:
                                            user_transcripts.append(transcript)

                                    elif event_type == "input_audio_buffer.speech_started":
                                        await client_ws.send_json({"type": "interrupted"})

                                    elif event_type in ("response.output_item.added", "conversation.item.created"):
                                        item = event.get("item", {})
                                        if item.get("type") == "function_call":
                                            cid = item.get("call_id")
                                            name = item.get("name")
                                            if cid and name:
                                                pending_tool_calls[cid] = name

                                    elif event_type == "response.function_call_arguments.done":
                                        call_id = event.get("call_id")
                                        args_str = event.get("arguments", "{}")
                                        tool_name = pending_tool_calls.pop(call_id, None) or event.get("name", "")

                                        await client_ws.send_json({
                                            "type": "tool_start",
                                            "name": tool_name,
                                            "call_id": call_id
                                        })

                                        try:
                                            parsed_args = json.loads(args_str) if isinstance(args_str, str) else args_str
                                            if not registry.in_realtime(user, tool_name):
                                                raise registry.ToolNotAvailable(tool_name)  # not offered in a voice call
                                            result = await registry.execute(user, tool_name, parsed_args)
                                            output_str = limit_tool_output(json.dumps(result) if not isinstance(result, str) else result)
                                            status = "completed"
                                        except registry.ToolNotAvailable as e:
                                            # Made up, or of an integration this user does not have: the same answer.
                                            logger.warning(f"Refused realtime tool call '{tool_name}': {e}")
                                            output_str = json.dumps({"error": str(e), "status": "failed"})
                                            status = "failed"
                                        except Exception as e:
                                            logger.error(f"Error executing tool '{tool_name}': {e}", exc_info=True)
                                            output_str = json.dumps({"error": str(e), "status": "failed"})
                                            status = "failed"

                                        # Submit function call result back to OpenAI
                                        await openai_ws.send_json({
                                            "type": "conversation.item.create",
                                            "item": {
                                                "type": "function_call_output",
                                                "call_id": call_id,
                                                "output": output_str
                                            }
                                        })
                                        # Command OpenAI to generate spoken continuation
                                        await openai_ws.send_json({"type": "response.create"})

                                        await client_ws.send_json({
                                            "type": "tool_done",
                                            "name": tool_name,
                                            "status": status
                                        })

                                    elif event_type == "session.created":
                                        sess_info = event.get("session", {})
                                        await client_ws.send_json({
                                            "type": "session_ready",
                                            "session_id": sess_info.get("id", session_id),
                                            "model": model
                                        })

                                    elif event_type == "error":
                                        err_obj = event.get("error", {})
                                        err_msg = err_obj.get("message", "OpenAI Realtime Error") if isinstance(err_obj, dict) else str(err_obj)
                                        logger.error(f"OpenAI Realtime error: {err_msg}")
                                        await client_ws.send_json({
                                            "type": "error",
                                            "message": err_msg
                                        })
                                except Exception as ex:
                                    logger.error(f"Error processing upstream frame: {ex}", exc_info=True)
                            elif msg.type == aiohttp.WSMsgType.BINARY:
                                await client_ws.send_bytes(msg.data)
                            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                                break
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                logger.error(f"Upstream WebSocket error: {openai_ws.exception()}")
                                break
                    except (ConnectionResetError, asyncio.CancelledError):
                        pass
                    except Exception as e:
                        logger.error(f"Error in openai_to_client relay: {e}", exc_info=True)

                client_task = asyncio.create_task(client_to_openai())
                openai_task = asyncio.create_task(openai_to_client())

                done, pending = await asyncio.wait(
                    [client_task, openai_task],
                    return_when=asyncio.FIRST_COMPLETED
                )

                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

    except Exception as conn_err:
        logger.error(f"Failed to connect to OpenAI Realtime API: {conn_err}", exc_info=True)
        if not client_ws.closed:
            await client_ws.send_json({
                "type": "error",
                "message": f"Failed to connect to OpenAI Realtime API: {str(conn_err)}"
            })
    finally:
        # Record the dialogue in the user's session (the thread: appended to it).
        if session_id and (user_transcripts or assistant_transcripts):
            try:
                entries = [{"role": "user", "content": ut} for ut in user_transcripts]
                entries += [{"role": "assistant", "content": at} for at in assistant_transcripts]
                append_history(user.id, session_id, entries, prune=False)
                logger.info(f"Recorded realtime dialogue to history for session {session_id}")
            except Exception as hist_err:
                logger.warning(f"Could not persist realtime session history: {hist_err}")

        if not client_ws.closed:
            try:
                await client_ws.send_json({"type": "call_ended", "reason": "closed"})
                await client_ws.close()
            except Exception:
                pass

        logger.info(f"Realtime WebSocket closed for session {session_id}")

    return client_ws
