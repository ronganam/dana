"""Context chats: a chat opened from an item ("Ask the assistant" from a
suggestion, call, notification, task, person, weekly review, transaction or saving idea), and the
on-screen context of one turn (core/session_runner.py).

A composer over the addons: each addon builds its own card and prompt, for the
chat's user and only from that user's own items; this module dispatches by
kind, stores the session for that user and rebuilds the prompt on every
turn (so it reflects edits and acceptance).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from core import users, weekly_review
from core.session import create_new_session_db, get_session_state
from core.users import User
from core.addons import store
from addons.money import config as money_config, queries as money_queries, savings as money_savings
from addons.people import detail as people_detail, values as people_values
from addons.phone.calls import chat as phone_chat
from addons.tasks import links as task_links, service as tasks_service

logger = logging.getLogger(__name__)


def build_chat_context(user: Optional[User], kind: str, ref_id: str) -> Dict[str, Any]:
    """The attachment card + agent context for a chat of ``user`` opened from their item.

    Raises:
        LookupError: not one of their items.
        ValueError: an unknown kind.
        NotConnected: a person without the ``people`` addon, a review without ``tasks``, a
            transaction or a saving without ``money``.
    """
    kind = (kind or "").strip().lower()
    if kind == "suggestion":
        return phone_chat.suggestion_card(user, ref_id)
    if kind == "call":
        return phone_chat.call_card(user, ref_id)
    if kind == "notification":
        return phone_chat.notification_card(user, ref_id)
    if kind == "task":
        row = tasks_service.get_task(user, ref_id)
        if not row:
            raise LookupError("Task not found")
        links = task_links.links_for_tasks(user, [row["id"]]).get(row["id"], [])
        call_link = next((l for l in links if l["ref_type"] == "call"), None)
        return {
            "kind": "task", "ref_id": row["id"], "call_id": call_link["ref_id"] if call_link else None,
            "emoji": "✅", "title": row["title"],
            "subtitle": (f"From call with {call_link['contact']}" if call_link else "Task")
                        + (f" · due {row['due_date']}" if row["due_date"] else ""),
            "quick_prompts": ["Change the reminder", "Break it into steps", "Mark it done"],
        }
    if kind == "person":
        store.require(user, people_values.ADDON)
        return people_detail.chat_context(user, ref_id)
    if kind == "review":
        if user is None:
            raise LookupError("Review not found")
        weekly_review.require(user)
        return weekly_review.chat_context(user, ref_id)
    if kind == "transaction":
        if user is None:
            raise LookupError("Transaction not found")
        store.require(user, money_config.ADDON)
        return money_queries.chat_context(user, ref_id)
    if kind == "saving":
        if user is None:
            raise LookupError("Saving not found")
        store.require(user, money_config.ADDON)
        return money_savings.chat_context(user, ref_id)
    raise ValueError("kind must be suggestion, call, notification, task, person, review, transaction or saving")


def start_context_session(user: User, kind: str, ref_id: str) -> Dict[str, Any]:
    """A new chat of ``user`` with their item attached as its context."""
    ctx = build_chat_context(user, kind, ref_id)
    session_id = str(uuid.uuid4())
    title = f"{ctx['emoji']} {ctx['title']}"[:80]
    create_new_session_db(user.id, session_id, title, context=ctx)
    return {"session_id": session_id, "title": title, "context": ctx}


def session_context_prompt(user: User, session_id: str) -> Optional[str]:
    """Extra system context for a chat of ``user`` opened from an item (None
    for an ordinary chat), read as that user."""
    state = get_session_state(user.id, session_id)
    with users.acting_as(user):
        return context_prompt(user, (state or {}).get("context"))


def context_prompt(user: Optional[User], ctx: Optional[Dict[str, Any]]) -> Optional[str]:
    """System context for an attached item (``{kind, ref_id}``) of ``user``.
    Never raises (None when the item is gone or not theirs)."""
    try:
        if not ctx:
            return None
        kind, ref = ctx.get("kind"), ctx.get("ref_id")
        if kind == "suggestion":
            return phone_chat.suggestion_prompt(user, ref)
        if kind == "notification":
            return phone_chat.notification_prompt(user, ref)
        if kind == "call":
            return phone_chat.call_prompt(user, ref)
        if kind == "task":
            t = tasks_service.get_task(user, ref)
            if not t:
                return None
            block = (f"## Opened from a task\nTask {t['id']}: “{t['title']}” status={t['status']} priority={t['priority']} "
                     f"due={t['due_date']} triggers={t['triggers']}\nDescription: {t['description'] or ''}\n"
                     "Use update_task with this id for changes.")
            link = next((l for l in task_links.links_for_tasks(user, [ref]).get(ref, []) if l["ref_type"] == "call"), None)
            if link:
                _, call_block = phone_chat.call_block(user, link["ref_id"])
                block += f"\n\n### Source call\n{call_block}"
            return block
        if kind == "person":
            return people_detail.context_prompt(user, ref)
        if kind == "review":
            return weekly_review.context_prompt(user, ref) if user is not None else None
        if kind == "transaction":
            if user is None or not store.is_connected(user, money_config.ADDON):
                return None
            return money_queries.context_prompt(user, ref)
        if kind == "saving":
            if user is None or not store.is_connected(user, money_config.ADDON):
                return None
            return money_savings.context_prompt(user, ref)
    except Exception as e:
        logger.warning(f"context for {(ctx or {}).get('kind')} {(ctx or {}).get('ref_id')} failed: {e}")
    return None
