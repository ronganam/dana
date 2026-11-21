"""The banks, card companies, investment houses and meal cards Dana can sign in to (docs/money.md, "Sources").

Each company says what its login form asks for, whether it is a bank or a
card, and how its SMS sign-in code arrives (the phone reads ``sms_senders`` and
``code_regex`` from the code request, so a new bank needs no app update)."""

from dataclasses import dataclass
from typing import Any, Dict, Tuple


@dataclass(frozen=True)
class LoginField:
    """One field of a company's login form."""
    name: str
    label: str
    secret: bool = False


@dataclass(frozen=True)
class Company:
    """A bank or card company: its worker id, display name, kind and login form."""
    id: str
    name: str
    kind: str  # "bank" | "card" | "investment" | "meal"
    fields: Tuple[LoginField, ...]
    sms_senders: Tuple[str, ...] = ()
    code_regex: str = ""

    def payload(self) -> Dict[str, Any]:
        """``{id, name, kind, fields: [{name, label, secret}]}`` for clients."""
        return {"id": self.id, "name": self.name, "kind": self.kind,
                "fields": [{"name": f.name, "label": f.label, "secret": f.secret} for f in self.fields]}


COMPANIES: Dict[str, Company] = {c.id: c for c in (
    Company("hapoalim", "Bank Hapoalim", "bank",
            (LoginField("userCode", "User code"), LoginField("password", "Password", secret=True)),
            sms_senders=("BHapoalim",), code_regex=r"קוד האימות הוא:?\s*(\d{4,8})"),
    Company("visaCal", "Cal", "card",
            (LoginField("username", "Username"), LoginField("password", "Password", secret=True))),
    # Psagot Trade: the SMS comes only on a password sign-in; later syncs use a view-only device key.
    Company("psagot", "Psagot Trade", "investment",
            (LoginField("username", "ID number or user"), LoginField("password", "Password", secret=True)),
            sms_senders=("Psagot",), code_regex=r"פסגות טרייד הוא:?\s*(\d{4,8})"),
    # Pluxee (Cibus), the employer's meal card: a phone number and an SMS code; the session lasts 24 hours.
    Company("pluxee", "Pluxee", "meal", (LoginField("phone", "Mobile number"),),
            sms_senders=("1700701130", "1-700-701-130", "+9721700701130", "Pluxee", "Cibus"),
            code_regex=r"(?<!\d)(\d{6})(?!\d)"),
)}


class UnknownCompany(ValueError):
    """A company id Dana does not support (the API answers 400)."""


def get(company_id: str) -> Company:
    """The company ``company_id``.

    Raises:
        UnknownCompany: not one of :data:`COMPANIES`.
    """
    try:
        return COMPANIES[company_id]
    except KeyError:
        raise UnknownCompany(f"Unknown company {company_id!r} (known: {', '.join(COMPANIES)})") from None


def name_of(company_id: str) -> str:
    """The company's display name ("Bank Hapoalim"); the id itself when unknown."""
    company = COMPANIES.get(company_id)
    return company.name if company else company_id
