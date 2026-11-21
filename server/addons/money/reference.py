"""Bank of Israel public data for the savings engine (docs/money.md, "Savings").

The Bank of Israel's SDMX API (CSV, no key, no user data in the request):
the policy rate (series ``BR/MNT_RIB_BOI_D``) and the representative
exchange rates (``EXR/RER_<currency>_ILS``). Answers are cached in memory and
in ``boi-cache.json`` in the money data folder (``core/paths.py``) for a day.
A failed request is logged and remembered for :data:`RETRY_SECONDS`; callers
get None / ``{}`` and leave out what needed it (nothing is guessed).
:func:`fetch_csv` is the one network call (the tests replace it)."""

import csv
import datetime
import io
import json
import logging
import time
from typing import Any, Dict, Optional

import httpx

from core import paths

logger = logging.getLogger(__name__)

BASE_URL = "https://edge.boi.gov.il/FusionEdgeServer/sdmx/v2/data/dataflow/BOI.STATISTICS"
POLICY_SERIES = "BR/1.0/MNT_RIB_BOI_D"
# Currencies whose representative rate is shekels for one unit (the yen is quoted per 100: left out).
FX_CURRENCIES = frozenset({"USD", "EUR", "GBP", "CHF", "CAD", "AUD", "SEK", "NOK", "DKK"})
CACHE_SECONDS = 24 * 3600
RETRY_SECONDS = 15 * 60
TIMEOUT_SECONDS = 5.0

_memory: Dict[str, Dict[str, Any]] = {}
_failed: Dict[str, float] = {}
_loaded = False


def cache_file() -> Any:
    """The day's cache of answers (public data only)."""
    return paths.MONEY_DIR / "boi-cache.json"


def fetch_csv(url: str) -> str:
    """GET ``url`` and return its body (raises on a network error or a non-200 answer)."""
    r = httpx.get(url, timeout=TIMEOUT_SECONDS, headers={"Accept": "text/csv"})
    r.raise_for_status()
    return r.text


def clear_cache() -> None:
    """Forget every cached answer and failure (memory only)."""
    global _loaded
    _memory.clear()
    _failed.clear()
    _loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        data = json.loads(cache_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(data, dict):
        for url, entry in data.items():
            if isinstance(entry, dict) and isinstance(entry.get("text"), str) and isinstance(entry.get("at"), (int, float)):
                _memory.setdefault(url, entry)


def _save() -> None:
    now = time.time()
    keep = {u: e for u, e in _memory.items() if now - e["at"] < CACHE_SECONDS}
    try:
        cache_file().parent.mkdir(parents=True, exist_ok=True)
        cache_file().write_text(json.dumps(keep), encoding="utf-8")
    except OSError as e:
        logger.warning(f"Bank of Israel cache not written: {e}")


def _get(url: str) -> Optional[str]:
    """The body of ``url``, from the cache when fetched less than a day ago; None after a failure."""
    _load()
    now = time.time()
    entry = _memory.get(url)
    if entry and now - entry["at"] < CACHE_SECONDS:
        return entry["text"]
    if now - _failed.get(url, 0) < RETRY_SECONDS:
        return None
    try:
        text = fetch_csv(url)
    except Exception as e:
        _failed[url] = now
        logger.warning(f"Bank of Israel data not fetched ({url.rsplit('/', 1)[-1][:60]}): {e}")
        return None
    _memory[url] = {"at": now, "text": text}
    _save()
    return text


def parse(text: str) -> Dict[str, float]:
    """``{YYYY-MM-DD: value}`` from an SDMX CSV (columns ``TIME_PERIOD`` and ``OBS_VALUE``)."""
    out: Dict[str, float] = {}
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    for row in reader:
        norm = {(k or "").strip().upper(): (v or "").strip() for k, v in row.items()}
        day, value = norm.get("TIME_PERIOD", ""), norm.get("OBS_VALUE", "")
        try:
            datetime.date.fromisoformat(day[:10])
            out[day[:10]] = float(value)
        except ValueError:
            continue
    return out


def _url(series: str, start: datetime.date, end: datetime.date) -> str:
    return f"{BASE_URL}/{series}?startperiod={start.isoformat()}&endperiod={end.isoformat()}&format=csv"


def _series(series: str, start: datetime.date, end: datetime.date) -> Dict[str, float]:
    text = _get(_url(series, start, end))
    return parse(text) if text else {}


def _at_or_before(values: Dict[str, float], day: datetime.date) -> Optional[str]:
    found = [d for d in values if d <= day.isoformat()]
    return max(found) if found else None


def policy_rate(today: datetime.date) -> Optional[Dict[str, Any]]:
    """The Bank of Israel's policy rate: ``{rate (percent), date, year_ago, year_ago_date}``
    (``year_ago``: the rate in force a year before ``today``, None when unknown); None
    when the data could not be fetched."""
    values = _series(POLICY_SERIES, today - datetime.timedelta(days=400), today)
    last = _at_or_before(values, today)
    if last is None:
        return None
    before = _at_or_before(values, today - datetime.timedelta(days=365))
    return {"rate": values[last], "date": last, "year_ago": values[before] if before else None,
            "year_ago_date": before}


def fx_rates(currency: str, start: datetime.date, end: datetime.date) -> Dict[str, float]:
    """``{YYYY-MM-DD: shekels for one unit}``: the representative rate of each business day in
    [start, end]; ``{}`` for a currency not in :data:`FX_CURRENCIES` or when not fetched."""
    code = (currency or "").upper()
    if code not in FX_CURRENCIES:
        return {}
    return _series(f"EXR/1.0/RER_{code}_ILS", start, end)


def rate_on(rates: Dict[str, float], day: str) -> Optional[float]:
    """The rate of ``day`` or of the last business day before it (within a week), None without one."""
    if day in rates:
        return rates[day]
    try:
        d = datetime.date.fromisoformat(day[:10])
    except ValueError:
        return None
    for back in range(1, 8):
        value = rates.get((d - datetime.timedelta(days=back)).isoformat())
        if value is not None:
            return value
    return None
