"""Work that runs after every ingest of a sync (docs/money.md, "Sync").

Other parts of the money kind register here instead of being imported by
``sync.py``: classification and reconciliation of provisional rows, the
unusual-charge check. A hook gets ``(user, source_id, new_ids, changed_ids)``,
may be sync or async, and runs as that user; a failing hook is logged and the
next one still runs."""

import inspect
import logging
from typing import Any, Callable, List

from core.users import User

logger = logging.getLogger(__name__)

TransactionsHook = Callable[[User, str, List[str], List[str]], Any]

_hooks: List[TransactionsHook] = []


def on_transactions(fn: TransactionsHook) -> TransactionsHook:
    """Register ``fn(user, source_id, new_ids, changed_ids)`` to run after each
    ingest (usable as a decorator; registering the same function twice is a no-op)."""
    if fn not in _hooks:
        _hooks.append(fn)
    return fn


async def run(user: User, source_id: str, new_ids: List[str], changed_ids: List[str]) -> None:
    """Call every registered hook in order, logging and swallowing errors."""
    for fn in list(_hooks):
        try:
            result = fn(user, source_id, list(new_ids), list(changed_ids))
            if inspect.isawaitable(result):
                await result
        except Exception as e:
            logger.error(f"Money hook {getattr(fn, '__qualname__', fn)} failed for user {user.id}: {e}",
                         exc_info=True)
