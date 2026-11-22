"""Shared by money's tests: a made-up provider addon, ``money_sample`` (not a test script itself).

It brings four companies through money's extension point (docs/money.md,
"Providers"), each syncing whatever a test sets in :data:`RESULTS` for the
company: ``samplebank`` (a bank: rows keyed by a hash, a transfer note
"לטובת: <payee>. עבור: <purpose>.", activity codes), ``samplecard`` (a card
company: stable ids, its own categories), ``sampleinvest`` and ``samplemeal``;
plus statement conventions (made-up ID numbers that pass a checksum masked,
the card companies a bank line pays, a payment app) and reference rates.
Every name, number and rule is invented for the tests.

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import moneyfakes                     # after the database is migrated
    connections.connect(user, moneyfakes.PROVIDER)
"""

import datetime
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core.addons import Addon, Need
from core.users import User
from addons.money import ingest, providers, savings, sources
from addons.money.providers import Company, LoginField, PushWords, Redaction, Statements, SyncJob, SyncResult

PROVIDER = "money_sample"
BANK, CARD, INVEST, MEAL = "samplebank", "samplecard", "sampleinvest", "samplemeal"
NAMES = {BANK: "Sample Bank", CARD: "Sample Card", INVEST: "Sample Invest", MEAL: "Sample Meals"}

# What the next sync of each company returns (accounts in money's ingest format).
RESULTS: Dict[str, List[Dict[str, Any]]] = {BANK: [], CARD: [], INVEST: [], MEAL: []}
JOBS: List[SyncJob] = []


async def _sync(job: SyncJob) -> SyncResult:
    JOBS.append(job)
    return SyncResult(accounts=[dict(a) for a in RESULTS.get(job.company, [])])


_NOTE = re.compile(r"^\s*לטובת:\s*(?P<payee>.+?)\s*(?:\.\s*עבור:\s*(?P<purpose>.*?))?\s*\.?\s*$", re.S)


def transfer_note(memo: str) -> Optional[Tuple[str, str]]:
    m = _NOTE.match(memo or "")
    return (m.group("payee"), m.group("purpose") or "") if m else None


LINE_CODES: Dict[int, Tuple[str, Optional[str]]] = {
    515: ("loan", "housing"), 507: ("cash", "cash"), 485: ("transfer_out", "transfers"),
    471: ("investment", "investments"), 121: ("income", "interest"), 122: ("income", "other_income"),
    205: ("transfer_in", "transfers_in"),
}


def _inner(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    inner = raw.get("rawTransaction")
    return inner if isinstance(inner, dict) else {}


def line_kind(raw: Mapping[str, Any]) -> Optional[Tuple[str, Optional[str]]]:
    try:
        return LINE_CODES.get(int(_inner(raw).get("activityTypeCode")))
    except (TypeError, ValueError):
        return None


CARD_CATEGORIES: Dict[str, Optional[str]] = {
    "מזון ומשקאות": "groceries", "מסעדות": "restaurants", "מזון מהיר": "restaurants", "אופנה": "clothing",
    "רפואה ובריאות": "health", "פנאי בילוי": "entertainment", "ריהוט ובית": "home", "תיירות": "travel",
    "אנרגיה": None, "תקשורת ומחשבים": None, "ביטוח ופיננסים": None, "שונות": None,
}


def own_category(raw: Mapping[str, Any]) -> Tuple[str, Optional[str]]:
    for text in (raw.get("category"), _inner(raw).get("branchCodeDesc")):
        if isinstance(text, str) and text.strip():
            words = " ".join(text.split())
            return words, CARD_CATEGORIES.get(words)
    return "", None


def id_valid(digits: str) -> bool:
    """A made-up checksum for 9-digit personal numbers (the Luhn rule)."""
    total = 0
    for i, ch in enumerate(digits):
        n = int(ch) * (1 + i % 2)
        total += n - 9 if n > 9 else n
    return len(digits) == 9 and digits.isdigit() and total % 10 == 0


STATEMENTS = Statements(
    PROVIDER,
    redactions=(Redaction(re.compile(r"(?<!\d)\d{2}-\d{3}-\d{4,9}(?!\d)"), ingest.keep_last3),
                Redaction(re.compile(r"(?<!\d)\d{9}(?!\d)"), lambda t: "[id]" if id_valid(t) else None)),
    card_issuers=("sample card", "כאל", "cal", "ישראכרט", "isracard", "לאומי קארד", "מקס", "max"),
    payment_apps=("com.example.samplecard",),
    # Its card app writes pushes in Hebrew.
    push=PushWords(
        currencies=(("ש\"ח", "ILS"), ("ש״ח", "ILS"), ("שח", "ILS"), ("NIS", "ILS")),
        card=(r"המסתיים\s+ב", r"המסתיימת\s+ב", r"שמסתיים\s+ב", r"מסתיים\s+ב", r"בכרטיס(?:\s+(?:מספר|שמספרו))?",
              r"כרטיס(?:\s+(?:מספר|שמספרו))?"),
        merchant=(r"(?:בבית\s+העסק|בבית\s+עסק|אצל)\s*[-:]?\s*(?P<m>[^\n,;.]+)", r"(?<!\w)ב-\s*(?P<m>[^\d\s\n,;.][^\n,;.]*)"),
        merchant_end=("בסך", "בסכום", r"על\s+סך", "סך", "בכרטיס", "בתאריך", "בשעה", "ביום"),
        refund=("זיכוי", "החזר"),
        not_payment=(r"חיוב\s+(?:החודשי|הבא|צפוי)", r"החיוב\s+החודשי", r"דף\s+(?:ה)?פירוט", r"יחויב\s+ב")),
    classify_note="Sample note for the classification prompt.")


def _changed(user: User) -> None:
    sources.provider_changed(user, PROVIDER)


def _bill_script(merchant: str, before: int, after: int, cadence: str, offer: Optional[Dict[str, Any]]) -> str:
    text = f"שלום, החיוב ב{merchant} עלה מ-{savings.exact(before)} ל-{savings.exact(after)}. אבקש לחזור למחיר הקודם."
    if offer:
        text += f" ל-{offer['provider']} יש {offer['plan']} ב-{savings.exact(offer['avg_12'])} לחודש."
    return text


def _fees_script(total: int) -> str:
    return f"שלום, שילמתי {savings.exact(total)} בעמלות בשנה האחרונה. אבקש לבטל אותן או לעבור למסלול הבסיסי."


# Its savings words, local names, pages and phone scripts (in Hebrew, like its statements).
SAVINGS = providers.SavingsRules(
    PROVIDER,
    words={"penalty": ("קנס", "דוח חניה", "ריבית חובה", "ריבית פיגורים", "פיגור", "איחור", "עמלת משיכה"), "money_market_fund": ("כספית",),
           "insurance": ("ביטוח",), "compulsory_insurance": ("חובה",)},
    terms={"money_market_fund": "קרן כספית", "deposit": "פיקדון", "basic_track": "מסלול בסיסי",
           "conversion_fee": "עמלת המרה", "early_repayment": "דוח יתרות לסילוק"},
    links={"insurance_policies": ("the sample policy register", "https://policies.example.test"),
           "car_insurance_prices": ("the sample price calculator", "https://car.example.test")},
    scripts={"bill": _bill_script, "fees": _fees_script}, script_lang="he")


def policy_rate(today: datetime.date) -> Optional[Dict[str, Any]]:
    return {"rate": 4.0, "date": today.isoformat(), "year_ago": 4.5,
            "year_ago_date": (today - datetime.timedelta(days=365)).isoformat()}


def fx_rates(currency: str, start: datetime.date, end: datetime.date) -> Dict[str, float]:
    return {}


Addon(PROVIDER, needs=(Need("addon", "money", "Its sources and transactions"),), title="Money (sample)", description="A made-up provider", on_change=_changed)
_PW = LoginField("password", "Password", secret=True)
providers.register_company(Company(BANK, NAMES[BANK], "bank", (LoginField("userCode", "User code"), _PW), PROVIDER,
                                   _sync, sms_senders=("SampleBank",), code_regex=r"code:?\s*(\d{4,8})",
                                   hash_key="sb", transfer_note=transfer_note, line_kind=line_kind))
providers.register_company(Company(CARD, NAMES[CARD], "card", (LoginField("username", "Username"), _PW), PROVIDER,
                                   _sync, id_key="sc", own_category=own_category))
providers.register_company(Company(INVEST, NAMES[INVEST], "investment", (LoginField("username", "Username"), _PW),
                                   PROVIDER, _sync))
providers.register_company(Company(MEAL, NAMES[MEAL], "meal", (LoginField("phone", "Mobile number"),), PROVIDER,
                                   _sync, card_line_words=("sample meals",)))
providers.register_statements(STATEMENTS)
providers.register_savings_rules(SAVINGS)
RATES = providers.register_rates(providers.Rates(PROVIDER, policy_rate, fx_rates, frozenset({"USD", "EUR"}),
                                                 bank="Bank of Example"))
