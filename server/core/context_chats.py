"""Context chats: a chat opened from an item ("Ask the assistant" from a
suggestion, call, notification, task, person, weekly review, transaction or
saving idea), and the on-screen context of one turn (core/session_runner.py).

A composer over the addons: each addon registers its own card, prompt and
optional block through ``@addon.context_card`` / ``@addon.context`` /
``@addon.context_block``; ``review`` is core's own. This module dispatches by
kind, stores the session for that user and rebuilds the prompt on every turn
(so it reflects edits and acceptance).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from core import users, weekly_review
from core.addons import ContextRequest, registry, store
from core.session import create_new_session_db, get_session_state
from core.users import User

logger = logging.getLogger(__name__)


def build_chat_context(user: Optional[User], kind: str, ref_id: str) -> Dict[str, Any]:
    """The attachment card + agent context for a chat of ``user`` opened from their item.

    Raises:
        LookupError: not one of their items.
        ValueError: an unknown kind.
        NotConnected: the addon owning ``kind`` is not active for ``user``.
    """
    kind = (kind or "").strip().lower()
    if kind == "review":
        if user is None:
            raise LookupError("Review not found")
        weekly_review.require(user)
        return weekly_review.chat_context(user, ref_id)
    hook = registry.hook_for("context_card", kind, user)  # NotConnected → 403 not_connected
    if hook is not None:
        if user is None:
            raise LookupError(f"{kind.capitalize()} not found")
        return hook.func(user, ref_id)
    kinds = ["review", *[h.key for h in registry.hooks("context_card", None)]]
    raise ValueError(f"kind must be one of {', '.join(kinds)}")


def _block(user: Optional[User], kind: str, ref_id: str) -> str:
    """Another item's description for an addon's context prompt (e.g. a task's source call); "" when there is none."""
    if not kind or not ref_id or user is None:
        return ""
    try:
        hook = registry.hook_for("context_block", kind, user)
    except store.NotConnected:
        return ""
    if hook is None:
        return ""
    try:
        return str(hook.func(user, ref_id) or "")
    except Exception:
        return ""


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
        if not ctx or user is None:
            return None
        kind, ref = ctx.get("kind"), ctx.get("ref_id")
        if kind == "review":
            return weekly_review.context_prompt(user, ref)
        try:
            hook = registry.hook_for("context", str(kind or ""), user)
        except store.NotConnected:
            return None
        if hook is not None:
            return hook.func(user, ref, ContextRequest(block=lambda k, rid: _block(user, k, rid)))
    except Exception as e:
        logger.warning(f"context for {(ctx or {}).get('kind')} {(ctx or {}).get('ref_id')} failed: {e}")
    return None
