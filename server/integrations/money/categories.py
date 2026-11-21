"""Dana's spending categories and money flows (docs/money.md, "Categories").

Two levels: a group ("food") holds categories ("groceries", "restaurants",
"delivery"); a filter or a question may name either (:func:`resolve`). A
transaction's ``flow`` says what kind of money movement it is; only
:data:`SPENDING_FLOWS` count as spending (a card bill on the bank account,
moves between the user's own accounts, investments and income never do;
refunds are negative and reduce spending)."""

import re
from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional, Set, Tuple


@dataclass(frozen=True)
class Group:
    key: str
    label: str
    label_he: str
    income: bool = False


@dataclass(frozen=True)
class Category:
    key: str
    group: str
    label: str
    label_he: str


GROUPS: Tuple[Group, ...] = (
    Group("food", "Food", "אוכל"),
    Group("housing", "Housing", "דיור ובית"),
    Group("transport", "Transport", "תחבורה"),
    Group("bills", "Bills", "חשבונות"),
    Group("health", "Health", "בריאות"),
    Group("shopping", "Shopping", "קניות"),
    Group("leisure", "Leisure", "פנאי"),
    Group("family", "Family", "משפחה"),
    Group("people", "People", "אנשים"),
    Group("finance", "Finance", "פיננסים"),
    Group("other", "Other", "אחר"),
    Group("income", "Income", "הכנסות", income=True),
)

_CATEGORY_LIST: Tuple[Category, ...] = (
    Category("groceries", "food", "Groceries", "מכולת וסופר"),
    Category("restaurants", "food", "Restaurants & cafés", "מסעדות ובתי קפה"),
    Category("delivery", "food", "Delivery", "משלוחי אוכל"),
    Category("housing", "housing", "Rent & mortgage", "שכירות ומשכנתא"),
    Category("utilities", "housing", "Utilities & municipality", "חשמל, מים, גז וארנונה"),
    Category("home", "housing", "Home & furniture", "בית וריהוט"),
    Category("fuel", "transport", "Fuel", "דלק"),
    Category("parking", "transport", "Parking & tolls", "חניה וכבישי אגרה"),
    Category("public_transport", "transport", "Public transport", "תחבורה ציבורית"),
    Category("car", "transport", "Car", "רכב"),
    Category("phone_internet", "bills", "Phone & internet", "טלפון ואינטרנט"),
    Category("subscriptions", "bills", "Subscriptions & software", "מנויים ותוכנה"),
    Category("insurance", "bills", "Insurance", "ביטוח"),
    Category("health", "health", "Health", "בריאות"),
    Category("clothing", "shopping", "Clothing", "ביגוד והנעלה"),
    Category("electronics", "shopping", "Electronics", "אלקטרוניקה"),
    Category("shopping", "shopping", "General shopping", "קניות כלליות"),
    Category("entertainment", "leisure", "Entertainment", "בילויים"),
    Category("travel", "leisure", "Travel & hotels", "חופשות ונסיעות"),
    Category("education", "family", "Education", "חינוך"),
    Category("kids", "family", "Kids", "ילדים"),
    Category("pets", "family", "Pets", "חיות מחמד"),
    Category("transfers", "people", "Transfers to people", "העברות לאנשים"),
    Category("gifts_donations", "people", "Gifts & donations", "מתנות ותרומות"),
    Category("fees", "finance", "Fees", "עמלות"),
    Category("cash", "finance", "Cash", "מזומן"),
    Category("taxes", "finance", "Taxes", "מיסים"),
    Category("investments", "finance", "Investments & savings", "השקעות וחיסכון"),
    Category("other", "other", "Other", "אחר"),
    Category("salary", "income", "Salary", "משכורת"),
    Category("refunds", "income", "Refunds", "החזרים"),
    Category("benefits", "income", "Benefits", "קצבאות"),
    Category("interest", "income", "Interest", "ריבית"),
    Category("transfers_in", "income", "Transfers in", "העברות נכנסות"),
    Category("other_income", "income", "Other income", "הכנסה אחרת"),
)

CATEGORIES: Dict[str, Category] = {c.key: c for c in _CATEGORY_LIST}
GROUP_KEYS: Dict[str, Group] = {g.key: g for g in GROUPS}

FLOWS: Tuple[str, ...] = ("purchase", "refund", "transfer_out", "transfer_in", "income", "card_bill",
                          "own_transfer", "fee", "cash", "loan", "investment", "other")
# What counts as spending: refunds are negative amounts and reduce it.
SPENDING_FLOWS: FrozenSet[str] = frozenset({"purchase", "refund", "transfer_out", "fee", "cash", "loan"})
# What counts as income.
INCOME_FLOWS: FrozenSet[str] = frozenset({"income", "transfer_in"})


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().casefold())


def in_group(group: str) -> Set[str]:
    """The category keys of a group key (empty for an unknown one)."""
    return {c.key for c in _CATEGORY_LIST if c.group == group}


def is_category(key: str) -> bool:
    return key in CATEGORIES


def is_income(key: Optional[str]) -> bool:
    """Whether a category key is an income one (salary, refunds, …)."""
    c = CATEGORIES.get(key or "")
    return bool(c) and GROUP_KEYS[c.group].income


def group_of(category: str) -> str:
    """The group key of a category key ("" for an unknown one)."""
    c = CATEGORIES.get(category)
    return c.group if c else ""


def resolve(term: str) -> Set[str]:
    """The category keys a word names: a group (its categories), a category,
    or either's English or Hebrew label, case-insensitive ("food" →
    groceries, restaurants, delivery; "מסעדות ובתי קפה" → restaurants).
    A group wins over a category of the same key. Empty for an unknown word."""
    t = _norm(term)
    if not t:
        return set()
    for g in GROUPS:
        if t in (g.key, _norm(g.label), _norm(g.label_he)):
            return in_group(g.key)
    for c in _CATEGORY_LIST:
        if t in (c.key, _norm(c.label), _norm(c.label_he)):
            return {c.key}
    return set()
