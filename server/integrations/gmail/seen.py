"""The headers of the messages the Gmail tools showed a user, kept in memory
so the Approve card of ``reply_to_email`` can say who the reply goes to
without a request to Gmail (``core/approval_text.py``).

Per user: an entry is found only for the user whose tool call read it.
Nothing is stored on disk and a restart empties it (the card then names the
message by its id).
"""

import threading
from collections import OrderedDict
from typing import Any, Dict, Mapping, Optional, Tuple

from core.users import User
from integrations.gmail import content

MAX_ENTRIES = 2000

_seen: "OrderedDict[Tuple[str, str], Dict[str, str]]" = OrderedDict()
_lock = threading.Lock()


def reply_headers(message: Mapping[str, Any]) -> Dict[str, str]:
    """What a reply to ``message`` is built from: its address headers as
    sent, its subject, its ``Message-ID`` / ``References`` and its thread."""
    payload = message.get("payload")
    out = {name.lower(): content.raw_header(payload, name) for name in content.REPLY_HEADERS}
    out["thread_id"] = str(message.get("threadId") or "")
    return out


def remember(user: User, message: Mapping[str, Any]) -> None:
    """Keep the reply headers of a message a tool of ``user`` just read."""
    message_id = str(message.get("id") or "")
    if not message_id:
        return
    with _lock:
        _seen[(user.id, message_id)] = reply_headers(message)
        _seen.move_to_end((user.id, message_id))
        while len(_seen) > MAX_ENTRIES:
            _seen.popitem(last=False)


def get(user: Optional[User], message_id: Any) -> Optional[Dict[str, str]]:
    """The reply headers of a message ``user``'s tools showed (None when unknown)."""
    if user is None:
        return None
    with _lock:
        return _seen.get((user.id, str(message_id or "")))
