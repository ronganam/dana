"""Price checks in Israel for the Money Save view: one web-grounded search turned into sourced offers
(docs/money-il.md, "Price checks"); money's price checks provider (docs/money.md, "Providers").

A check runs only when the user asks (:func:`start`, from the API) and never in
the background. What goes out is built from the topic and its params alone:
no names, transactions, merchants, account numbers or amounts but the
``current_price`` the user's card gives. Every offer cites a page the search
returned; numbers out of their sane range are dropped; amounts are agorot.
"""

import asyncio
import datetime
import json
import logging
import math
import re
import sqlite3
import uuid
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

import httpx

from core import clock, llm, users
from core.users import User
from addons.money import store
from addons.money.providers import PriceCheckRunning, PriceCheckTooSoon, PriceCheckUnavailable

logger = logging.getLogger(__name__)

TOPICS = ("telecom", "electricity", "fx_card", "arnona")
TELECOM_SERVICES = ("mobile", "home_internet", "triple", "mobile_and_internet")
FX_KINDS = ("card", "wallet", "bank")

PURPOSE = "money_market"
RATE_SECONDS = 6 * 3600          # the same check at most this often…
RETRY_SECONDS = 5 * 60           # …or this often after one that failed
CHECK_TIMEOUT_SECONDS = 240      # a check gives up after this,
INTERRUPTED_SECONDS = 5 * 60     # so a "checking" row older than this was cut off (a restart)
STALE_DAYS = 45
MAX_ITEMS = 8
RESOLVE_TIMEOUT_SECONDS = 5.0
MAX_SEARCH_CHARS = 20000

PRICE_MIN, PRICE_MAX = 500, 50000            # a telecom plan: ₪5–₪500 a month (agorot)
PROMO_MONTHS_MAX = 36
LINES_MAX = 10                               # phone lines in a telecom check or an offer
DISCOUNT_MAX = 30.0                          # electricity / arnona discounts: (0, 30] %
FX_FEE_MAX = 5.0                             # FX fees: [0, 5] %
MONTHLY_FEE_MAX = 10000                      # a card's monthly fee: ₪0–₪100 (agorot)

_REDIRECT_HOST = "vertexaisearch.cloud.google.com"
_REDIRECT_PATH = "/grounding-api-redirect/"
_HEBREW = re.compile(r"[א-ת]")
_CITY = re.compile(r"^[א-ת \-'\"׳״]+$")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")
_FIELDS = {"telecom": ("service", "lines", "current_price"), "electricity": (), "fx_card": (),
           "arnona": ("city", "year")}

ERRORS = {
    "interrupted": "The check was interrupted. Try again.",
    "timeout": "The check took too long. Try again in a few minutes.",
    "no_search": "The model answered without searching the web, so there are no sourced prices. Try again later.",
    "nothing": "No offer with a source was found this time. Try again later.",
    "failed": "The web search did not answer. Try again in a few minutes.",
    "stopped": "The check was stopped. Try again.",
}


class _NothingFound(Exception):
    """The search answered, but no offer survived validation."""


# (user id, topic, params key) → the running check's task.
_running: Dict[Tuple[str, str, str], asyncio.Task] = {}


# --- Params -----------------------------------------------------------------------------------

def _whole(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be a whole number from {low} to {high}")
    return value


def normalize(topic: str, params: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """The check's params, validated, in canonical form. Raises ValueError for an
    unknown topic, an unknown key or a value out of its range."""
    if topic not in TOPICS:
        raise ValueError(f"unknown topic {topic!r}")
    params = {} if params is None else params
    if not isinstance(params, Mapping):
        raise ValueError("params must be an object")
    unknown = sorted(str(k) for k in params if k not in _FIELDS[topic])
    if unknown:
        raise ValueError(f"unknown params for {topic}: {', '.join(unknown)}")
    if topic == "telecom":
        service = params.get("service")
        if not isinstance(service, str) or service not in TELECOM_SERVICES:
            raise ValueError(f"service must be one of {', '.join(TELECOM_SERVICES)}")
        lines = params.get("lines")
        if "current_price" not in params:
            raise ValueError("current_price is required")
        return {"service": service, "lines": None if lines is None else _whole(lines, "lines", 1, LINES_MAX),
                "current_price": _whole(params["current_price"], "current_price", 1, 2000)}
    if topic == "arnona":
        city = params.get("city")
        if not isinstance(city, str):
            raise ValueError("city must be text")
        city = re.sub(r"\s+", " ", city).strip()
        if not city or len(city) > 40 or not _CITY.match(city) or not _HEBREW.search(city):
            raise ValueError("city must be up to 40 Hebrew letters, spaces, '-' or quotes")
        year = clock.now().year
        return {"city": city, "year": _whole(params.get("year"), "year", year - 1, year + 1)}
    return {}


def params_key(params: Mapping[str, Any]) -> str:
    """The canonical JSON of normalized params (one row per user, topic and key)."""
    return json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# --- Prompts ------------------------------------------------------------------------------------

_SERVICES = {
    "mobile": ("mobile phone plans", "חבילות סלולר"),
    "home_internet": ("home internet plans, fiber or cable, with the internet provider", "אינטרנט ביתי, סיבים"),
    "triple": ("bundles of TV, home internet and home phone", "חבילות טריפל"),
    "mobile_and_internet": ("bundles of mobile lines together with home internet", "חבילות סלולר ואינטרנט"),
}


def search_prompt(topic: str, params: Mapping[str, Any], today: datetime.date) -> str:
    """What the search model is asked: the topic's words and the params, nothing else."""
    month = f"{_MONTHS[today.month - 1]} {today.year}"
    if topic == "telecom":
        english, hebrew = _SERVICES[params["service"]]
        lines = f" for a household with {params['lines']} lines" if params.get("lines") else ""
        return (
            f"Search the web for the {english} ({hebrew}) that providers in Israel offer now, in {month}{lines}. "
            f"The household pays about ₪{params['current_price']} a month today; look for offers that may cost less.\n"
            "List up to ten current offers from different providers, the cheapest first. For each give: the provider, "
            "the plan's name, the regular monthly price in shekels, the promotional monthly price and for how many months "
            "it lasts (if there is a promotion), the main conditions (commitment, data or speed, number of lines, "
            "equipment or joining fees), and the web page where you found it.\n"
            f"Use only offers published on the providers' own sites or on price-comparison sites, current in {month}. "
            "Answer in English and keep provider and plan names as they are written.")
    if topic == "electricity":
        return (
            "Search the web for the plans of the private electricity suppliers in Israel (ספקי חשמל, the open "
            f"electricity market) as of {month}: the discount each supplier and plan gives off the Israel Electric "
            "Corporation's regulated tariff.\n"
            "For each plan give: the supplier, the plan's name, the discount in percent, the hours it applies (all day, "
            "nights, daytime…), the days (every day, weekdays…), whether it needs a smart meter, and the web page where "
            "you found it.\n"
            "List every supplier you find, the biggest discount first. Answer in English.")
    if topic == "fx_card":
        return (
            "Search the web for the foreign-currency fees of the credit cards, digital wallets and bank accounts "
            f"available to people in Israel as of {month}: the conversion fee in percent for paying abroad or online in "
            "a foreign currency (עמלת המרה, עמלת עסקה במט\"ח) and the monthly fee.\n"
            "For each give: the name of the card, wallet or account, whether it is a credit card, a wallet or a bank "
            "account, the conversion fee in percent, the monthly fee in shekels, the main conditions, and the web page "
            "where you found it.\n"
            "List up to ten, the lowest conversion fee first. Answer in English.")
    city, year = params["city"], params["year"]
    return (
        f"Search the web for the arnona (municipal property tax) order of {city} for {year} (צו הארנונה {city} {year}). "
        "Does the municipality give a discount for paying the whole year in advance (הנחה בתשלום מראש, תשלום שנתי) "
        "or for paying by standing order (הוראת קבע)?\n"
        "Give: whether such a discount exists, its percent, the last date to pay to get it, how to get it (for example "
        "paying the whole year by a date, or setting up a standing order), and the web page where you found it — the "
        f"municipality's own site or its published tax order first. If you find no such discount for {year}, say so "
        "plainly. Answer in English.")


_WORDS = {"telecom": "telecom plans in Israel", "electricity": "electricity supplier plans in Israel",
          "fx_card": "foreign-currency fees of cards, wallets and bank accounts in Israel",
          "arnona": "a municipality's arnona discount for paying in advance"}

_RULES = {
    "telecom": (
        "One item per offer. price: the regular monthly price in shekels (after any promotion ends). promo_price and "
        "promo_months: the promotional monthly price and how many months it lasts, both null when there is no "
        "promotion (0 for free months). lines: how many phone lines that price covers (1 when it is per line, null "
        "for home internet or TV). conditions: commitment, data or speed, lines and fees, in a few words."),
    "electricity": (
        "One item per supplier plan. discount_pct: the discount off the regulated tariff, in percent. hours: when it "
        "applies (e.g. \"all day\", \"nights\"). days: e.g. \"every day\", \"Sunday to Thursday\". smart_meter: true "
        "when the plan needs a smart meter."),
    "fx_card": (
        "One item per card, wallet or bank account. kind: card, wallet or bank. fx_fee_pct: the fee in percent on a "
        "payment in a foreign currency. monthly_fee: shekels a month, 0 when there is none. conditions: in a few words."),
    "arnona": (
        "found: true only when the results say {city} gives a discount for paying the {year} arnona in advance or by "
        "standing order. discount_pct: its percent, null if not stated. deadline: the last date to pay for it as "
        "YYYY-MM-DD, null if not stated. method: how to get it in a few words, e.g. \"paying the whole year by "
        "31 January\". source: the page that says so, null when nothing was found."),
}


def extract_prompt(topic: str, params: Mapping[str, Any], text: str, sources: List[Dict[str, Any]]) -> str:
    """What the extraction model is asked: the search's text and its usable pages, numbered as the search numbered them."""
    pages = "\n".join(f"[{n}] {s['title']} — {s['url']}" for n, s in enumerate(sources, 1) if s["ok"])
    rules = _RULES[topic].format(**params) if topic == "arnona" else _RULES[topic]
    return (
        f"These are the results of a web search for {_WORDS[topic]}, and the numbered pages the search used. "
        "A [n] in the results marks a passage that page n supports.\n"
        "Turn them into the schema. Use only what the results say; never guess or fill in a number. Each item's "
        "\"source\" is the number of the page its facts came from (the [n] next to them, else that provider's page "
        "in the list); leave out an item whose page is not in the list. Amounts in shekels, as numbers.\n"
        f"{rules}\n\nResults:\n\"\"\"\n{text[:MAX_SEARCH_CHARS]}\n\"\"\"\n\nPages:\n{pages}")


def _schema_items(properties: Dict[str, Any]) -> Dict[str, Any]:
    item = {"type": "object", "properties": properties, "required": list(properties)}
    return {"type": "object", "properties": {"items": {"type": "array", "items": item}}, "required": ["items"]}


_SOURCE = {"type": "integer", "description": "the number of the page in the list"}
SCHEMAS: Dict[str, Dict[str, Any]] = {
    "telecom": _schema_items({
        "provider": {"type": "string"}, "plan": {"type": "string"}, "price": {"type": "number"},
        "promo_price": llm.nullable({"type": "number"}), "promo_months": llm.nullable({"type": "integer"}),
        "lines": llm.nullable({"type": "integer"}), "conditions": {"type": "string"}, "source": _SOURCE}),
    "electricity": _schema_items({
        "supplier": {"type": "string"}, "plan": {"type": "string"}, "discount_pct": {"type": "number"},
        "hours": {"type": "string"}, "days": {"type": "string"}, "smart_meter": {"type": "boolean"},
        "source": _SOURCE}),
    "fx_card": _schema_items({
        "name": {"type": "string"}, "kind": {"type": "string", "enum": list(FX_KINDS)},
        "fx_fee_pct": {"type": "number"}, "monthly_fee": {"type": "number"}, "conditions": {"type": "string"},
        "source": _SOURCE}),
    "arnona": {"type": "object", "properties": {
        "found": {"type": "boolean"}, "discount_pct": llm.nullable({"type": "number"}),
        "deadline": llm.nullable({"type": "string"}), "method": {"type": "string"},
        "source": llm.nullable(_SOURCE)}, "required": ["found", "discount_pct", "deadline", "method", "source"]},
}


# --- Sources ------------------------------------------------------------------------------------

def _web(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


async def resolve_url(client: httpx.AsyncClient, url: str) -> str:
    """Where a Google grounding redirect leads (a HEAD that follows redirects); any
    other URL as it is, and the redirect itself when the HEAD fails."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.hostname != _REDIRECT_HOST or not parts.path.startswith(_REDIRECT_PATH):
        return url
    try:
        final = str((await client.head(url)).url)
    except Exception as e:
        logger.info(f"Money price check: a grounding link did not resolve ({type(e).__name__})")
        return url
    return final if _web(final) else url


async def resolve_sources(sources: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """The search's sources in its order, each URL resolved, with ``ok``: an http(s) page an offer may cite."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=RESOLVE_TIMEOUT_SECONDS) as client:
        urls = await asyncio.gather(*(resolve_url(client, s["url"]) for s in sources))
    out = []
    for source, url in zip(sources, urls):
        title = str(source.get("title") or "").strip()
        if not title or title == source["url"]:
            title = (urlsplit(url).hostname or url) if _web(url) else url
        out.append({"title": title[:200], "url": url, "ok": _web(url)})
    return out


def _listed(sources: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """The usable sources once each, ``[{title, url}]``."""
    seen: set = set()
    out = []
    for s in sources:
        if s["ok"] and s["url"] not in seen:
            seen.add(s["url"])
            out.append({"title": s["title"], "url": s["url"]})
    return out


# --- Validation -----------------------------------------------------------------------------------

def _words(value: Any, limit: int) -> str:
    """A model's text, one line, at most ``limit`` characters ("" for anything but text)."""
    return re.sub(r"\s+", " ", value).strip()[:limit] if isinstance(value, str) else ""


def _number(value: Any) -> Optional[float]:
    """A model's number (or numeric text such as "₪39.90") as a float; None otherwise."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = re.sub(r"[₪,%\s]", "", value)
        try:
            value = float(value)
        except ValueError:
            return None
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _agorot(value: Any) -> Optional[int]:
    shekels = _number(value)
    return None if shekels is None else int(round(shekels * 100))


def _index(value: Any) -> Optional[int]:
    number = _number(value)
    return int(number) if number is not None and number == int(number) else None


def avg_12(price: int, promo_price: Optional[int], promo_months: Optional[int]) -> int:
    """The average monthly price over the first year (agorot): the promotion's months at its price, the rest at the regular one."""
    if promo_price is None or not promo_months:
        return price
    months = min(promo_months, 12)
    return int(round((promo_price * months + price * (12 - months)) / 12))


def _telecom(raw: Mapping[str, Any], source: Dict[str, str]) -> Optional[Dict[str, Any]]:
    provider, price = _words(raw.get("provider"), 60), _agorot(raw.get("price"))
    if not provider or price is None or not PRICE_MIN <= price <= PRICE_MAX:
        return None
    promo, months = _agorot(raw.get("promo_price")), _index(raw.get("promo_months"))
    if promo is None or months is None or not 0 <= promo < price or not 1 <= months <= PROMO_MONTHS_MAX:
        promo = months = None
    lines = _index(raw.get("lines"))
    return {"provider": provider, "plan": _words(raw.get("plan"), 120), "price": price, "promo_price": promo,
            "promo_months": months, "avg_12": avg_12(price, promo, months),
            "lines": lines if lines is not None and 1 <= lines <= LINES_MAX else None,
            "conditions": _words(raw.get("conditions"), 300), "source": source}


def _electricity(raw: Mapping[str, Any], source: Dict[str, str]) -> Optional[Dict[str, Any]]:
    supplier, discount = _words(raw.get("supplier"), 60), _number(raw.get("discount_pct"))
    if not supplier or discount is None or not 0 < discount <= DISCOUNT_MAX:
        return None
    return {"supplier": supplier, "plan": _words(raw.get("plan"), 120), "discount_pct": round(discount, 2),
            "hours": _words(raw.get("hours"), 80), "days": _words(raw.get("days"), 80),
            "smart_meter": raw.get("smart_meter") is True, "source": source}


def _fx_card(raw: Mapping[str, Any], source: Dict[str, str]) -> Optional[Dict[str, Any]]:
    name, fee, monthly = _words(raw.get("name"), 80), _number(raw.get("fx_fee_pct")), _agorot(raw.get("monthly_fee"))
    if raw.get("monthly_fee") is None:
        monthly = 0
    if (not name or raw.get("kind") not in FX_KINDS or fee is None or not 0 <= fee <= FX_FEE_MAX
            or monthly is None or not 0 <= monthly <= MONTHLY_FEE_MAX):
        return None
    return {"name": name, "kind": raw["kind"], "fx_fee_pct": round(fee, 2), "monthly_fee": monthly,
            "conditions": _words(raw.get("conditions"), 300), "source": source}


_BUILD = {"telecom": _telecom, "electricity": _electricity, "fx_card": _fx_card}
_ORDER = {"telecom": lambda i: (i["avg_12"], i["price"]),
          "electricity": lambda i: (-i["discount_pct"],),
          "fx_card": lambda i: (i["fx_fee_pct"], i["monthly_fee"])}
_SAME = {"telecom": lambda i: (i["provider"].casefold(), i["plan"].casefold()),
         "electricity": lambda i: (i["supplier"].casefold(), i["plan"].casefold(), i["hours"].casefold()),
         "fx_card": lambda i: (i["name"].casefold(),)}


def _cited(value: Any, sources: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """The source an item cites, when it is one of the search's usable pages."""
    n = _index(value)
    if n is None or not 1 <= n <= len(sources) or not sources[n - 1]["ok"]:
        return None
    return {"title": sources[n - 1]["title"], "url": sources[n - 1]["url"]}


def validate(topic: str, data: Mapping[str, Any], sources: List[Dict[str, Any]],
             params: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The extraction's items that cite a grounded page and hold sane numbers, sorted,
    without repeats, at most :data:`MAX_ITEMS` (arnona: exactly one item)."""
    if topic == "arnona":
        return [_arnona(data, sources, params)]
    items = []
    raw_items = data.get("items")
    for raw in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(raw, Mapping):
            continue
        source = _cited(raw.get("source"), sources)
        item = _BUILD[topic](raw, source) if source else None
        if item:
            items.append(item)
    items.sort(key=_ORDER[topic])
    seen: set = set()
    out = []
    for item in items:
        key = _SAME[topic](item)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out[:MAX_ITEMS]


def _arnona(data: Mapping[str, Any], sources: List[Dict[str, Any]], params: Mapping[str, Any]) -> Dict[str, Any]:
    source = _cited(data.get("source"), sources)
    found = data.get("found") is True and source is not None  # an unsourced "found" is not found
    discount = _number(data.get("discount_pct")) if found else None
    if discount is not None and not 0 < discount <= DISCOUNT_MAX:
        discount = None
    deadline = None
    if found and isinstance(data.get("deadline"), str):
        try:
            day = datetime.date.fromisoformat(data["deadline"].strip())
        except ValueError:
            day = None
        if day is not None and params["year"] - 1 <= day.year <= params["year"]:
            deadline = day.isoformat()
    return {"found": found, "discount_pct": None if discount is None else round(discount, 2), "deadline": deadline,
            "method": _words(data.get("method"), 200) if found else "", "source": source}


# --- Storage --------------------------------------------------------------------------------------

def _row(user_id: str, topic: str, key: str) -> Optional[sqlite3.Row]:
    with store.db() as conn:
        return conn.execute("SELECT * FROM money_market_checks WHERE user_id = ? AND topic = ? AND params_key = ?",
                            (user_id, topic, key)).fetchone()


def _payload(row: sqlite3.Row, now: datetime.datetime) -> Dict[str, Any]:
    state, error = row["state"], row["error"]
    started = store.parse(row["started_at"])
    if state == "checking" and (started is None or (now - started).total_seconds() > INTERRUPTED_SECONDS):
        state, error = "failed", ERRORS["interrupted"]
    checked = store.parse(row["checked_at"])
    return {"topic": row["topic"], "params": json.loads(row["params"]), "state": state,
            "started_at": row["started_at"], "checked_at": row["checked_at"],
            "stale": checked is not None and now - checked > datetime.timedelta(days=STALE_DAYS),
            "items": json.loads(row["items"] or "[]"), "sources": json.loads(row["sources"] or "[]"),
            "error": error if state == "failed" else None}


def latest(user: User, topic: str, params: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """The user's newest check of ``topic`` with these params, None when there is none:
    ``{topic, params, state, started_at, checked_at, stale, items, sources, error}``.
    ``items`` / ``sources`` / ``checked_at`` are those of the last check that
    succeeded (kept while a new one runs and when it fails). Raises ValueError for bad params."""
    clean = normalize(topic, params)
    row = _row(user.id, topic, params_key(clean))
    return _payload(row, store.utc_now()) if row is not None else None


def start(user: User, topic: str, params: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    """Start a check for the user in the background (needs the running event loop);
    returns ``{"state": "checking"}``.

    Raises:
        ValueError: unknown topic or bad params.
        PriceCheckUnavailable: no model with web search is configured.
        PriceCheckRunning: this check is already running.
        PriceCheckTooSoon: it ran less than 6 hours ago (5 minutes after a failed one).
    """
    clean = normalize(topic, params)
    if not llm.can_search():
        raise PriceCheckUnavailable("no model with web search is configured")
    key = params_key(clean)
    slot = (user.id, topic, key)
    if slot in _running:
        raise PriceCheckRunning(f"a {topic} check is already running")
    now = store.utc_now()
    row = _row(user.id, topic, key)
    if row is not None:
        current = _payload(row, now)
        if current["state"] == "checking":
            raise PriceCheckRunning(f"a {topic} check is already running")
        started = store.parse(row["started_at"])
        gap = RETRY_SECONDS if current["state"] == "failed" else RATE_SECONDS
        wait = gap - (now - started).total_seconds() if started else 0
        if wait > 0:
            raise PriceCheckTooSoon(math.ceil(wait))
    loop = asyncio.get_running_loop()
    started_at = store.iso(now)
    with store.db() as conn:
        conn.execute(
            "INSERT INTO money_market_checks (id, user_id, topic, params_key, params, state, started_at) "
            "VALUES (?, ?, ?, ?, ?, 'checking', ?) ON CONFLICT (user_id, topic, params_key) DO UPDATE SET "
            "state = 'checking', error = NULL, params = excluded.params, started_at = excluded.started_at",
            (uuid.uuid4().hex, user.id, topic, key, json.dumps(clean, ensure_ascii=False), started_at))
    with users.acting_as(user):
        task = loop.create_task(_check(user, topic, clean, key, started_at))
    _running[slot] = task
    task.add_done_callback(lambda done: _running.pop(slot) if _running.get(slot) is done else None)
    logger.info(f"Money price check ({topic}) of user {user.id} started")
    return {"state": "checking"}


def _finish(user_id: str, topic: str, key: str, started_at: str, **fields: Any) -> None:
    """Write a check's outcome onto its row (unless a newer check replaced it)."""
    columns = ", ".join(f"{name} = ?" for name in fields)
    with store.db() as conn:
        conn.execute(f"UPDATE money_market_checks SET {columns} WHERE user_id = ? AND topic = ? AND params_key = ? "
                     "AND started_at = ?", (*fields.values(), user_id, topic, key, started_at))


async def _find(topic: str, params: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Search, resolve the pages, extract and validate; returns (items, sources)."""
    ref = f"money_market:{topic}"
    found = await llm.search(search_prompt(topic, params, clock.now().date()), purpose=PURPOSE, ref=ref,
                             source="app")
    sources = await resolve_sources(found["sources"])
    if not any(s["ok"] for s in sources):
        raise _NothingFound()
    data = await llm.complete_json(extract_prompt(topic, params, found["text"], sources), SCHEMAS[topic],
                                   job="money", purpose=PURPOSE, ref=ref, source="app", attempts=2)
    items = validate(topic, data, sources, params)
    if not items:
        raise _NothingFound()
    return items, _listed(sources)


async def _check(user: User, topic: str, params: Dict[str, Any], key: str, started_at: str) -> None:
    """One check from search to the stored result; every failure is stored in plain words."""
    error = None
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            items, sources = await _find(topic, params)
    except asyncio.CancelledError:
        _finish(user.id, topic, key, started_at, state="failed", error=ERRORS["stopped"])
        raise
    except TimeoutError:
        error = ERRORS["timeout"]
    except llm.NoSearch:
        error = ERRORS["no_search"]
    except _NothingFound:
        error = ERRORS["nothing"]
    except Exception as e:
        logger.warning(f"Money price check ({topic}) of user {user.id} failed: {type(e).__name__}: {e}")
        error = ERRORS["failed"]
    if error:
        _finish(user.id, topic, key, started_at, state="failed", error=error)
        logger.info(f"Money price check ({topic}) of user {user.id} failed: {error}")
        return
    _finish(user.id, topic, key, started_at, state="ok", error=None, items=json.dumps(items, ensure_ascii=False),
            sources=json.dumps(sources, ensure_ascii=False), model=llm.model("search"),
            checked_at=store.now_iso())
    logger.info(f"Money price check ({topic}) of user {user.id}: {len(items)} items")
