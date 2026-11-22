"""The money addon's sample data for the demo household (core/demo.py): a
current account and a card with three months of made-up transactions, already
in their categories (nothing is sent to the model)."""

import datetime
import json
import uuid
from typing import List, Tuple

from core.addons import DemoContext
from core.db import get_db_connection
from core.users import User
from addons.money import ingest
from addons.money.addon import addon
from addons.money.store import MANUAL, now_iso

# Monthly: (day of month, description, cents (out negative), category, flow, account)
MONTHLY = (
    (1, "Salary - Example Studio", 520000, "salary", "income", "bank"),
    (2, "Rent - Maple Street", -165000, "housing", "transfer_out", "bank"),
    (5, "City Power & Water", -9840, "utilities", "purchase", "bank"),
    (8, "Mobile & Internet", -6500, "phone_internet", "purchase", "card"),
    (12, "Music streaming", -1199, "subscriptions", "purchase", "card"),
    (15, "Video streaming", -1549, "subscriptions", "purchase", "card"),
    (20, "Gym membership", -4500, "health", "purchase", "card"),
    (10, "Card bill - Example Card", 0, "other", "card_bill", "bank"),
)
# Every week: (weekday, description, cents, category)
WEEKLY = (
    (5, "Green Market", -11850, "groceries"),
    (2, "Corner Bakery", -1420, "groceries"),
    (4, "Bike Shop Café", -2360, "restaurants"),
)
# Once, days ago: (days ago, description, cents, category)
ONCE = (
    (3, "Noodle House", -4880, "restaurants"),
    (9, "City Transit", -2500, "public_transport"),
    (16, "Pet Supplies Co", -6230, "pets"),
    (23, "Bookshop on Main", -3120, "shopping"),
    (34, "Hardware Corner", -8790, "home"),
    (47, "Concert tickets", -12000, "entertainment"),
    (61, "Pharmacy Plus", -2675, "health"),
    (75, "Weekend train trip", -18400, "travel"),
)
DAYS = 90


def _rows(ctx: DemoContext) -> List[Tuple[str, str, int, str, str, str]]:
    """(date, description, cents, category, flow, account) for the last :data:`DAYS` days."""
    today = ctx.now.date()
    out = []
    card_month: dict = {}
    for back in range(DAYS, -1, -1):
        day = today - datetime.timedelta(days=back)
        for weekday, desc, cents, cat in WEEKLY:
            if day.weekday() == weekday:
                out.append((day.isoformat(), desc, cents, cat, "purchase", "card"))
                card_month[day.strftime("%Y-%m")] = card_month.get(day.strftime("%Y-%m"), 0) + cents
        for dom, desc, cents, cat, flow, account in MONTHLY:
            if day.day != dom:
                continue
            if flow == "card_bill":
                prev = (day.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")
                cents = card_month.get(prev, 0)
                if not cents:
                    continue
            out.append((day.isoformat(), desc, cents, cat, flow, account))
            if account == "card":
                card_month[day.strftime("%Y-%m")] = card_month.get(day.strftime("%Y-%m"), 0) + cents
    for back, desc, cents, cat in ONCE:
        out.append(((today - datetime.timedelta(days=back)).isoformat(), desc, cents, cat, "purchase", "card"))
    return out


@addon.demo
def demo_money(user: User, ctx: DemoContext) -> int:
    """The first demo user's account, card and transactions (none for the others)."""
    if ctx.index != 0:
        return 0
    now = now_iso()
    rows = _rows(ctx)
    balance = 1_250_000 + sum(c for _, _, c, _, _, a in rows if a == "bank")
    accounts = {"bank": uuid.uuid4().hex, "card": uuid.uuid4().hex}
    with get_db_connection() as conn:
        for kind, label in (("bank", "Everyday account"), ("card", "Example Card")):
            account_id = accounts[kind]
            conn.execute("INSERT INTO money_accounts (id, user_id, source_id, company, kind, number, label, balance, "
                         "balance_at, created_at, updated_at) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (account_id, user.id, MANUAL, kind, f"{MANUAL}:{account_id}", label,
                          balance if kind == "bank" else None, now if kind == "bank" else None, now, now))
        for i, (day, desc, cents, cat, flow, account) in enumerate(rows):
            conn.execute("INSERT INTO money_transactions (id, user_id, account_id, ext_key, source, status, "
                         "purchase_date, charge_date, amount, description, merchant, merchant_key, category, "
                         "category_source, flow, raw, created_at, updated_at) "
                         "VALUES (?, ?, ?, ?, 'scrape', 'completed', ?, ?, ?, ?, ?, ?, ?, 'rule', ?, ?, ?, ?)",
                         (uuid.uuid4().hex, user.id, accounts[account], f"demo:{i}", day, day, cents, desc, desc,
                          ingest.merchant_key(desc), cat, flow, json.dumps({"demo": True}), now, now))
        conn.commit()
    return len(accounts) + len(rows)
