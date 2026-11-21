"""Runs the conversation for a user: messages, approvals, selections, step
limits, learned phrases.

Every entry point (:func:`start_agent_task`, :func:`start_resume_task`,
:func:`start_resume_selection_task`, :func:`cancel_agent_task`) takes the
:class:`core.users.User` the work is for. The run is started for that user
(core/runs.py binds them as the current user inside the run), and the user's
sessions, histories and pending prompts are read and written through
core/session.py. Channels (web handlers in core/assistant_api.py, the Telegram
adapter in core/telegram_bot.py) only call these functions and render results.

This module owns the one :class:`core.agent.AgentOrchestrator` every
conversation runs on (:func:`agent`).
"""
import asyncio
import logging
import time
import traceback
import json
import uuid
from typing import Dict, Any, Optional, List
from core.agent import UNTRUSTED_CONTEXT, AgentOrchestrator, limit_tool_output
from core.runs import emit, runs, Run, SessionBusyError, add_undos, current_run
from core import context_chats, device_screen as device_screen_mod
from core import shortcuts, thread, undo, usage, user_profile, users
from core.images import image_url_to_data_uri
from core.models import resolve_profile
from core.session import (
    append_history,
    create_new_session_db,
    get_history,
    get_pending,
    get_session_state,
    pop_pending,
    set_pending,
    set_session_status,
    set_session_title,
    save_session_debug,
)
from core.users import User
from integrations.framework import registry

logger = logging.getLogger(__name__)

_agent: Optional[AgentOrchestrator] = None


def agent() -> AgentOrchestrator:
    """The agent every conversation runs on (created on first use with the
    chat model, ``core.llm.model("chat")``)."""
    global _agent
    if _agent is None:
        _agent = AgentOrchestrator()
    return _agent


def set_agent(orchestrator: AgentOrchestrator) -> None:
    """Use ``orchestrator`` for every conversation from now on."""
    global _agent
    _agent = orchestrator

# Additional iterations granted each time the user extends the budget.
EXTRA_ITERATIONS_PER_EXTENSION = 10

# Kinds a message's `screen` may name ("the user is looking at this").
SCREEN_KINDS = ("suggestion", "call", "task", "person", "notification", "review", "transaction", "saving")

def _convert_image_urls_in_content(content):
    """Convert relative image URLs to base64 data URIs in content (only the
    current user's own images: core/images.py)."""
    if not isinstance(content, list):
        return content
    converted = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "image_url":
            url = part.get("image_url", {}).get("url", "")
            if url and not url.startswith("data:"):
                data_uri = image_url_to_data_uri(url, users.current())
                if data_uri:
                    converted.append({"type": "image_url", "image_url": {"url": data_uri}})
                elif url.startswith("http://") or url.startswith("https://"):
                    converted.append(part)
                else:
                    logger.warning(f"Could not convert relative image URL to data URI: '{url}'. Replacing with placeholder text to prevent LLM error.")
                    converted.append({"type": "text", "text": "[Attached image unavailable]"})
            else:
                converted.append(part)
        else:
            converted.append(part)
    return converted


def _convert_image_urls_in_history(history):
    """Convert all relative image URLs to base64 data URIs in history messages."""
    converted_history = []
    for msg in history:
        if isinstance(msg, dict) and isinstance(msg.get("content"), list):
            msg_copy = dict(msg)
            msg_copy["content"] = _convert_image_urls_in_content(msg["content"])
            converted_history.append(msg_copy)
        else:
            converted_history.append(msg)
    return converted_history


# --- "On screen" context ---------------------------------------------------------

def normalize_screen(screen: Any) -> Optional[Dict[str, str]]:
    """``{kind, id}`` when valid, else None."""
    if not isinstance(screen, dict):
        return None
    kind = str(screen.get("kind") or "").strip().lower()
    ref = str(screen.get("id") or screen.get("ref_id") or "").strip()
    if kind not in SCREEN_KINDS or not ref:
        return None
    return {"kind": kind, "id": ref}


def screen_context_prompt(user: User, screen: Optional[Dict[str, str]]) -> Optional[str]:
    """System context for one turn about the item on ``user``'s screen, built
    with the context-chat prompt builders (core/context_chats.py), only from
    their own items."""
    screen = normalize_screen(screen)
    if not screen:
        return None
    body = context_chats.context_prompt(user, {"kind": screen["kind"], "ref_id": screen["id"]})
    if not body:
        return None
    return (
        "## ON SCREEN NOW (this message only)\n"
        f"{user_profile.for_user(user).first_name} is looking at this {screen['kind']} while sending this message, "
        "so 'this', 'it', 'that' and "
        "'here' mean it. The details below were written for chats opened from an item; they apply to this "
        "message only, not to the rest of the conversation.\n\n" + body
    )


# Context kinds whose prompt carries text written by other people (a transfer's memo): a turn that
# carries one (the chat's own context or the item on screen) runs with untrusted content, so its
# acts_outside tools ask first.
UNTRUSTED_CONTEXT_KINDS = ("transaction", "saving")


def _untrusted_context(user: User, session_id: str, screen: Optional[Dict[str, str]]) -> bool:
    if screen and screen.get("kind") in UNTRUSTED_CONTEXT_KINDS:
        return True
    ctx = (get_session_state(user.id, session_id) or {}).get("context") or {}
    return ctx.get("kind") in UNTRUSTED_CONTEXT_KINDS


def _extra_context(user: User, session_id: str, screen: Optional[Dict[str, str]],
                   device_screen: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """This turn's extra system context: the chat's own context, the item on
    screen in Dana's app (``screen``) and the phone screen the user shared
    (``device_screen``, untrusted). The last two are never stored."""
    parts = [context_chats.session_context_prompt(user, session_id), screen_context_prompt(user, screen),
             device_screen_mod.prompt_block(user, device_screen)]
    joined = "\n\n".join(p for p in parts if p)
    return joined or None


# --- Runs ------------------------------------------------------------------------

async def run_agent_background(user: User, session_id: str, user_text: str, is_voice: bool,
                               model_id: Optional[str] = None, device: str = "",
                               screen: Optional[Dict[str, str]] = None, has_images: bool = False,
                               device_screen: Optional[Dict[str, Any]] = None):
    try:
        set_session_status(user.id, session_id, "running")
        usage.bind(device or "app")

        history = get_history(user.id, session_id)

        # Prepare the user message for the LLM — convert stored image URLs to base64 data URIs
        last_msg = history[-1] if history else None
        user_message_for_llm = user_text
        if last_msg and isinstance(last_msg.get("content"), list):
            user_message_for_llm = _convert_image_urls_in_content(last_msg["content"])
            if not user_message_for_llm:
                user_message_for_llm = user_text

        # Convert all image URLs in history to data URIs before sending to LLM
        history_for_llm = _convert_image_urls_in_history(history[:-1])

        # Run agent loop
        extra_context = _extra_context(user, session_id, screen, device_screen)
        # The shared phone screen (or a context others wrote) is untrusted: this turn's
        # acts_outside tools ask first (the flag rides on the run's suspended state, not globally).
        untrusted: Any = True if device_screen else (
            UNTRUSTED_CONTEXT if _untrusted_context(user, session_id, screen) else False)
        result = await agent().run(user_message_for_llm, history_for_llm, model_id=model_id,
                                   extra_context=extra_context, untrusted_content=untrusted)  # run on prior history, run() will append user_text

        await handle_agent_result(user, session_id, result, is_voice, user_text, model_id=model_id, device=device)
        if not screen and not has_images and not device_screen:
            _learn_shortcut(user, user_text, result)
        return result

    except asyncio.CancelledError:
        logger.info(f"Agent task cancelled for session {session_id}")
        set_session_status(user.id, session_id, "idle")
        try:
            save_session_debug(user.id, session_id, error_context="Task interrupted by user")
        except Exception:
            pass
        raise
    except Exception as e:
        logger.error(f"Error in background agent loop for session {session_id}: {e}", exc_info=True)
        set_session_status(user.id, session_id, "error")
        save_session_debug(user.id, session_id, error_context=traceback.format_exc(), agent=agent())
        raise e


def _learn_shortcut(user: User, user_text: str, result: Any) -> None:
    if not isinstance(result, tuple):
        return
    try:
        shortcuts.learn(user, user_text, result[1], result[0])
    except Exception:
        logger.warning("Learning a shortcut failed", exc_info=True)


async def run_shortcut_background(user: User, session_id: str, user_text: str, is_voice: bool,
                                  model_id: Optional[str], device: str, shortcut: Dict[str, Any]):
    """Answer a learned phrase without the model: run its tool, reply with the
    stored confirmation, and store the turn as a normal tool call. Falls back
    to the model (and deactivates the phrase) when the tool fails. A tool the
    user no longer has goes to the model as if no phrase had matched
    (:func:`core.shortcuts.match` already says no; this covers a disconnect
    in between)."""
    tool_name, tool_args = shortcut["tool"], dict(shortcut["args"])
    if not registry.has_tool(user, tool_name):
        logger.info(f"Shortcut '{shortcut['phrase']}': {tool_name} is not available to the user; asking the model")
        return await run_agent_background(user, session_id, user_text, is_voice, model_id, device=device)
    try:
        set_session_status(user.id, session_id, "running")
        tc_id = "sc_" + uuid.uuid4().hex[:12]
        emit({"type": "step_start", "iteration": 1, "max_iterations": 10, "shortcut": True})
        emit({"type": "tool_call", "id": tc_id, "name": tool_name, "iteration": 1})
        emit({"type": "tool_start", "id": tc_id, "name": tool_name, "args": tool_args, "iteration": 1})
        started = time.monotonic()
        with undo.collect() as undos:
            try:
                result_str = str(await registry.execute(user, tool_name, tool_args))
                ok = not shortcuts.looks_failed(result_str)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Shortcut tool {tool_name} raised: {e}")
                result_str, ok = f"Error executing tool: {e}", False
        add_undos(undos)
        emit({"type": "tool_end", "id": tc_id, "name": tool_name, "ok": ok, "result": result_str,
              "duration_ms": int((time.monotonic() - started) * 1000), "undos": list(undos), "shortcut": True})

        if not ok:
            shortcuts.deactivate(shortcut["id"], f"tool failed: {result_str[:200]}")
            logger.info(f"Shortcut '{shortcut['phrase']}' failed; asking the model instead")
            return await run_agent_background(user, session_id, user_text, is_voice, model_id, device=device)

        reply = shortcut["reply"]
        run = current_run()
        if run is not None:
            run.shortcut = True
        emit({"type": "text_delta", "iteration": 1, "delta": reply})
        emit({"type": "final", "content": reply, "shortcut": True})
        entries = [
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": tc_id, "type": "function",
                "function": {"name": tool_name, "arguments": json.dumps(tool_args, ensure_ascii=False)},
            }]},
            {"role": "tool", "tool_call_id": tc_id, "name": tool_name, "content": limit_tool_output(result_str)},
            {"role": "assistant", "content": reply},
        ]
        shortcuts.mark_used(shortcut["id"])
        result = (reply, entries)
        await handle_agent_result(user, session_id, result, is_voice, user_text, model_id=model_id, device=device)
        return result
    except asyncio.CancelledError:
        logger.info(f"Shortcut task cancelled for session {session_id}")
        set_session_status(user.id, session_id, "idle")
        raise
    except Exception as e:
        logger.error(f"Error in shortcut run for session {session_id}: {e}", exc_info=True)
        set_session_status(user.id, session_id, "error")
        save_session_debug(user.id, session_id, error_context=traceback.format_exc(), agent=agent())
        raise e


async def resume_agent_background(user: User, session_id: str, action: str, device: str = ""):
    try:
        set_session_status(user.id, session_id, "running")

        pending = pop_pending(user.id, session_id)
        if pending is None:
            logger.warning(f"No pending action found to resume for session {session_id}")
            set_session_status(user.id, session_id, "idle")
            return None

        tool_name = pending["tool_name"]
        tool_call_id = pending["tool_call_id"]
        device = device or pending.get("device") or ""
        usage.bind(device or "app")

        # Execute tool or simulate refusal
        started = time.monotonic()
        with undo.collect() as undos:
            if action == "approve":
                logger.info(f"User approved action: '{tool_name}' with args: {pending['tool_args']}")
                try:
                    tool_result = await registry.execute(user, tool_name, pending["tool_args"])
                    tool_result_str = str(tool_result)
                    ok = True
                except registry.ToolNotAvailable as e:
                    # Checked again at resume: the integration was disconnected
                    # since the card was shown. The model gets the same refusal
                    # as for a made-up tool and finishes the turn.
                    logger.info(f"Approved '{tool_name}' is no longer available to user {user.id}: {e}")
                    tool_result_str = f"Error executing tool: {e}"
                    ok = False
                except Exception as e:
                    logger.error(f"Error executing tool '{tool_name}' on resume: {e}", exc_info=True)
                    tool_result_str = f"Error executing tool: {e}"
                    ok = False
            else:
                logger.info(f"User denied action: '{tool_name}'")
                tool_result_str = "Error: User denied/cancelled this action. Do not attempt this action again."
                ok = False
        add_undos(undos)

        # The pre-approval stream already emitted `tool_start` for this id and
        # suspended, so close the card the user is still looking at.
        emit({
            "type": "tool_end",
            "id": tool_call_id,
            "name": tool_name,
            "ok": ok,
            "result": tool_result_str,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "approved": action == "approve",
            "undos": list(undos),
        })

        # Resume the agent loop
        # NOTE: Use a fresh new_entries list — the previous new_entries were already
        # extended into history during handle_agent_result when the agent suspended.
        # Reusing the same list would duplicate every entry, creating consecutive
        # assistant messages without intervening tool responses, which Gemini rejects.
        # Convert any relative image URLs in messages to data URIs
        messages_for_llm = _convert_image_urls_in_history(pending["messages"])
        result = await agent().resume_run(
            messages=messages_for_llm,
            new_entries=[],
            iteration=pending["iteration"],
            max_iterations=10,
            tool_result_str=tool_result_str,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            pending_tool_calls=pending["pending_tool_calls"],
            model_id=pending.get("model_id"),
            untrusted_content=pending.get("untrusted_content") or False,
        )

        await handle_agent_result(user, session_id, result, pending.get("is_voice", False),
                                  pending.get("user_text", ""), model_id=pending.get("model_id"), device=device)
        return result

    except asyncio.CancelledError:
        logger.info(f"Agent resume task cancelled for session {session_id}")
        set_session_status(user.id, session_id, "idle")
        try:
            save_session_debug(user.id, session_id, error_context="Task interrupted by user")
        except Exception:
            pass
        raise
    except Exception as e:
        logger.error(f"Error in background resume agent loop for session {session_id}: {e}", exc_info=True)
        set_session_status(user.id, session_id, "error")
        save_session_debug(user.id, session_id, error_context=traceback.format_exc(), agent=agent())
        raise e

async def handle_agent_result(user: User, session_id: str, result, is_voice: bool, user_text: str,
                              model_id: Optional[str] = None, device: str = ""):
    """Store a run's outcome in the user's session: the suspended state (and
    the turn so far) when it waits for the user, else the finished turn."""
    # Capture any error that occurred in the agent (e.g. LLM call failure)
    orchestrator = agent()
    error_context = orchestrator.last_error
    orchestrator.last_error = None

    if isinstance(result, dict) and result.get("status") in ("pending_approval", "tool_call", "iterations_exceeded", "awaiting_selection"):
        status = result["status"]
        # Extract numeric chat_id if session_id has tg_ prefix, or pass session_id as chat_id
        chat_id_val = session_id[3:] if session_id.startswith("tg_") else session_id
        payload = {
            "messages": result["messages"],
            "new_entries": result["new_entries"],
            "iteration": result.get("iteration"),
            "pending_tool_calls": result.get("pending_tool_calls"),
            "tool_name": result.get("tool_name"),
            "tool_args": result.get("tool_args"),
            "tool_call_id": result.get("tool_call_id"),
            "status": status,
            "is_voice": is_voice,
            "user_text": user_text,
            "chat_id": chat_id_val,
            "model_id": model_id,
            "device": device,
            # The turn carries the shared phone screen (True) or a context others wrote
            # ("untrusted_context"): its resume keeps asking before acts_outside tools.
            "untrusted_content": result.get("untrusted_content") or False,
        }
        if result.get("approval_reason"):
            payload["approval_reason"] = result["approval_reason"]
        if status == "iterations_exceeded":
            payload["max_iterations"] = result.get("max_iterations")
        if status == "awaiting_selection":
            payload["prompt"] = result.get("prompt", "")
            payload["options"] = result.get("options", [])
            payload["multiple"] = bool(result.get("multiple", False))
            payload["context"] = result.get("context", "")
        set_pending(user.id, session_id, payload)
        # Append any new entries (e.g. tool messages) to history
        append_history(user.id, session_id, result.get("new_entries", []), device)

        if status == "iterations_exceeded":
            set_session_status(user.id, session_id, "iterations_exceeded")
        elif status in ("pending_approval", "tool_call"):
            set_session_status(user.id, session_id, "waiting_approval")
        else:
            set_session_status(user.id, session_id, status)
        save_session_debug(user.id, session_id, error_context=error_context, agent=orchestrator)
    else:
        response_text, new_entries = result
        append_history(user.id, session_id, new_entries, device)

        # Generate/set a title if session has no title yet or is default
        session_state = get_session_state(user.id, session_id)
        if session_state and not thread.is_thread(session_id) and (not session_state.get("title") or session_state["title"] == "New Chat"):
            # Use the first user message or a shortened assistant response as title
            first_msg = user_text[:30] + ("..." if len(user_text) > 30 else "")
            set_session_title(user.id, session_id, first_msg)

        set_session_status(user.id, session_id, "idle")
        save_session_debug(user.id, session_id, error_context=error_context, agent=orchestrator)

async def resume_iterations_background(user: User, session_id: str, action: str, device: str = ""):
    """Resume a session that hit the iteration limit.

    Args:
        action: 'extend_iterations' to continue with a larger budget,
                'decline_iterations' to stop and let the user pivot the session.
    """
    try:
        pending = pop_pending(user.id, session_id)
        if pending is None:
            logger.warning(f"No pending iteration-extension found to resume for session {session_id}")
            set_session_status(user.id, session_id, "idle")
            return None

        if pending.get("status") != "iterations_exceeded":
            logger.warning(
                f"Session {session_id} pending state is '{pending.get('status')}', "
                "not 'iterations_exceeded' — ignoring extension request"
            )
            set_session_status(user.id, session_id, "idle")
            return None
        device = device or pending.get("device") or ""
        usage.bind(device or "app")

        if action == "decline_iterations":
            return await decline_iterations_background(user, session_id, pending, device=device)

        set_session_status(user.id, session_id, "running")

        base = pending.get("max_iterations") or pending.get("iteration") or 10
        new_max = base + EXTRA_ITERATIONS_PER_EXTENSION
        logger.info(f"Extending iteration limit for session {session_id}: {base} -> {new_max}")

        # Convert any relative image URLs in messages to data URIs
        messages_for_llm = _convert_image_urls_in_history(pending["messages"])
        result = await agent().resume_iterations(
            messages=messages_for_llm,
            iteration=pending.get("iteration") or 0,
            max_iterations=new_max,
            model_id=pending.get("model_id"),
            untrusted_content=pending.get("untrusted_content") or False,
        )

        await handle_agent_result(
            user, session_id, result,
            pending.get("is_voice", False),
            pending.get("user_text", ""),
            model_id=pending.get("model_id"),
            device=device,
        )
        return result

    except asyncio.CancelledError:
        logger.info(f"Agent task cancelled for session {session_id}")
        set_session_status(user.id, session_id, "idle")
        raise
    except Exception as e:
        logger.error(f"Error in background extend-iterations loop for session {session_id}: {e}", exc_info=True)
        set_session_status(user.id, session_id, "error")
        save_session_debug(user.id, session_id, error_context=traceback.format_exc(), agent=agent())
        raise e


async def decline_iterations_background(user: User, session_id: str, pending: Dict[str, Any], device: str = ""):
    """Stop the agent after it hit the iteration limit so the user can pivot.

    The partial tool trace is already in history (extended by handle_agent_result),
    so a follow-up user message continues the same conversation.
    """
    reply = (
        "Stopped — the task ran out of steps before I could finish. "
        "You can continue this conversation with more details, a correction, or a "
        "rephrased request and I'll pick it up from here."
    )
    append_history(user.id, session_id, [{"role": "assistant", "content": reply}], device)

    set_session_status(user.id, session_id, "idle")
    save_session_debug(user.id, session_id, agent=agent())
    return reply, []


def _drop_unanswered_prompt(user: User, session_id: str, device: str) -> None:
    """The shared thread moves on when a new message arrives while an
    approval / selection / step-limit prompt is still open (often on another
    device): the prompt counts as declined, and every tool call left without
    a result gets one, so the history stays valid for the model."""
    pending = pop_pending(user.id, session_id)
    if pending is None:
        return
    if pending.get("status") in ("pending_approval", "tool_call", "awaiting_selection"):
        entries = []
        for tc in pending.get("pending_tool_calls") or []:
            if isinstance(tc, str):
                continue
            tc_id, name, _ = AgentOrchestrator._parse_tool_call(tc)
            if tc_id:
                entries.append({"role": "tool", "tool_call_id": tc_id, "name": name,
                                "content": "Not done: the user sent a new message instead of answering. "
                                           "Do not do this unless they ask again."})
        append_history(user.id, session_id, entries, device)
    set_session_status(user.id, session_id, "idle")
    logger.info(f"Thread {session_id}: dropped an unanswered '{pending.get('status')}' prompt")


def clear_thread(user: User, session_id: str, device: str = "") -> None:
    """"New topic" on the shared thread: an open prompt counts as declined
    and the model forgets everything said so far (core/thread.py).

    Raises :class:`core.runs.SessionBusyError` while a run is going.
    """
    busy = runs.active(user.id, session_id)
    if busy is not None:
        raise SessionBusyError(busy)
    create_new_session_db(user.id, session_id)
    _drop_unanswered_prompt(user, session_id, device)
    thread.clear(user.id, session_id)


def _model_info(user: User, model_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not model_id:
        return None
    return resolve_profile(model_id, user.level).public()


def start_agent_task(
    user: User,
    session_id: str,
    user_text: str,
    is_voice: bool = False,
    images: list = None,
    model_id: Optional[str] = None,
    pre_events: Optional[List[Dict[str, Any]]] = None,
    device: str = "",
    screen: Optional[Dict[str, Any]] = None,
    device_screen: Optional[Dict[str, Any]] = None,
) -> Run:
    """Append the user's message to their session (created if missing) and
    start a run answering it, for ``user``.

    Args:
        device: Which device sent it (phone|watch|tablet|tv|mac|web); stored
            with the message in the shared thread.
        screen: ``{kind, id}`` of the item the user is looking at; added to
            this turn's system context only.
        device_screen: What is on the phone screen, shared by the user with
            this message (core/device_screen.py). Added to this turn's system
            context only, never stored; untrusted, so this turn's
            ``acts_outside`` tools ask first.

    A text message matching an active learned phrase (core/shortcuts.py) is
    answered without the model (never one with ``screen``, ``device_screen``
    or images).

    Raises :class:`core.runs.SessionBusyError` if the session is already running.
    """
    busy = runs.active(user.id, session_id)
    if busy is not None:
        raise SessionBusyError(busy)

    screen = normalize_screen(screen)
    device_screen = device_screen_mod.normalize(device_screen) if device_screen is not None else None
    create_new_session_db(user.id, session_id)
    if thread.is_thread(session_id):
        _drop_unanswered_prompt(user, session_id, device)

    if images:
        content = [{"type": "text", "text": user_text}]
        for img_url in images:
            content.append({"type": "image_url", "image_url": {"url": img_url}})
        user_entry = {"role": "user", "content": content}
    else:
        user_entry = {"role": "user", "content": user_text}
    append_history(user.id, session_id, [user_entry], device, prune=False)

    shortcut = None
    if not images and not screen and not device_screen and get_pending(user.id, session_id) is None:
        try:
            shortcut = shortcuts.match(user, user_text)
        except Exception:
            logger.warning("Shortcut lookup failed", exc_info=True)

    if shortcut:
        body = lambda: run_shortcut_background(user, session_id, user_text, is_voice, model_id, device, shortcut)
    else:
        body = lambda: run_agent_background(user, session_id, user_text, is_voice, model_id,
                                            device=device, screen=screen, has_images=bool(images),
                                            device_screen=device_screen)
    return runs.start(
        user, session_id, "message", body,
        model=_model_info(user, model_id),
        pre_events=pre_events or (),
    )


def _pending_model_id(user: User, session_id: str) -> Optional[str]:
    pending = get_pending(user.id, session_id) or {}
    return pending.get("model_id")


def cancel_agent_task(user: User, session_id: str) -> bool:
    """Cancel the user's running run in the session, if any."""
    return runs.cancel(user.id, session_id)


def start_resume_task(user: User, session_id: str, action: str, device: str = "") -> Run:
    """Start a run resuming the user's session after approve/deny/extend/decline."""
    if action in ("extend_iterations", "decline_iterations"):
        body = lambda: resume_iterations_background(user, session_id, action, device=device)
    else:
        body = lambda: resume_agent_background(user, session_id, action, device=device)
    return runs.start(user, session_id, action, body, model=_model_info(user, _pending_model_id(user, session_id)))


def start_resume_selection_task(user: User, session_id: str, selection: List[int], device: str = "") -> Run:
    """Start a run resuming the user's session after they answered a
    ``request_user_selection`` prompt (empty selection = cancelled)."""
    return runs.start(
        user, session_id, "select",
        lambda: resume_selection_background(user, session_id, selection, device=device),
        model=_model_info(user, _pending_model_id(user, session_id)),
    )

async def resume_selection_background(user: User, session_id: str, selection: List[int], device: str = ""):
    """Resume the agent loop after the user answered an interactive selection prompt.

    Args:
        user: Whose session it is.
        session_id: The session to resume.
        selection: Option indices the user picked; empty list means cancelled.

    Returns:
        The agent ``_run_loop`` result: a ``(text, new_entries)`` tuple on
        completion or a dict on suspend (approval / selection / iteration limit).
    """
    try:
        pending = pop_pending(user.id, session_id)
        if pending is None:
            logger.warning(f"No pending selection found to resume for session {session_id}")
            set_session_status(user.id, session_id, "idle")
            return None

        if pending.get("status") != "awaiting_selection":
            logger.warning(
                f"Session {session_id} pending state is '{pending.get('status')}', "
                "not 'awaiting_selection' — ignoring selection"
            )
            set_session_status(user.id, session_id, "idle")
            return None
        device = device or pending.get("device") or ""
        usage.bind(device or "app")

        options = pending.get("options") or []
        selected = [
            options[i]
            for i in selection
            if isinstance(i, int) and 0 <= i < len(options)
        ]

        if not selected:
            logger.info(f"User cancelled selection for session {session_id}")
            tool_result_str = "Error: User denied/cancelled this action. Do not attempt this action again."
        else:
            logger.info(
                f"User answered selection for session {session_id}: "
                f"{json.dumps(selected, ensure_ascii=False)}"
            )
            tool_result_str = (
                "The user chose the following option(s) from your selection prompt "
                f"(label and value included): {json.dumps(selected, ensure_ascii=False)}. "
                "Treat these as the user's answer and continue the task accordingly."
            )

        set_session_status(user.id, session_id, "running")

        # Convert any relative image URLs in messages to data URIs
        messages_for_llm = _convert_image_urls_in_history(pending["messages"])
        result = await agent().resume_run(
            messages=messages_for_llm,
            new_entries=[],
            iteration=pending["iteration"],
            max_iterations=10,
            tool_result_str=tool_result_str,
            tool_call_id=pending["tool_call_id"],
            tool_name=pending["tool_name"],
            pending_tool_calls=pending["pending_tool_calls"],
            model_id=pending.get("model_id"),
            untrusted_content=pending.get("untrusted_content") or False,
        )

        await handle_agent_result(
            user, session_id, result,
            pending.get("is_voice", False),
            pending.get("user_text", ""),
            model_id=pending.get("model_id"),
            device=device,
        )
        return result

    except asyncio.CancelledError:
        logger.info(f"Agent task cancelled for session {session_id}")
        set_session_status(user.id, session_id, "idle")
        raise
    except Exception as e:
        logger.error(f"Error in background selection resume loop for session {session_id}: {e}", exc_info=True)
        set_session_status(user.id, session_id, "error")
        save_session_debug(user.id, session_id, error_context=traceback.format_exc(), agent=agent())
        raise e
