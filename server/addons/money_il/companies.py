"""The Israeli banks, card companies, investment house and meal card money_il reads
(docs/money-il.md, "Companies"), registered with money's providers (docs/money.md, "Providers").

Each says what its login form asks for, its kind, and how its SMS sign-in code
arrives (the phone reads ``sms_senders`` and ``code_regex`` from the code
request, so a new bank needs no app update). Every one syncs through the
bank worker (``sync.py``)."""

from typing import Tuple

from addons.money.providers import Company, LoginField
from addons.money_il import sync
from addons.money_il.config import ADDON

COMPANIES: Tuple[Company, ...] = (
    Company("hapoalim", "Bank Hapoalim", "bank",
            (LoginField("userCode", "User code"), LoginField("password", "Password", secret=True)),
            ADDON, sync.sync, sms_senders=("BHapoalim",), code_regex=r"קוד האימות הוא:?\s*(\d{4,8})",
            hash_key="poalim", forget=sync.forget),
    # Cal's identifier is stable: one purchase's payments share it.
    Company("visaCal", "Cal", "card",
            (LoginField("username", "Username"), LoginField("password", "Password", secret=True)),
            ADDON, sync.sync, id_key="cal", forget=sync.forget),
    # Psagot Trade: the SMS comes only on a password sign-in; later syncs use a view-only device key.
    Company("psagot", "Psagot Trade", "investment",
            (LoginField("username", "ID number or user"), LoginField("password", "Password", secret=True)),
            ADDON, sync.sync, sms_senders=("Psagot",), code_regex=r"פסגות טרייד הוא:?\s*(\d{4,8})",
            forget=sync.forget),
    # Pluxee (Cibus), the employer's meal card: a phone number and an SMS code; the session lasts 24 hours.
    # What the budget did not cover comes as one card line a month ("סיבוס פלאקסי" on Cal).
    Company("pluxee", "Pluxee", "meal", (LoginField("phone", "Mobile number"),),
            ADDON, sync.sync, sms_senders=("1700701130", "1-700-701-130", "+9721700701130", "Pluxee", "Cibus"),
            code_regex=r"(?<!\d)(\d{6})(?!\d)", card_line_words=("סיבוס", "פלאקסי", "pluxee", "cibus"),
            forget=sync.forget),
)
