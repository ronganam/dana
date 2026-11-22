"""Limits on wrong guesses at short secrets (pairing codes, the setup code).

A :class:`FailureLimit` counts failures in a sliding window, per client
(:func:`client_key`) and for the whole server: past either limit every
attempt is refused until the window has moved on, the right one included. A
success does not reset the count. The overall limit bounds guessing from many
addresses (or forged ``X-Forwarded-For`` values on a server reached without a
proxy) at the cost of pausing that one action for everybody for a while.
In memory: a restart clears it.
"""

import time
from collections import deque
from typing import Deque, Dict, Optional

from aiohttp import web


def client_key(request: web.Request) -> str:
    """Who is asking, for counting: the last ``X-Forwarded-For`` entry (the
    address the nearest proxy saw) when there is one, else the peer address."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded.strip():
        last = forwarded.split(",")[-1].strip()
        if last:
            return last[:64]
    return request.remote or "unknown"


class FailureLimit:
    """At most ``per_client`` failures per client and ``overall`` failures in
    all within ``window`` seconds."""

    def __init__(self, per_client: int, overall: int, window: float) -> None:
        self.per_client = per_client
        self.overall = overall
        self.window = window
        self._by_client: Dict[str, Deque[float]] = {}
        self._all: Deque[float] = deque()

    def _trim(self, times: Deque[float], now: float) -> None:
        while times and now - times[0] >= self.window:
            times.popleft()

    def retry_after(self, key: str) -> Optional[int]:
        """Seconds until ``key`` may try again (None: it may now)."""
        now = time.monotonic()
        self._trim(self._all, now)
        mine = self._by_client.get(key)
        if mine is not None:
            self._trim(mine, now)
            if not mine:
                del self._by_client[key]
        waits = []
        if mine and len(mine) >= self.per_client:
            waits.append(self.window - (now - mine[0]))
        if len(self._all) >= self.overall:
            waits.append(self.window - (now - self._all[0]))
        return max(1, int(max(waits)) + 1) if waits else None

    def fail(self, key: str) -> None:
        """Count one failure of ``key``."""
        now = time.monotonic()
        self._by_client.setdefault(key, deque()).append(now)
        self._all.append(now)

    def clear(self) -> None:
        """Forget every failure (tests)."""
        self._by_client.clear()
        self._all.clear()


def too_many(retry_after: int) -> web.Response:
    """The answer to an attempt past the limit: 429 ``rate_limited``."""
    return web.json_response({"error": "rate_limited", "retry_after": retry_after}, status=429,
                             headers={"Retry-After": str(retry_after)})


# Wrong pairing codes (POST /api/pair, a code typed on /login): 10 per client
# and 200 in all per 10 minutes, against codes of 8 characters from 32 that
# live 5 minutes.
PAIRING_FAILURES = FailureLimit(per_client=10, overall=200, window=600.0)
# Wrong setup codes (POST /api/setup/admin).
SETUP_FAILURES = FailureLimit(per_client=10, overall=100, window=600.0)
