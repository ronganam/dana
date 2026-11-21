"""Usage and cost of everything that bills: chat models, background jobs,
transcription, decision models (TypeSafe) and realtime voice.

Capture costs the calling code nothing:

* **LiteLLM calls** (chat, ``llm.complete*``, ``llm.transcribe``, the agent loop,
  streamed or not, failures too) are caught by one LiteLLM callback. LiteLLM
  already knows the provider's token counts and the price, so no call site
  computes anything. What a call was *for* travels in ``litellm_metadata`` (never
  sent to a provider): :func:`meta` builds it from the call's ``purpose`` /
  ``ref`` / ``source`` and the surrounding :func:`scope`.
* **Calls outside LiteLLM** call :func:`record` themselves: TypeSafe
  (``addons/phone/notifications/triage.py``, priced per input token by :func:`decision_cost`) and the
  OpenAI realtime WebSocket (``core/realtime.py``, priced from LiteLLM's price
  table by :func:`realtime_cost`).

Storage stays small and off the hot path:

* :func:`record` only appends a dict to an in-memory buffer (never raises, never
  touches SQLite). :func:`run_loop` writes the buffer in one transaction every
  ``USAGE_FLUSH_SECONDS`` on a worker thread.
* ``usage_events`` keeps one row per call for ``USAGE_RAW_DAYS`` (60); older days
  are folded into ``usage_daily`` (one row per day × kind × purpose × source ×
  model) and the raw rows deleted, so history is kept for good at a few KB a day.
* Every row carries its local ``day`` (``core.clock`` zone), so a day means the
  same thing in both tables and range queries are plain ``day`` comparisons.
* Every row carries the user the call was made for (``user_id``: the current
  user, :func:`core.users.current`, which runs, requests, Telegram updates and
  background entry points bind; it travels in ``litellm_metadata`` like the
  rest of the attribution). ``''`` means no user (system work).

A call whose price is unknown (``cost`` NULL, ``ok``) is counted as *unpriced*
rather than as free; the screen says how many there are.

Reads: :func:`summary` (totals, per day, breakdowns by kind / purpose / source /
model, previous period) and :func:`events` (recent or most expensive calls),
both for one user or everyone (``user_id=None``).
Routes: ``core/usage_api.py``. Docs: ``docs/usage.md``.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import datetime
import json
import logging
import os
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Iterator, List, Optional

import litellm
from litellm.integrations.custom_logger import CustomLogger

from core import clock, purposes, runs, users
from core.db import get_db_connection

logger = logging.getLogger(__name__)

FLUSH_SECONDS = float(os.getenv("USAGE_FLUSH_SECONDS", "3"))
RAW_DAYS = int(os.getenv("USAGE_RAW_DAYS", "60"))
ROLLUP_EVERY_SECONDS = 6 * 3600
BUFFER_MAX = 20_000

KINDS = ("llm", "transcribe", "decision", "realtime")
KIND_LABELS = {"llm": "Language models", "transcribe": "Transcription", "decision": "Decision models",
               "realtime": "Realtime voice"}


# Dimensions the summary can be filtered / broken down by (columns shared by
# usage_events and usage_daily).
DIMENSIONS = ("kind", "purpose", "source", "model")

_RANGES = {"today": 1, "7d": 7, "30d": 30, "90d": 90, "1y": 365, "all": None}


# --- Attribution -------------------------------------------------------------

_scope: contextvars.ContextVar[Optional[Dict[str, str]]] = contextvars.ContextVar("dana_usage_scope", default=None)


@contextlib.contextmanager
def scope(*, source: Optional[str] = None, ref: Optional[str] = None,
          purpose: Optional[str] = None) -> Iterator[None]:
    """Attribute every call made inside the block (tasks created inside copy it).

    ``source`` is what started the work (``app``, ``telegram``, ``whatsapp``,
    ``phone``, ``background``…), ``ref`` the thing it was for (``call:12``,
    ``notification:98``, ``session:main``), ``purpose`` what it was (a purpose of
    core/purposes.py). Anything not given keeps the outer scope's value.
    """
    merged = dict(_scope.get() or {})
    merged.update({k: v for k, v in (("source", source), ("ref", ref), ("purpose", purpose)) if v})
    token = _scope.set(merged)
    try:
        yield
    finally:
        _scope.reset(token)


def bind(source: str) -> None:
    """Attribute the rest of the current *task* to ``source``. For the start of a
    run's task, which already has its own context copy (no block to undo)."""
    _scope.set({**(_scope.get() or {}), "source": source})


def attribution(purpose: str = "", ref: str = "", source: str = "") -> Dict[str, str]:
    """The ``purpose`` / ``source`` / ``ref`` for a call: explicit values first,
    then the surrounding :func:`scope`, then the live run (an agent run is the
    app's, for its session), then ``background``."""
    s = _scope.get() or {}
    run = runs.current_run()
    return {
        "purpose": purpose or s.get("purpose") or "other",
        "source": source or s.get("source") or ("app" if run else "background"),
        "ref": ref or s.get("ref") or (f"session:{run.session_id}" if run else ""),
    }


def meta(purpose: str = "", ref: str = "", source: str = "", kind: str = "") -> Dict[str, Any]:
    """``litellm_metadata=`` for one LiteLLM call (see module docstring). ``kind``
    only for what LiteLLM can't tell: audio sent to a chat model is ``transcribe``."""
    tag = attribution(purpose, ref, source)
    tag["user_id"] = users.current_id() or ""
    if kind:
        tag["kind"] = kind
    return {"usage": tag}


# --- Capture -------------------------------------------------------------------

_buffer: Deque[Dict[str, Any]] = deque(maxlen=BUFFER_MAX)


def record(*, kind: str, purpose: str, model: str, cost: Optional[float], ok: bool = True,
           source: str = "", ref: str = "", provider: str = "", input_tokens: int = 0,
           output_tokens: int = 0, cached_tokens: int = 0, reasoning_tokens: int = 0,
           units: Optional[float] = None, ms: int = 0, error: str = "",
           user_id: Optional[str] = None) -> None:
    """Queue one billable call. Never raises, never blocks (the buffer is
    written by :func:`run_loop`). ``cost`` is USD, ``None`` when the price is
    unknown. ``units`` is audio seconds for transcription. ``user_id``: whose
    call it was (None: the current user; ``""``: nobody's)."""
    try:
        attr = attribution(purpose, ref, source)
        ts = time.time()
        _buffer.append({
            "ts": ts, "day": clock.from_timestamp(ts).strftime("%Y-%m-%d"),
            "user_id": (users.current_id() or "") if user_id is None else user_id, "kind": kind,
            "purpose": attr["purpose"], "source": attr["source"], "ref": attr["ref"],
            "provider": provider or "", "model": model or "",
            "input_tokens": int(input_tokens or 0), "output_tokens": int(output_tokens or 0),
            "cached_tokens": int(cached_tokens or 0), "reasoning_tokens": int(reasoning_tokens or 0),
            "units": units, "cost": None if cost is None else float(cost), "ms": int(ms or 0),
            "ok": 1 if ok else 0, "error": (error or "")[:200],
        })
    except Exception:
        logger.warning("Could not queue a usage record", exc_info=True)


def _int(obj: Any, *path: str) -> int:
    for key in path:
        obj = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
        if obj is None:
            return 0
    try:
        return int(obj)
    except (TypeError, ValueError):
        return 0


def _from_litellm(kwargs: Dict[str, Any], response: Any, started: Any, ended: Any, ok: bool) -> None:
    """LiteLLM callback body: one finished (or failed) call → one record."""
    try:
        std = kwargs.get("standard_logging_object") or {}
        md = ((kwargs.get("litellm_params") or {}).get("metadata") or {}).get("usage") or {}
        usage = getattr(response, "usage", None) if response is not None else None
        tokens_in = int(std.get("prompt_tokens") or _int(usage, "prompt_tokens"))
        tokens_out = int(std.get("completion_tokens") or _int(usage, "completion_tokens"))
        cost = std.get("response_cost")
        if cost is None:
            cost = kwargs.get("response_cost")
        if ok and (cost is None or (not cost and (tokens_in or tokens_out))):
            cost = None  # LiteLLM has no price for this model: unknown, not free
        call_type = str(std.get("call_type") or kwargs.get("call_type") or "")
        transcribe = "transcription" in call_type or md.get("kind") == "transcribe"
        units = None
        if transcribe and response is not None:
            units = getattr(response, "duration", None)
            units = float(units) if isinstance(units, (int, float)) else None
        error = ""
        if not ok:
            error = str(std.get("error_str") or kwargs.get("exception") or "")
            cost = None
        record(
            kind="transcribe" if transcribe else "llm",
            purpose=md.get("purpose", ""), source=md.get("source", ""), ref=md.get("ref", ""),
            provider=str(std.get("custom_llm_provider") or ""), model=str(std.get("model") or kwargs.get("model") or ""),
            input_tokens=tokens_in, output_tokens=tokens_out,
            cached_tokens=_int(usage, "prompt_tokens_details", "cached_tokens"),
            reasoning_tokens=_int(usage, "completion_tokens_details", "reasoning_tokens"),
            units=units, cost=cost, ok=ok, error=error, user_id=md.get("user_id"),
            ms=int((ended - started).total_seconds() * 1000) if started and ended else 0)
    except Exception:
        logger.warning("Could not read a LiteLLM call for usage tracking", exc_info=True)


class _LiteLLMHook(CustomLogger):
    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        _from_litellm(kwargs, response_obj, start_time, end_time, True)

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        _from_litellm(kwargs, response_obj, start_time, end_time, False)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        _from_litellm(kwargs, response_obj, start_time, end_time, True)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        _from_litellm(kwargs, response_obj, start_time, end_time, False)


def install() -> None:
    """Register the LiteLLM callback (idempotent)."""
    if not any(isinstance(c, _LiteLLMHook) for c in litellm.callbacks):
        litellm.callbacks.append(_LiteLLMHook())


install()


# --- Calls outside LiteLLM -------------------------------------------------------

# TypeSafe bills input tokens only (output is free): $0.042 per million for Jev
# (docs.typesafe.ai/models). Its responses carry no usage, so the input size is estimated.
TYPESAFE_USD_PER_MTOK = 0.042


def estimate_tokens(payload: Any) -> int:
    """Rough input-token count of a JSON-able payload: ~4 characters per token for
    ASCII, ~1.5 for other scripts (Hebrew tokenizes poorly). Only for models whose API
    reports no usage."""
    text = json.dumps(payload, ensure_ascii=False, default=str)
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return round(ascii_chars / 4 + (len(text) - ascii_chars) / 1.5)


def decision_cost(input_tokens: int) -> Optional[float]:
    """USD for a decision-model request of ``input_tokens`` (estimated), at
    ``TYPESAFE_COST_PER_MTOK`` (default: Jev's published price). None when the env value is not a number."""
    try:
        per_mtok = float(os.environ.get("TYPESAFE_COST_PER_MTOK", TYPESAFE_USD_PER_MTOK))
    except ValueError:
        return None
    return input_tokens * per_mtok / 1e6


def _price(info: Dict[str, Any], key: str, fallback: str = "") -> float:
    return float(info.get(key) or (info.get(fallback) if fallback else 0) or 0)


def realtime_cost(model: str, usage: Dict[str, Any]) -> Optional[float]:
    """USD for one realtime ``response.done`` usage object, from LiteLLM's price
    table (text / audio / image tokens, cached input at its own rate). None when
    the model has no price."""
    info = litellm.model_cost.get(model) or litellm.model_cost.get(f"openai/{model}")
    if not info or not usage:
        return None
    d_in = usage.get("input_token_details") or {}
    if not d_in:
        d_in = {"text_tokens": usage.get("input_tokens") or 0}
    cached = d_in.get("cached_tokens_details") or {}
    cost = 0.0
    for part, rate, cache_rate in (
        ("text", "input_cost_per_token", "cache_read_input_token_cost"),
        ("audio", "input_cost_per_audio_token", "cache_read_input_audio_token_cost"),
        ("image", "input_cost_per_image_token", "cache_read_input_image_token_cost"),
    ):
        total, hit = int(d_in.get(f"{part}_tokens") or 0), int(cached.get(f"{part}_tokens") or 0)
        cost += max(total - hit, 0) * _price(info, rate) + hit * _price(info, cache_rate, rate)
    d_out = usage.get("output_token_details") or {"text_tokens": usage.get("output_tokens") or 0}
    cost += int(d_out.get("text_tokens") or 0) * _price(info, "output_cost_per_token")
    cost += int(d_out.get("audio_tokens") or 0) * _price(info, "output_cost_per_audio_token", "output_cost_per_token")
    return cost


def record_realtime(model: str, usage: Dict[str, Any], *, session_id: str = "", transcription: bool = False) -> None:
    """One realtime event's usage: a model response (``response.done``) or the
    input-audio transcription of one user turn (billed as its own model)."""
    if not isinstance(usage, dict) or not usage:
        return
    if transcription:
        # `whisper-1` reports seconds, the gpt-4o transcribe models report tokens.
        model = "whisper-1"
        seconds = usage.get("seconds") if usage.get("type") == "duration" else None
        info = litellm.model_cost.get(model) or {}
        cost = seconds * _price(info, "input_cost_per_second") if seconds is not None and info else None
        record(kind="realtime", purpose="realtime_voice", source="app", ref=f"session:{session_id}" if session_id else "",
               provider="openai", model=model, input_tokens=int(usage.get("input_tokens") or 0),
               output_tokens=int(usage.get("output_tokens") or 0), units=seconds, cost=cost)
        return
    cached = (usage.get("input_token_details") or {}).get("cached_tokens") or 0
    record(kind="realtime", purpose="realtime_voice", source="app", ref=f"session:{session_id}" if session_id else "",
           provider="openai", model=model, input_tokens=int(usage.get("input_tokens") or 0),
           output_tokens=int(usage.get("output_tokens") or 0), cached_tokens=int(cached),
           cost=realtime_cost(model, usage))


# --- Storage -------------------------------------------------------------------

_EVENT_COLUMNS = ("ts", "day", "user_id", "kind", "purpose", "source", "ref", "provider", "model", "input_tokens",
                  "output_tokens", "cached_tokens", "reasoning_tokens", "units", "cost", "ms", "ok", "error")


def _write(rows: List[Dict[str, Any]]) -> None:
    placeholders = ",".join("?" for _ in _EVENT_COLUMNS)
    with get_db_connection() as conn:
        # scoped: inserts rows that carry their own user_id column
        conn.executemany(f"INSERT INTO usage_events ({','.join(_EVENT_COLUMNS)}) VALUES ({placeholders})",
                         [tuple(r[c] for c in _EVENT_COLUMNS) for r in rows])
        conn.commit()


def flush_sync() -> int:
    """Write everything queued; returns how many rows. A failed write puts the
    rows back (the buffer is capped) and raises."""
    rows: List[Dict[str, Any]] = []
    while True:
        try:
            rows.append(_buffer.popleft())
        except IndexError:
            break
    if not rows:
        return 0
    try:
        _write(rows)
    except Exception:
        _buffer.extendleft(reversed(rows))
        raise
    return len(rows)


async def flush() -> int:
    return await asyncio.to_thread(flush_sync)


def rollup_sync(raw_days: int = RAW_DAYS) -> int:
    """Fold raw rows from days before the last ``raw_days`` into ``usage_daily``
    and delete them (one transaction; late rows for an already folded day add
    to its row). Returns how many raw rows were folded."""
    cutoff = (clock.now().date() - datetime.timedelta(days=raw_days)).isoformat()
    with get_db_connection() as conn:
        # scoped: server-wide roll-up of old raw rows into daily totals
        n = conn.execute("SELECT COUNT(*) FROM usage_events WHERE day < ?", (cutoff,)).fetchone()[0]
        if not n:
            return 0
        conn.execute("""
            INSERT INTO usage_daily (day, user_id, kind, purpose, source, model, calls, failures, input_tokens,
                                     output_tokens, cached_tokens, cost, unpriced, ms)
            SELECT day, user_id, kind, purpose, source, model, COUNT(*), SUM(ok = 0), SUM(input_tokens),
                   SUM(output_tokens), SUM(cached_tokens), COALESCE(SUM(cost), 0), SUM(ok = 1 AND cost IS NULL), SUM(ms)
            FROM usage_events WHERE day < ? GROUP BY day, user_id, kind, purpose, source, model
            ON CONFLICT (day, user_id, kind, purpose, source, model) DO UPDATE SET
                calls = calls + excluded.calls, failures = failures + excluded.failures,
                input_tokens = input_tokens + excluded.input_tokens,
                output_tokens = output_tokens + excluded.output_tokens,
                cached_tokens = cached_tokens + excluded.cached_tokens, cost = cost + excluded.cost,
                unpriced = unpriced + excluded.unpriced, ms = ms + excluded.ms""", (cutoff,))
        # scoped: server-wide roll-up of old raw rows into daily totals
        conn.execute("DELETE FROM usage_events WHERE day < ?", (cutoff,))
        conn.commit()
    return n


async def run_loop() -> None:
    """Background task: write the buffer every ``FLUSH_SECONDS``, fold old days
    every few hours. Flushes once more when cancelled (shutdown)."""
    last_rollup: Optional[float] = None
    try:
        while True:
            await asyncio.sleep(FLUSH_SECONDS)
            try:
                if _buffer:
                    await flush()
                if last_rollup is None or time.monotonic() - last_rollup > ROLLUP_EVERY_SECONDS:
                    last_rollup = time.monotonic()
                    folded = await asyncio.to_thread(rollup_sync)
                    if folded:
                        logger.info(f"Usage: folded {folded} raw rows older than {RAW_DAYS} days into daily totals")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Usage write failed (will retry, {len(_buffer)} queued): {e}")
    finally:
        try:
            flush_sync()
        except Exception as e:
            logger.warning(f"Final usage flush failed: {e}")


# --- Reads ---------------------------------------------------------------------

def _clean_filters(filters: Optional[Dict[str, Any]], allowed: tuple) -> Dict[str, str]:
    return {k: str(v) for k, v in (filters or {}).items() if k in allowed and v not in (None, "")}


def _bounds(rng: str) -> tuple:
    """(first day, last day, day count) of ``rng`` ending today; first = None for ``all``."""
    if rng not in _RANGES:
        raise ValueError(f"range must be one of: {', '.join(_RANGES)}")
    today = clock.now().date()
    days = _RANGES[rng]
    first = None if days is None else today - datetime.timedelta(days=days - 1)
    return first, today, days


_SUM_COLUMNS = ("calls", "failures", "input_tokens", "output_tokens", "cached_tokens", "cost", "unpriced", "ms")


def _grouped(conn, first: Optional[datetime.date], last: datetime.date, filters: Dict[str, str],
             user_id: Optional[str]) -> List[Dict[str, Any]]:
    """Rows per day × kind × purpose × source × model, raw and folded together,
    of one user (``None``: everyone)."""
    where, params = ["day <= ?"], [last.isoformat()]
    if first is not None:
        where.append("day >= ?")
        params.append(first.isoformat())
    if user_id is not None:
        where.append("user_id = ?")
        params.append(user_id)
    for key, value in filters.items():
        where.append(f"{key} = ?")
        params.append(value)
    clause = " AND ".join(where)
    # scoped: where gets user_id = ? when one user is asked; None = everyone (see_everyone)
    sql = f"""
        SELECT day, kind, purpose, source, model, SUM(calls) AS calls, SUM(failures) AS failures,
               SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens,
               SUM(cached_tokens) AS cached_tokens, SUM(cost) AS cost, SUM(unpriced) AS unpriced, SUM(ms) AS ms
        FROM (
            SELECT day, kind, purpose, source, model, 1 AS calls, (ok = 0) AS failures, input_tokens,
                   output_tokens, cached_tokens, COALESCE(cost, 0) AS cost,
                   (ok = 1 AND cost IS NULL) AS unpriced, ms
            FROM usage_events WHERE {clause}
            UNION ALL
            SELECT day, kind, purpose, source, model, calls, failures, input_tokens, output_tokens,
                   cached_tokens, cost, unpriced, ms
            FROM usage_daily WHERE {clause}
        ) GROUP BY day, kind, purpose, source, model"""
    return [dict(r) for r in conn.execute(sql, params * 2).fetchall()]


def _total(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    t: Dict[str, Any] = {c: 0 for c in _SUM_COLUMNS}
    for r in rows:
        for c in _SUM_COLUMNS:
            t[c] += r[c] or 0
    t["cost"] = round(t["cost"], 6)
    t["avg_ms"] = round(t["ms"] / t["calls"]) if t["calls"] else 0
    del t["ms"]
    return t


def _label(dimension: str, key: str) -> str:
    if dimension == "kind":
        return KIND_LABELS.get(key, key)
    if dimension == "purpose":
        return purposes.label(key) or key.replace("_", " ").capitalize()
    return key or "—"


def summary(rng: str = "30d", filters: Optional[Dict[str, Any]] = None,
            user_id: Optional[str] = None) -> Dict[str, Any]:
    """Totals, per-day series, breakdowns and the previous period for ``rng``
    (``today`` · ``7d`` · ``30d`` · ``90d`` · ``1y`` · ``all``), optionally
    narrowed by ``kind`` / ``purpose`` / ``source`` / ``model``, of one user
    (``None``: everyone, system calls included)."""
    first, last, days = _bounds(rng)
    flt = _clean_filters(filters, DIMENSIONS)
    with get_db_connection() as conn:
        rows = _grouped(conn, first, last, flt, user_id)
        previous = None
        if first is not None:
            prev_last = first - datetime.timedelta(days=1)
            previous = _total(_grouped(conn, prev_last - datetime.timedelta(days=days - 1), prev_last, flt,
                                       user_id))
    by_day: Dict[str, Dict[str, Any]] = {}
    if first is None and rows:
        first = datetime.date.fromisoformat(min(r["day"] for r in rows))
    if first is not None:
        for i in range((last - first).days + 1):
            d = (first + datetime.timedelta(days=i)).isoformat()
            by_day[d] = {"day": d, "cost": 0.0, "calls": 0}
    for r in rows:
        slot = by_day.setdefault(r["day"], {"day": r["day"], "cost": 0.0, "calls": 0})
        slot["cost"] += r["cost"] or 0
        slot["calls"] += r["calls"] or 0
    breakdown: Dict[str, List[Dict[str, Any]]] = {}
    for dim in DIMENSIONS:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            groups.setdefault(r[dim], []).append(r)
        items = []
        for key, members in groups.items():
            t = _total(members)
            items.append({"key": key, "label": _label(dim, key), **t})
        items.sort(key=lambda i: (-i["cost"], -i["calls"]))
        breakdown[dim] = items
    return {
        "range": rng, "from": first.isoformat() if first else None, "to": last.isoformat(),
        "filters": flt, "totals": _total(rows), "previous": previous,
        "by_day": [{**v, "cost": round(v["cost"], 6)} for v in by_day.values()],
        "breakdown": breakdown,
        "raw_days": RAW_DAYS,
    }


def events(limit: int = 50, before: Optional[int] = None, sort: str = "recent", rng: str = "7d",
           filters: Optional[Dict[str, Any]] = None, user_id: Optional[str] = None) -> Dict[str, Any]:
    """Individual calls still held raw (``USAGE_RAW_DAYS``), newest first or most
    expensive first, filtered by dimension / ``ref`` / ``ok``, of one user
    (``None``: everyone). ``before`` is the ``next`` cursor of the previous
    page (recent order only)."""
    first, _last, _days = _bounds(rng)
    flt = _clean_filters(filters, DIMENSIONS + ("ref", "ok"))
    where, params = ["1 = 1"], []
    if first is not None:
        where.append("day >= ?")
        params.append(first.isoformat())
    if user_id is not None:
        where.append("user_id = ?")
        params.append(user_id)
    for key, value in flt.items():
        where.append(f"{key} = ?")
        params.append(int(value) if key == "ok" else value)
    if before is not None and sort == "recent":
        where.append("id < ?")
        params.append(int(before))
    order = "cost IS NULL, cost DESC, id DESC" if sort == "cost" else "id DESC"
    limit = max(1, min(int(limit), 200))
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            # scoped: where gets user_id = ? when one user is asked; None = everyone (see_everyone)
            f"SELECT * FROM usage_events WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?", params + [limit]).fetchall()]
    for r in rows:
        r["ok"] = bool(r["ok"])
        r["purpose_label"] = _label("purpose", r["purpose"])
        r.pop("day", None)
        r.pop("user_id", None)
    return {"events": rows, "next": rows[-1]["id"] if sort == "recent" and len(rows) == limit else None,
            "raw_days": RAW_DAYS}
