"""Investment accounts: storing and reading a portfolio (docs/money.md, "Investment accounts").

A provider returns an investment account (a company of kind ``investment``) in
the ingest format's investment shape (docs/money.md, "Ingest format"):
``details`` (the account's summary: value, cash, today's change, profit…),
``holdings`` (one row per security), ``trades`` (the activity: trades,
deposits, dividends, taxes…, each with its ``ext_key``) and ``values`` (the
account's value per day). :func:`store_account` keeps them for one source, in
the ingest's transaction: the account upserted (``balance`` = its value,
``details`` as given), the trades upserted by key, the holdings replaced (one
without a name takes its latest trade's), today's value and the daily values
upserted. Amounts are agorot. Text and raw rows are scrubbed like
transactions (``ingest.scrub``). An investment account never makes
``money_transactions``."""

import datetime
import json
import logging
import uuid
from typing import Any, Dict, List, Mapping, Optional

from core import clock
from core.users import User
from addons.money import ingest, providers, store

logger = logging.getLogger(__name__)

# A trade's kinds (``trades[].kind`` in the ingest format; "other" for anything else).
KINDS = ("buy", "sell", "dividend", "interest", "deposit", "withdrawal", "tax", "split", "redemption", "benefit",
         "expiry", "other")
# Money in or out of the account (not a gain or a loss): the value chart leaves them out of its change.
FLOW_KINDS = ("deposit", "withdrawal")
HISTORY_DAYS = 400


def _raw(value: Any) -> Optional[str]:
    """A provider's raw row, scrubbed, as stored JSON."""
    return json.dumps(ingest.scrub_value(dict(value)), ensure_ascii=False, sort_keys=True) \
        if isinstance(value, Mapping) else None


def _text(value: Any) -> Optional[str]:
    return ingest.scrub(str(value).strip()) or None if value not in (None, "") else None


def store_account(conn, user: User, source_id: str, company: providers.Company, account: Mapping[str, Any],
                  now: str) -> Optional[str]:
    """Store one investment account of a sync (inside the ingest's transaction); returns its id
    (None for an account without a number). ``account`` is in the ingest format's investment shape:
    ``{accountNumber, details {value, cash, day_change, profit, profit_pct, …}, holdings [...], trades [...],
    values {date: value}}`` (docs/money.md, "Ingest format"; amounts in agorot)."""
    number = str(account.get("accountNumber") or "").strip()
    if not number:
        logger.warning(f"Money ingest for source {source_id}: an investment account without a number was skipped")
        return None
    details = dict(account.get("details") or {}) if isinstance(account.get("details"), Mapping) else {}
    value = details.get("value")
    holdings = [_holding(h) for h in account.get("holdings") or [] if isinstance(h, Mapping) and h.get("security")]
    row = conn.execute("SELECT id FROM money_accounts WHERE user_id = ? AND company = ? AND number = ?",
                       (user.id, company.id, number)).fetchone()
    payload = json.dumps(details, ensure_ascii=False, sort_keys=True)
    if row is None:
        account_id = uuid.uuid4().hex
        conn.execute("INSERT INTO money_accounts (id, user_id, source_id, company, kind, number, balance, balance_at, "
                     "details, created_at, updated_at) VALUES (?, ?, ?, ?, 'investment', ?, ?, ?, ?, ?, ?)",
                     (account_id, user.id, source_id, company.id, number, value, now if value is not None else None,
                      payload, now, now))
    else:
        account_id = row["id"]
        # scoped: the account found by user_id above
        conn.execute("UPDATE money_accounts SET source_id = ?, kind = 'investment', balance = ?, balance_at = ?, "
                     "details = ?, updated_at = ? WHERE id = ?",
                     (source_id, value, now if value is not None else None, payload, now, account_id))
    for t in account.get("trades") or []:
        if not isinstance(t, Mapping) or not t.get("ext_key") or not t.get("date"):
            continue
        kind = t.get("kind") if t.get("kind") in KINDS else "other"
        conn.execute(
            "INSERT INTO money_trades (id, user_id, account_id, ext_key, date, action, kind, security, name, quantity, "
            "price, currency, amount, commission, tax, raw, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(account_id, ext_key) DO UPDATE SET kind = excluded.kind, name = COALESCE(excluded.name, name), "
            "raw = excluded.raw, updated_at = excluded.updated_at",
            (uuid.uuid4().hex, user.id, account_id, str(t["ext_key"]), t["date"], _text(t.get("action")), kind,
             t.get("security"), _text(t.get("name")), t.get("quantity"), t.get("price"), t.get("currency"),
             t.get("amount"), t.get("commission"), t.get("tax"), _raw(t.get("raw")), now, now))
    # A holding the provider did not name takes the name of its latest activity row.
    for h in holdings:
        if not h["name"]:
            r = conn.execute("SELECT name FROM money_trades WHERE account_id = ? AND user_id = ? AND security = ? "
                             "AND name IS NOT NULL ORDER BY date DESC LIMIT 1", (account_id, user.id, h["security"])).fetchone()
            h["name"] = r["name"] if r else None
    conn.execute("DELETE FROM money_holdings WHERE account_id = ? AND user_id = ?", (account_id, user.id))
    for h in holdings:
        conn.execute("INSERT OR REPLACE INTO money_holdings (id, user_id, account_id, security, name, quantity, price, "
                     "currency, value, cost, profit, profit_pct, weight, raw, updated_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (uuid.uuid4().hex, user.id, account_id, h["security"], h["name"], h["quantity"], h["price"],
                      h["currency"], h["value"], h["cost"], h["profit"], h["profit_pct"], h["weight"], h["raw"], now))
    points = {str(d): v for d, v in (account.get("values") or {}).items() if isinstance(v, int)} \
        if isinstance(account.get("values"), Mapping) else {}
    if value is not None:
        points[clock.now().date().isoformat()] = value
    for day, amount in points.items():
        conn.execute("INSERT INTO money_account_values (user_id, account_id, date, value) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(account_id, date) DO UPDATE SET value = excluded.value",
                     (user.id, account_id, day, amount))
    return account_id


def _holding(h: Mapping[str, Any]) -> Dict[str, Any]:
    """A holding of the ingest format as stored (text and raw scrubbed)."""
    return {"security": str(h["security"]), "name": _text(h.get("name")), "quantity": h.get("quantity"),
            "price": h.get("price"), "currency": h.get("currency"), "value": h.get("value"), "cost": h.get("cost"),
            "profit": h.get("profit"), "profit_pct": h.get("profit_pct"), "weight": h.get("weight"),
            "raw": _raw(h.get("raw"))}


# --- Reads ------------------------------------------------------------------------------------

_HOLDING_FIELDS = ("security", "name", "quantity", "price", "currency", "value", "cost", "profit", "profit_pct",
                   "weight")
_TRADE_FIELDS = ("id", "date", "action", "kind", "security", "name", "quantity", "price", "currency", "amount",
                 "commission", "tax")


def _details(account: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        out = json.loads(account.get("details") or "{}")
    except ValueError:
        out = {}
    return out if isinstance(out, dict) else {}


def investment_accounts(user: User) -> List[Dict[str, Any]]:
    """The user's visible investment accounts as the overview lists them: ``{id, label, number, company,
    company_name, value, value_at, cash, day_change, profit, profit_pct}``."""
    out = []
    for a in store.list_accounts(user):
        if a["kind"] != "investment" or a.get("hidden"):
            continue
        d = _details(a)
        out.append({"id": a["id"], "label": store.account_label(a["company"], a["kind"], a["number"], a.get("label")),
                    "number": store.account_payload(a)["number"], "company": a["company"],
                    "company_name": providers.name_of(a["company"]), "value": a.get("balance"),
                    "value_at": a.get("balance_at"), "cash": d.get("cash"), "day_change": d.get("day_change"),
                    "profit": d.get("profit"), "profit_pct": d.get("profit_pct")})
    return out


def portfolio(user: User, days: int = 365, trades: int = 50) -> Dict[str, Any]:
    """Everything the Investments view shows.

    Returns:
        ``{total {value, cash, day_change, profit, cost, as_of}, accounts: [{id, label, number, company_name,
        value, value_at, cash, day_change, profit, profit_pct, holdings: [{security, name, quantity, price,
        currency, value, cost, profit, profit_pct, weight}] (largest first), history: [{date, value}]}],
        history: [{date, value}] (all accounts, days with every account known), trades: [{id, account_id,
        date, action, kind, security, name, quantity, price, currency, amount, commission, tax}] (newest
        first), flows: [{date, amount}] (deposits +, withdrawals −, within ``days``, oldest first),
        allocation: [{name, value, share}] (the largest holdings, the rest as one)}``; amounts in agorot.
    """
    accounts = investment_accounts(user)
    ids = [a["id"] for a in accounts]
    since = (clock.now().date() - datetime.timedelta(days=max(1, min(int(days), HISTORY_DAYS)))).isoformat()
    holdings: Dict[str, List[Dict[str, Any]]] = {i: [] for i in ids}
    series: Dict[str, List[Dict[str, Any]]] = {i: [] for i in ids}
    trade_list: List[Dict[str, Any]] = []
    flows: List[Dict[str, Any]] = []
    if ids:
        marks = ",".join("?" * len(ids))
        with store.db() as conn:
            for r in conn.execute(f"SELECT * FROM money_holdings WHERE user_id = ? AND account_id IN ({marks}) "
                                  "ORDER BY COALESCE(value, 0) DESC, security", (user.id, *ids)):
                holdings[r["account_id"]].append({k: r[k] for k in _HOLDING_FIELDS})
            for r in conn.execute(f"SELECT account_id, date, value FROM money_account_values WHERE user_id = ? "
                                  f"AND account_id IN ({marks}) AND date >= ? ORDER BY date", (user.id, *ids, since)):
                series[r["account_id"]].append({"date": r["date"], "value": r["value"]})
            for r in conn.execute(f"SELECT * FROM money_trades WHERE user_id = ? AND account_id IN ({marks}) "
                                  "ORDER BY date DESC, created_at DESC LIMIT ?", (user.id, *ids, max(1, int(trades)))):
                trade_list.append({"account_id": r["account_id"], **{k: r[k] for k in _TRADE_FIELDS}})
            kinds = ",".join("?" * len(FLOW_KINDS))
            for r in conn.execute(f"SELECT date, amount FROM money_trades WHERE user_id = ? AND account_id IN ({marks}) "
                                  f"AND kind IN ({kinds}) AND amount IS NOT NULL AND date >= ? ORDER BY date",
                                  (user.id, *ids, *FLOW_KINDS, since)):
                flows.append({"date": r["date"], "amount": r["amount"]})
    for a in accounts:
        a["holdings"] = holdings[a["id"]]
        a["history"] = series[a["id"]]
    # The total per day: only days on which every account has a value (no dips where one is missing).
    by_day: Dict[str, Dict[str, int]] = {}
    for a in accounts:
        for p in a["history"]:
            by_day.setdefault(p["date"], {})[a["id"]] = p["value"]
    history = [{"date": d, "value": sum(v.values())} for d, v in sorted(by_day.items()) if len(v) == len(accounts)]

    def total(key: str) -> Optional[int]:
        values = [a.get(key) for a in accounts if a.get(key) is not None]
        return sum(values) if values else None

    every = [h for a in accounts for h in a["holdings"]]
    costs = [h["cost"] for h in every if h.get("cost") is not None]
    value = total("value")
    allocation: List[Dict[str, Any]] = []
    positive = sorted((h for h in every if (h.get("value") or 0) > 0), key=lambda h: -h["value"])
    if value:
        for h in positive[:6]:
            allocation.append({"name": h["name"] or h["security"], "value": h["value"], "share": h["value"] / value})
        rest = sum(h["value"] for h in positive[6:])
        cash = total("cash") or 0
        if rest:
            allocation.append({"name": "Other holdings", "value": rest, "share": rest / value})
        if cash > 0:
            allocation.append({"name": "Cash", "value": cash, "share": cash / value})
    stamps = [a["value_at"] for a in accounts if a.get("value_at")]
    return {"total": {"value": value, "cash": total("cash"), "day_change": total("day_change"), "profit": total("profit"),
                      "cost": sum(costs) if costs else None, "as_of": max(stamps) if stamps else None},
            "accounts": accounts, "history": history, "trades": trade_list, "flows": flows, "allocation": allocation}
