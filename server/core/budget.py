"""The server's monthly budget for model calls (docs/usage.md, "Monthly budget").

An admin sets a limit in USD on the Usage page (``PUT /api/usage/budget``,
``manage_server``), stored in ``server_settings['budget']`` (no env; none set
= no budget). The month's spend is everyone's (system calls included), read
from core/usage.py; the forecast carries that spend on at the same daily rate
to the end of the month.

- ``ok``: below 80 % of the limit (or no limit);
- ``warn``: from 80 %: a Now card for whoever may manage the server
  (core/now.py), dismissible for the rest of the month;
- ``paused``: from 100 %: core/llm.py refuses every call of a background
  purpose (core/purposes.py) with :class:`Paused` until the month ends or the
  limit is raised. Chat, voice and anything a person started keep working:
  user-started purposes, and any call made inside an agent run or a
  :func:`user_started` block. Each background loop asks :func:`holds` before
  its pass and skips quietly; the pause is logged once a month and the
  ``budget`` self-check (core/checks.py) says it.

The spend is read at most every :data:`CACHE_SECONDS` (a call costs nothing
extra); a change of the limit reads it again at once.
"""
from __future__ import annotations

import calendar
import contextlib
import contextvars
import datetime
import logging
import time
from typing import Any, Dict, Iterator, Optional

from core import clock, purposes, runs, server_settings, usage

logger = logging.getLogger(__name__)

SETTING = "budget"
WARN_AT = 0.8
CACHE_SECONDS = 60.0
MAX_LIMIT = 1_000_000.0

OK, WARN, PAUSED = "ok", "warn", "paused"


class Paused(RuntimeError):
    """A background model call refused because the month's budget is spent."""

    code = "budget_paused"


_user_started: contextvars.ContextVar[bool] = contextvars.ContextVar("dana_budget_user_started", default=False)
# (monotonic time read, month "YYYY-MM", USD spent)
_cache: Optional[tuple] = None
_logged_month: Optional[str] = None


def forget_cache() -> None:
    """Read the spend again on next use (a limit change, tests)."""
    global _cache
    _cache = None


def limit() -> Optional[float]:
    """The monthly limit in USD (None: no budget)."""
    value = server_settings.get(SETTING, {})
    raw = value.get("limit") if isinstance(value, dict) else None
    try:
        amount = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
    return amount if amount and amount > 0 else None


def set_limit(amount: Optional[float], *, by: Optional[str]) -> Dict[str, Any]:
    """Set the monthly limit (None or 0: no budget); ``by``: who changed it. Returns :func:`state`.

    Raises:
        ValueError: not a number, negative, or more than :data:`MAX_LIMIT`.
    """
    if amount is not None:
        if isinstance(amount, bool):
            raise ValueError("limit must be a number of US dollars")
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            raise ValueError("limit must be a number of US dollars") from None
        if amount != amount or amount < 0 or amount > MAX_LIMIT:
            raise ValueError(f"limit must be between 0 and {MAX_LIMIT:,.0f}")
    if not amount:
        server_settings.delete(SETTING)
        logger.info("Monthly budget removed")
    else:
        server_settings.put(SETTING, {"limit": round(amount, 2)}, by=by)
        logger.info(f"Monthly budget set to ${amount:,.2f}")
    forget_cache()
    return state()


def _month(today: datetime.date) -> tuple:
    """(first day, first day of the next month) of ``today``'s month."""
    first = today.replace(day=1)
    days = calendar.monthrange(today.year, today.month)[1]
    return first, first + datetime.timedelta(days=days)


def spent(today: Optional[datetime.date] = None) -> float:
    """USD spent by everyone this month (read at most every :data:`CACHE_SECONDS`)."""
    global _cache
    today = today or clock.now().date()
    month = today.strftime("%Y-%m")
    if _cache is not None and _cache[1] == month and time.monotonic() - _cache[0] < CACHE_SECONDS:
        return _cache[2]
    try:
        amount = usage.spent_since(_month(today)[0])
    except Exception as e:  # before the first migration, or a locked database: keep the last answer
        logger.debug(f"Budget: the month's spend could not be read: {e}")
        return _cache[2] if _cache is not None and _cache[1] == month else 0.0
    _cache = (time.monotonic(), month, amount)
    return amount


def state(today: Optional[datetime.date] = None) -> Dict[str, Any]:
    """``{limit, spent, forecast, state, month, resets}``: the limit (None: no
    budget), the month's spend and forecast in USD, ok | warn | paused, the
    month ("YYYY-MM") and the day the pause lifts on its own (ISO date)."""
    today = today or clock.now().date()
    first, nxt = _month(today)
    cap = limit()
    used = spent(today)
    days = (nxt - first).days
    forecast = used / today.day * days
    level = OK
    if cap is not None:
        level = PAUSED if used >= cap else WARN if used >= cap * WARN_AT else OK
    return {"limit": cap, "spent": round(used, 4), "forecast": round(forecast, 4), "state": level,
            "month": today.strftime("%Y-%m"), "resets": nxt.isoformat()}


def paused() -> bool:
    """Whether the month's budget is spent (background work waits)."""
    cap = limit()
    return cap is not None and spent() >= cap


@contextlib.contextmanager
def user_started() -> Iterator[None]:
    """Treat every model call inside the block as started by a person (an
    endpoint that runs a background purpose on request): the budget never
    pauses it."""
    token = _user_started.set(True)
    try:
        yield
    finally:
        _user_started.reset(token)


def started_by_user() -> bool:
    """Whether the current call was started by a person: inside an agent run or a :func:`user_started` block."""
    return _user_started.get() or runs.current_run() is not None


def holds(purpose: str) -> bool:
    """Whether a call for ``purpose`` must wait for the budget now: a
    background purpose, not started by a person, while the budget is spent.
    Background loops ask this before a pass and skip it quietly."""
    global _logged_month
    if not purposes.is_background(purpose) or started_by_user():
        return False
    if not paused():
        _logged_month = None
        return False
    month = clock.now().strftime("%Y-%m")
    if _logged_month != month:
        _logged_month = month
        cap = limit() or 0
        logger.warning(f"Monthly budget of ${cap:,.2f} reached: background model work is paused until "
                       f"{_month(clock.now().date())[1].isoformat()} or the budget is raised")
    return True


def check(purpose: str) -> None:
    """Refuse a call for ``purpose`` while :func:`holds` says it must wait (core/llm.py).

    Raises:
        Paused: the month's budget is spent and ``purpose`` is background work.
    """
    if holds(purpose):
        raise Paused(f"Background work ({purpose}) is paused: this month's model budget is spent")
