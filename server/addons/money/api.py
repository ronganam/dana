"""HTTP routes of the money addon: companies, sources, syncs and sign-in code
requests (docs/money.md, "API").

- ``GET /api/money/companies`` (those of the user's provider addons)
- ``GET|POST /api/money/sources``, ``PATCH|DELETE /api/money/sources/{id}``
  (``?transactions=1`` also deletes its accounts and transactions)
- ``POST /api/money/sources/{id}/sync`` → 202 ``{sync_id}`` | 429 ``too_soon`` | 409 ``needs_attention``
- ``GET /api/money/syncs?source=&limit=``
- ``GET /api/money/code-requests?status=``, ``GET|POST /api/money/code-requests/{id}``
- ``POST /api/money/signals``: the card / bank apps' payment pushes from the phone
- The page: ``GET /api/money/overview|pace?month=``, ``GET /api/money/categories?period=|start=&end=``,
  ``GET /api/money/months?n=``, ``GET /api/money/transactions?…``, ``GET|PATCH /api/money/transactions/{id}``,
  ``GET /api/money/recurring``, ``GET /api/money/changes?since=``
- The dashboards (dashboard.py): ``GET /api/money/cashflow?n=&end=``, ``GET /api/money/balance?days=``,
  ``GET /api/money/trends|insights?month=``, ``GET /api/money/merchants?period=|start=&end=``
- Investments (portfolio.py): ``GET /api/money/portfolio?days=&trades=``; meal cards (meals.py): ``GET /api/money/meals?months=``
- Savings (savings.py): ``GET /api/money/savings``, ``GET|PATCH /api/money/savings/{key}``,
  ``POST /api/money/savings/{key}/check`` (keys contain ":"; clients URL-encode them)

Every route answers for the request's user, who must have ``money``
connected (else 403 ``not_connected`` from the middleware). Another user's
ids answer 404, like missing ones. Credentials are accepted, never returned.
"""

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

from aiohttp import web

from core import undo, users
from core.auth import request_user
from core.users import User
from core.addons import crypto, store as connections
from addons.money import (categories, config, dashboard, insights, meals, otp, periods, portfolio, providers, queries,
                          rules, savings, schedule, signals, sources, store)

logger = logging.getLogger(__name__)


def _money_user(request: web.Request) -> User:
    """The request's user, who must have ``money`` connected (else the middleware answers 403)."""
    user = request_user(request)
    connections.require(user, config.ADDON)
    return user


def _error(status: int, error: str, **extra: Any) -> web.Response:
    return web.json_response({"error": error, **extra}, status=status)


async def _body(request: web.Request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(text='{"error": "invalid", "message": "the body must be JSON"}',
                                 content_type="application/json") from None
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text='{"error": "invalid", "message": "the body must be an object"}',
                                 content_type="application/json")
    return body


def _source_or_404(user: User, request: web.Request) -> Dict[str, Any]:
    source = store.get_source(user, request.match_info["id"])
    if source is None:
        raise web.HTTPNotFound(text='{"error": "not_found"}', content_type="application/json")
    return source


async def get_companies(request: web.Request) -> web.Response:
    """GET /api/money/companies → ``{companies: [{id, name, kind, fields: [{name, label, secret}]}]}``: those of
    the provider addons the user has on (``providers.companies``)."""
    user = _money_user(request)
    return web.json_response({"companies": [c.payload() for c in providers.companies(user)]})


async def get_sources(request: web.Request) -> web.Response:
    """GET /api/money/sources → ``{sources: [payload]}``"""
    user = _money_user(request)
    return web.json_response({"sources": [store.source_payload(user, s) for s in store.list_sources(user)]})


async def post_source(request: web.Request) -> web.Response:
    """POST /api/money/sources ``{company, label?, credentials}`` → 201 payload (its first sync starts); 400
    ``unknown_company`` when no provider brings it, 403 ``not_connected`` when its provider addon is off."""
    user = _money_user(request)
    body = await _body(request)
    try:
        payload = sources.add(user, str(body.get("company") or ""), body.get("credentials"), body.get("label"))
    except providers.UnknownCompany as e:
        return _error(400, "unknown_company", message=str(e))
    except sources.InvalidCredentials as e:
        return _error(400, "invalid", field=e.field, message=str(e))
    except crypto.CredentialsKeyError:
        logger.error("Money: a source can't be stored: CREDENTIALS_KEY is missing or wrong")
        return _error(503, "credentials_key", message="The server can't store login details right now.")
    return web.json_response(payload, status=201)


async def patch_source(request: web.Request) -> web.Response:
    """PATCH /api/money/sources/{id} ``{label?, credentials?}`` → payload."""
    user = _money_user(request)
    _source_or_404(user, request)
    body = await _body(request)
    label = body.get("label")
    if label is not None and not isinstance(label, str):
        return _error(400, "invalid", field="label", message="label must be text")
    try:
        payload = sources.update(user, request.match_info["id"], label=label, credentials=body.get("credentials"))
    except providers.UnknownCompany as e:
        return _error(400, "unknown_company", message=str(e))
    except LookupError:
        return _error(404, "not_found")
    except sources.InvalidCredentials as e:
        return _error(400, "invalid", field=e.field, message=str(e))
    except crypto.CredentialsKeyError:
        logger.error("Money: a source can't be updated: CREDENTIALS_KEY is missing or wrong")
        return _error(503, "credentials_key", message="The server can't store login details right now.")
    return web.json_response(payload)


async def delete_source(request: web.Request) -> web.Response:
    """DELETE /api/money/sources/{id}?transactions=1 → ``{ok: true}``."""
    user = _money_user(request)
    with_transactions = request.query.get("transactions", "") in ("1", "true", "yes")
    if not await sources.remove(user, request.match_info["id"], with_transactions):
        return _error(404, "not_found")
    return web.json_response({"ok": True})


async def post_sync(request: web.Request) -> web.Response:
    """POST /api/money/sources/{id}/sync → 202 ``{sync_id}`` (403 ``not_connected`` while its provider addon is off;
    409 ``unavailable`` when no provider reads its company any more)."""
    user = _money_user(request)
    _source_or_404(user, request)
    try:
        sync_id = schedule.sync_now(user, request.match_info["id"], "manual")
    except providers.UnknownCompany as e:
        return _error(409, "unavailable", reason=str(e))
    except LookupError:
        return _error(404, "not_found")
    except schedule.TooSoon as e:
        return _error(429, "too_soon", retry_after=e.retry_after)
    except schedule.NeedsAttention as e:
        return _error(409, "needs_attention", reason=e.reason)
    return web.json_response({"sync_id": sync_id}, status=202)


async def get_syncs(request: web.Request) -> web.Response:
    """GET /api/money/syncs?source=&limit= → ``{syncs: [...]}`` (newest first)."""
    user = _money_user(request)
    try:
        limit = int(request.query.get("limit") or 20)
    except ValueError:
        return _error(400, "invalid", field="limit", message="limit must be a number")
    return web.json_response({"syncs": store.list_syncs(user, request.query.get("source") or None, limit)})


def _request_payload(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": r["id"], "source_id": r["source_id"], "company": r["company"],
            "company_name": providers.name_of(r["company"]), "status": r["status"], "expires_at": r["expires_at"]}


async def get_code_requests(request: web.Request) -> web.Response:
    """GET /api/money/code-requests?status=pending → ``{requests: [...]}``."""
    user = _money_user(request)
    status = request.query.get("status") or None
    return web.json_response({"requests": [_request_payload(r) for r in store.code_requests(user, status=status)]})


async def get_code_request(request: web.Request) -> web.Response:
    """GET /api/money/code-requests/{id} → ``{id, status, company, company_name, senders, code_regex, expires_at}``
    (the phone reads ``senders`` and ``code_regex``)."""
    user = _money_user(request)
    r = store.get_code_request(user, request.match_info["id"])
    if r is None:
        return _error(404, "not_found")
    company = providers.company(r["company"])
    return web.json_response({"id": r["id"], "status": r["status"], "company": r["company"],
                              "company_name": providers.name_of(r["company"]),
                              "senders": list(company.sms_senders) if company else [],
                              "code_regex": company.code_regex if company else "", "expires_at": r["expires_at"]})


async def post_code_request(request: web.Request) -> web.Response:
    """POST /api/money/code-requests/{id} ``{code, via?: "phone"}`` → ``{ok: true}`` | 400 ``bad_code`` | 404 | 409 ``not_pending``."""
    user = _money_user(request)
    request_id = request.match_info["id"]
    if store.get_code_request(user, request_id) is None:
        return _error(404, "not_found")
    body = await _body(request)
    code = body.get("code")
    if not isinstance(code, str) or not otp.CODE_RE.match(code.strip()):
        return _error(400, "bad_code", message="the code is 4 to 8 digits")
    via = "phone" if body.get("via") == "phone" else "web"
    if not otp.submit(user, request_id, code, via):
        return _error(409, "not_pending", message="this sign-in isn't waiting for a code any more")
    return web.json_response({"ok": True})


# --- Payment signals from the phone (docs/money.md, "Instant updates from the phone") --------

async def post_signals(request: web.Request) -> web.Response:
    """POST /api/money/signals ``{notifications: [{key, package, app_name, title, text, posted_at}]}``
    → ``{accepted, duplicates}`` (``accepted``: newly stored; a push already stored with the same key
    and text is a duplicate)."""
    user = _money_user(request)
    body = await _body(request)
    items = body.get("notifications")
    if not isinstance(items, list):
        return _error(400, "invalid", field="notifications", message="notifications must be a list")
    result = await asyncio.to_thread(signals.record_push, user, items)
    return web.json_response({"accepted": result["accepted"], "duplicates": result["duplicates"]})


# --- The Money page: numbers, transactions, recurring charges, changes (queries.py, insights.py) ---

def _bad(field: str, message: str) -> web.HTTPBadRequest:
    return web.HTTPBadRequest(text=json.dumps({"error": "invalid", "field": field, "message": message}),
                              content_type="application/json")


def _period_arg(request: web.Request, required: bool = True) -> Optional[periods.Period]:
    """``?period=`` or ``?start=&end=`` (``end`` inclusive); None when neither is given and not ``required``."""
    q = request.query
    if not required and not q.get("period") and not q.get("start"):
        return None
    try:
        return periods.resolve(q.get("period") or None, start=q.get("start") or None, end=q.get("end") or None)
    except ValueError as e:
        raise _bad("period", str(e)) from None


def _number_arg(request: web.Request, name: str) -> Optional[float]:
    value = request.query.get(name)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        raise _bad(name, f"{name} must be a number") from None


def _month_arg(request: web.Request, name: str = "month") -> Optional[str]:
    """``?month=YYYY-MM`` (None when not given)."""
    month = request.query.get(name) or None
    if month is not None and not (re.fullmatch(r"\d{4}-\d{2}", month) and 1 <= int(month[5:]) <= 12):
        raise _bad(name, f"{name} must look like YYYY-MM")
    return month


async def get_overview(request: web.Request) -> web.Response:
    """GET /api/money/overview?month=YYYY-MM → the top of the page (``queries.overview``) + ``unusual`` and
    the dashboard tiles: ``everyday {total, typical, on_track}``, ``net``, ``set_aside``, ``fixed_monthly``
    (recurring charges + yearly ones a month), ``yearly_monthly``, ``forecast {soon_low, low_point,
    next: [3 events]} | None`` (this month only)."""
    user = _money_user(request)
    try:
        payload = queries.overview(user, request.query.get("month") or None)
    except ValueError as e:
        return _error(400, "invalid", field="month", message=str(e))
    p = payload["period"]
    payload["unusual"] = [u for u in insights.unusual(user, p["start"]) if u["transaction"]["purchase_date"] <= p["end"]]
    payload.update(await asyncio.to_thread(dashboard.tiles, user, payload["month"]))
    return web.json_response(payload)


async def get_pace(request: web.Request) -> web.Response:
    """GET /api/money/pace?month=YYYY-MM → everyday spending day by day against typical (``dashboard.everyday_pace``)."""
    user = _money_user(request)
    month = _month_arg(request)
    return web.json_response(await asyncio.to_thread(lambda: dashboard.everyday_pace(dashboard.load(user), month)))


async def get_cashflow(request: web.Request) -> web.Response:
    """GET /api/money/cashflow?n=12&end=YYYY-MM → money in and out per month (``dashboard.cashflow``)."""
    user = _money_user(request)
    n = _number_arg(request, "n")
    end = _month_arg(request, "end")
    count = int(n) if n is not None else 12
    return web.json_response(await asyncio.to_thread(lambda: dashboard.cashflow(dashboard.load(user), count, end)))


async def get_balance(request: web.Request) -> web.Response:
    """GET /api/money/balance?days=180 → ``{history, forecast}`` (``dashboard.balance``)."""
    user = _money_user(request)
    days = _number_arg(request, "days")
    return web.json_response(await asyncio.to_thread(dashboard.balance, user,
                                                     int(days) if days is not None else dashboard.FORECAST_DAYS))


async def get_trends(request: web.Request) -> web.Response:
    """GET /api/money/trends?month=YYYY-MM → spending by group and category against typical (``dashboard.trends``)."""
    user = _money_user(request)
    month = _month_arg(request)
    return web.json_response(await asyncio.to_thread(lambda: dashboard.trends(dashboard.load(user), month)))


async def get_merchants(request: web.Request) -> web.Response:
    """GET /api/money/merchants?period=|start=&end=&limit= → the top merchants (``dashboard.merchants``)."""
    user = _money_user(request)
    p = _period_arg(request)
    limit = _number_arg(request, "limit")
    top = max(1, min(int(limit), 50)) if limit is not None else 15
    items = await asyncio.to_thread(lambda: dashboard.merchants(dashboard.load(user), p.start, p.end_exclusive, top))
    return web.json_response({"period": p.payload(), "merchants": items})


async def get_insights(request: web.Request) -> web.Response:
    """GET /api/money/insights?month=YYYY-MM → ``{month, items}`` (``dashboard.month_insights``)."""
    user = _money_user(request)
    month = _month_arg(request)
    return web.json_response(await asyncio.to_thread(dashboard.month_insights, user, month))


async def get_portfolio(request: web.Request) -> web.Response:
    """GET /api/money/portfolio?days=365&trades=50 → the investment accounts, holdings, daily values and
    activity (``portfolio.portfolio``)."""
    user = _money_user(request)
    days = _number_arg(request, "days")
    trades = _number_arg(request, "trades")
    return web.json_response(await asyncio.to_thread(
        portfolio.portfolio, user, int(days) if days is not None else 365,
        max(1, min(int(trades), 500)) if trades is not None else 50))


async def get_meals(request: web.Request) -> web.Response:
    """GET /api/money/meals?months=12 → the meal card's budget now, its cycles, meals and places (``meals.meals``)."""
    user = _money_user(request)
    months = _number_arg(request, "months")
    return web.json_response(await asyncio.to_thread(
        meals.meals, user, max(1, min(int(months), 24)) if months is not None else 12))


async def get_categories(request: web.Request) -> web.Response:
    """GET /api/money/categories?period=|start=&end= → groups with their categories + ``taxonomy``."""
    user = _money_user(request)
    return web.json_response({**queries.category_tree(user, _period_arg(request)), "taxonomy": queries.taxonomy()})


async def get_months(request: web.Request) -> web.Response:
    """GET /api/money/months?n=12&end=YYYY-MM → spending per month stacked by group (``queries.months``)."""
    user = _money_user(request)
    n = _number_arg(request, "n")
    end = _month_arg(request, "end")
    return web.json_response(queries.months(user, int(n) if n is not None else 12, end))


async def get_transactions(request: web.Request) -> web.Response:
    """GET /api/money/transactions?period=|start=&end=&q=&category=&account=&min=&max=&cursor=&limit=
    → ``{transactions, next_cursor}`` (newest first; each with its ``kind``)."""
    user = _money_user(request)
    q = request.query
    limit = _number_arg(request, "limit")
    try:
        payload = queries.search(user, query=q.get("q") or None, period=_period_arg(request, required=False),
                                 category=q.get("category") or None, account_id=q.get("account") or None,
                                 min_amount=_number_arg(request, "min"), max_amount=_number_arg(request, "max"),
                                 limit=int(limit) if limit is not None else 50, cursor=q.get("cursor") or None)
    except queries.NotFound:
        return _error(404, "not_found")
    except ValueError as e:
        return _error(400, "invalid", message=str(e))
    dashboard.annotate(user, payload["transactions"])
    return web.json_response(payload)


async def get_transaction(request: web.Request) -> web.Response:
    """GET /api/money/transactions/{id} → the transaction in full (``queries.transaction``) with its
    ``kind`` (``dashboard.spend_kinds``)."""
    user = _money_user(request)
    try:
        return web.json_response(dashboard.with_kind(user, queries.transaction(user, request.match_info["id"])))
    except queries.NotFound:
        return _error(404, "not_found")


async def patch_transaction(request: web.Request) -> web.Response:
    """PATCH /api/money/transactions/{id} ``{category?, for_merchant?, note?, flow?, one_off?, yearly?}``
    → ``{transaction, changed, undo_id}`` (one Undo for everything the call changed). ``flow``: what
    it counts as (``rules.USER_FLOWS``); ``one_off`` / ``yearly``: true, false or null (Dana decides)."""
    user = _money_user(request)
    txn_id = request.match_info["id"]
    try:
        queries.transaction(user, txn_id)
    except queries.NotFound:
        return _error(404, "not_found")
    body = await _body(request)
    category, note = body.get("category"), body.get("note")
    if category is not None and (not isinstance(category, str) or not categories.is_category(category)):
        return _error(400, "invalid", field="category", message="category must be a category key")
    if note is not None and not isinstance(note, str):
        return _error(400, "invalid", field="note", message="note must be text")
    flow = body.get("flow")
    if flow is not None and flow not in rules.USER_FLOWS:
        return _error(400, "invalid", field="flow", message="flow must be one of " + ", ".join(rules.USER_FLOWS))
    marks = {m: body[m] for m in ("one_off", "yearly") if m in body}
    for m, v in marks.items():
        if v is not None and not isinstance(v, bool):
            return _error(400, "invalid", field=m, message=f"{m} must be true, false or null")
    if category is None and "note" not in body and flow is None and not marks:
        return _error(400, "invalid", message="nothing to change: give category, note, flow, one_off or yearly")
    undo_ids: List[str] = []
    changed = 0
    try:
        if category is not None:
            result = rules.set_category(user, txn_id, category, for_merchant=body.get("for_merchant") is not False)
            changed, undo_ids = result["changed"], [result["undo_id"]]
        if "note" in body:
            undo_ids.append(rules.set_note(user, txn_id, note or "")["undo_id"])
        if flow is not None:
            undo_ids.append(rules.set_flow(user, txn_id, flow)["undo_id"])
        for m, v in marks.items():
            result = rules.set_mark(user, txn_id, m, v)
            changed = max(changed, result["changed"])
            undo_ids.append(result["undo_id"])
    except LookupError:
        return _error(404, "not_found")
    undo_id = undo_ids[0] if len(undo_ids) == 1 else None
    if len(undo_ids) > 1:
        async def undo_both() -> None:
            for uid in reversed(undo_ids):
                await undo.run(uid)
        with users.acting_as(user):
            undo_id = undo.record("Changed the transaction", undo_both, source="money.patch")
    return web.json_response({"transaction": dashboard.with_kind(user, queries.transaction(user, txn_id)),
                              "changed": changed, "undo_id": undo_id})


async def get_recurring(request: web.Request) -> web.Response:
    """GET /api/money/recurring → ``{items, income, monthly_total, yearly, yearly_monthly}``
    (``insights.recurring`` and ``insights.yearly``)."""
    user = _money_user(request)
    rec = insights.recurring(user)
    yearly = insights.yearly(user, rec=rec)
    return web.json_response({**rec, "yearly": yearly, "yearly_monthly": sum(y["monthly_cost"] for y in yearly)})


async def get_changes(request: web.Request) -> web.Response:
    """GET /api/money/changes?since=<cursor> → ``{changed, cursor}`` (cheap; the page polls it every 30 s)."""
    user = _money_user(request)
    return web.json_response(queries.changes(user, request.query.get("since") or None))


# --- Savings (savings.py; docs/money.md, "Savings") -----------------------------------------------

async def get_savings(request: web.Request) -> web.Response:
    """GET /api/money/savings → ``{as_of, rate, totals, items}`` (``savings.overview``)."""
    user = _money_user(request)
    return web.json_response(await asyncio.to_thread(savings.overview, user))


async def get_saving(request: web.Request) -> web.Response:
    """GET /api/money/savings/{key} → one item; 404 when the user has no such item."""
    user = _money_user(request)
    item = await asyncio.to_thread(savings.get_item, user, request.match_info["key"])
    if item is None:
        return _error(404, "not_found")
    return web.json_response(item)


async def patch_saving(request: web.Request) -> web.Response:
    """PATCH /api/money/savings/{key} ``{status?, answers?: {id: value}, snooze_days?, target?: agorot | null,
    subitem?: {key, choice: keep | cancel | null}}`` → ``{item, undo_id}`` (one Undo for the call);
    400 ``invalid`` ``{field, message}``."""
    user = _money_user(request)
    key = request.match_info["key"]
    body = await _body(request)
    status = body.get("status")
    if status is not None and not isinstance(status, str):
        return _error(400, "invalid", field="status", message="status must be text")
    kwargs: Dict[str, Any] = {"status": status, "answers": body.get("answers"), "snooze_days": body.get("snooze_days"),
                              "subitem": body.get("subitem")}
    if "target" in body:
        kwargs["target"] = body["target"]
    try:
        result = await asyncio.to_thread(lambda: savings.update(user, key, **kwargs))
    except savings.NotFound:
        return _error(404, "not_found")
    except savings.Invalid as e:
        return _error(400, "invalid", field=e.field, message=str(e))
    return web.json_response(result)


async def post_saving_check(request: web.Request) -> web.Response:
    """POST /api/money/savings/{key}/check → 202 ``{state: "checking"}`` (the user's price checks); 400
    ``needs_answer`` ``{question}`` | 400 ``no_check`` | 409 ``running`` | 429 ``too_soon`` ``{retry_after}`` |
    503 ``unavailable``."""
    user = _money_user(request)
    key = request.match_info["key"]
    try:
        topic, params = await asyncio.to_thread(savings.check_request, user, key)
    except savings.NotFound:
        return _error(404, "not_found")
    except savings.NoCheck:
        return _error(400, "no_check", message="this item has no price check")
    except savings.NeedsAnswer as e:
        return _error(400, "needs_answer", question=e.question, message=str(e))
    try:
        # On the event loop: the price checks start the check as a task.
        result = savings.start_check(user, topic, params)
    except ValueError as e:
        return _error(400, "invalid", message=str(e))
    except providers.PriceCheckRunning:
        return _error(409, "running", message="this price check is already running")
    except providers.PriceCheckTooSoon as e:
        return _error(429, "too_soon", retry_after=e.retry_after)
    except providers.PriceCheckUnavailable:
        return _error(503, "unavailable", message="price checks are not available")
    return web.json_response({**result, "state": result.get("state") or "checking"}, status=202)


def setup(app: web.Application) -> None:
    """Register the money routes."""
    app.router.add_get("/api/money/companies", get_companies)
    app.router.add_get("/api/money/sources", get_sources)
    app.router.add_post("/api/money/sources", post_source)
    app.router.add_patch("/api/money/sources/{id}", patch_source)
    app.router.add_delete("/api/money/sources/{id}", delete_source)
    app.router.add_post("/api/money/sources/{id}/sync", post_sync)
    app.router.add_get("/api/money/syncs", get_syncs)
    app.router.add_get("/api/money/code-requests", get_code_requests)
    app.router.add_get("/api/money/code-requests/{id}", get_code_request)
    app.router.add_post("/api/money/code-requests/{id}", post_code_request)
    app.router.add_post("/api/money/signals", post_signals)
    app.router.add_get("/api/money/overview", get_overview)
    app.router.add_get("/api/money/pace", get_pace)
    app.router.add_get("/api/money/categories", get_categories)
    app.router.add_get("/api/money/months", get_months)
    app.router.add_get("/api/money/transactions", get_transactions)
    app.router.add_get("/api/money/transactions/{id}", get_transaction)
    app.router.add_patch("/api/money/transactions/{id}", patch_transaction)
    app.router.add_get("/api/money/recurring", get_recurring)
    app.router.add_get("/api/money/cashflow", get_cashflow)
    app.router.add_get("/api/money/balance", get_balance)
    app.router.add_get("/api/money/trends", get_trends)
    app.router.add_get("/api/money/merchants", get_merchants)
    app.router.add_get("/api/money/insights", get_insights)
    app.router.add_get("/api/money/portfolio", get_portfolio)
    app.router.add_get("/api/money/meals", get_meals)
    app.router.add_get("/api/money/changes", get_changes)
    app.router.add_get("/api/money/savings", get_savings)
    app.router.add_get("/api/money/savings/{key}", get_saving)
    app.router.add_patch("/api/money/savings/{key}", patch_saving)
    app.router.add_post("/api/money/savings/{key}/check", post_saving_check)
