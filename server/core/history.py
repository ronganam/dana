"""History limits: how much of a conversation the model sees.

Used for ordinary sessions (core/session.py, stored pruned) and for the model
window of the shared thread (core/thread.py).
"""
import json
from typing import Any, Dict, List

# Serialized-size budget for the history the model sees. Message count alone
# lets a few huge tool results through, and history is re-sent on every turn.
MAX_HISTORY_CHARS = 60_000
# Messages of history the model sees.
MAX_HISTORY_MESSAGES = 40


def prune_history(history: List[Dict[str, Any]], max_len: int = MAX_HISTORY_MESSAGES,
                  max_chars: int = MAX_HISTORY_CHARS) -> List[Dict[str, Any]]:
    """Keep the most recent part of the conversation within both ``max_len``
    messages and ``max_chars`` of serialized size.

    The cut always lands on a user message, so the kept history never starts
    with a tool result or an orphaned assistant tool call (Gemini rejects
    both). The latest user turn is always kept, even if it alone is over
    budget — tool results are already capped individually by the agent.
    """
    if not history:
        return history

    start = len(history)
    size = 0
    while start > 0 and len(history) - start < max_len:
        size += len(json.dumps(history[start - 1], ensure_ascii=False, default=str))
        if size > max_chars:
            break
        start -= 1

    user_idx = [i for i, m in enumerate(history) if m.get("role") == "user"]
    if not user_idx:
        return history
    cut = next((i for i in user_idx if i >= start), user_idx[-1])
    return history[cut:]
