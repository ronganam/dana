"""Where money's sources and reference data come from: the provider extension point
(docs/money.md, "Providers").

money signs in nowhere itself. A **provider** addon (``Need("addon", "money", ...)``;
``money_il`` brings the Israeli banks) registers, when its ``addon.py`` is
imported:

- companies (:func:`register_company`): each bank, card, investment house or
  meal card it can read, with its login form, its kind, how its SMS sign-in
  code arrives and ``sync``: ``async def sync(job: SyncJob) -> SyncResult``,
  which signs in, reads and returns the accounts in money's ingest format
  (docs/money.md, "Ingest format"). money runs everything around it: the
  sources and their encrypted credentials, the schedule, the sync runs and
  their states, the code broker, the ingest and the hooks after it;
- reference rates (:func:`register_rates`): the central bank's policy rate and
  FX rates, which savings compares with (no rates: those parts of savings are
  left out);
- price checks (:func:`register_price_checks`): current offers on the web for
  savings' cards (none: the cards show checks as unavailable).

What a provider registers is offered to a user only while they have its addon
(``addon``) on (connected, and enabled on the server). money never imports a
provider."""

import datetime
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, FrozenSet, List, Mapping, Optional, Tuple

from core.users import User
from core.addons import NotConnected, store as connections

KINDS = ("bank", "card", "investment", "meal")

# Sync errors that are login problems: Dana never retries them on its own (docs/money.md, "Sync").
LOGIN_ERRORS = ("INVALID_PASSWORD", "CHANGE_PASSWORD", "ACCOUNT_BLOCKED", "CODE_REJECTED", "LOGIN_UNKNOWN")
CODE_TIMEOUT = "CODE_TIMEOUT"


@dataclass(frozen=True)
class LoginField:
    """One field of a company's login form."""
    name: str
    label: str
    secret: bool = False


class SyncError(Exception):
    """A sync that failed: ``error_type`` is one of :data:`LOGIN_ERRORS` (never retried on its own),
    :data:`CODE_TIMEOUT` (no sign-in code in time) or anything else (``GENERIC``: retried). The
    message is shown to the user after scrubbing: plain words, never a credential."""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type or "GENERIC"


@dataclass
class SyncJob:
    """What a company's ``sync`` gets for one sync of one source.

    ``credentials`` are the login form's values (decrypted for this call only);
    ``device`` is what the last :class:`SyncResult` asked to keep (None at first);
    ``start_date`` is the first local date to read (the last good sync minus an
    overlap, or a year back on the first sync). ``ask_code()`` asks the user for
    the SMS code the site just sent (the phone reads it by the company's
    ``sms_senders`` / ``code_regex``, or the user types it) and returns it;
    it raises :class:`SyncError` (``CODE_TIMEOUT``) when none came in time.
    ``signed_in()`` sends the quiet "Signed in to …" notice once a code worked."""
    user: User
    source_id: str
    company: str
    credentials: Mapping[str, str]
    device: Optional[Mapping[str, str]]
    start_date: datetime.date
    ask_code: Callable[[], Awaitable[str]]
    signed_in: Callable[[], Awaitable[None]]


@dataclass
class SyncResult:
    """A finished sync: ``accounts`` in money's ingest format; ``device``: a key to get back as
    ``SyncJob.device`` next time (kept encrypted); ``device_dropped``: the site refused the kept one."""
    accounts: List[Dict[str, Any]]
    device: Optional[Dict[str, str]] = None
    device_dropped: bool = False


SyncFn = Callable[[SyncJob], Awaitable[SyncResult]]
ForgetFn = Callable[[List[str]], Awaitable[None]]


@dataclass(frozen=True)
class Company:
    """A bank, card company, investment house or meal card a provider can read.

    Args:
        id: Stable id, stored on sources and accounts (``"visaCal"``); unique across providers.
        name: Its name for people ("Cal").
        kind: ``bank`` | ``card`` | ``investment`` | ``meal`` (:data:`KINDS`).
        fields: Its login form; every field is required, secret ones are password inputs.
        addon: The provider addon that brings it (a user needs it on).
        sync: Reads one source (:class:`SyncJob` → :class:`SyncResult`).
        sms_senders: Who sends its SMS sign-in codes (the phone reads them).
        code_regex: The code's pattern in that SMS (its first group is the code).
        id_key: Prefix of a transaction's key made from the site's own ``identifier``
            (``"cal"``); empty: the site's identifiers are not stable, rows are keyed by a hash.
        hash_key: Prefix of a hashed key (``"x"``).
        card_line_words: A meal card's: words of the merchant key of the user's card line that
            pays a cycle's overflow.
        forget: Called with source ids that were removed: drop what the provider keeps for them
            (browser profiles); best-effort.
    """
    id: str
    name: str
    kind: str
    fields: Tuple[LoginField, ...]
    addon: str
    sync: SyncFn
    sms_senders: Tuple[str, ...] = ()
    code_regex: str = ""
    id_key: str = ""
    hash_key: str = "x"
    card_line_words: Tuple[str, ...] = ()
    forget: Optional[ForgetFn] = None

    def payload(self) -> Dict[str, Any]:
        """``{id, name, kind, fields: [{name, label, secret}]}`` for clients."""
        return {"id": self.id, "name": self.name, "kind": self.kind,
                "fields": [{"name": f.name, "label": f.label, "secret": f.secret} for f in self.fields]}


@dataclass(frozen=True)
class Rates:
    """Reference rates from a central bank. ``policy_rate(today)`` → ``{rate, date, year_ago,
    year_ago_date}`` (percent; None when unknown); ``fx_rates(currency, start, end)`` → ``{ISO date:
    units of the home currency per unit}`` for each of ``currencies`` (``{}`` when unknown). Both may
    fetch (they run in a worker thread) and must cache."""
    addon: str
    policy_rate: Callable[[datetime.date], Optional[Dict[str, Any]]]
    fx_rates: Callable[[str, datetime.date, datetime.date], Dict[str, float]]
    currencies: FrozenSet[str] = field(default_factory=frozenset)


class PriceCheckUnavailable(Exception):
    """No price check can run (no model that searches, or no provider): 503 ``unavailable``."""


class PriceCheckRunning(Exception):
    """The same check is running: 409 ``running``."""


class PriceCheckTooSoon(Exception):
    """The same check ran moments ago: 429 ``too_soon`` with ``retry_after`` seconds."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"checked recently, again in {retry_after} s")
        self.retry_after = retry_after


@dataclass(frozen=True)
class PriceChecks:
    """Price checks savings' cards can ask for. ``latest(user, topic, params)`` → the newest check
    ``{topic, params, state, checked_at, stale, items, sources, error}`` or None (raises ValueError for
    params it refuses); ``start(user, topic, params)`` → ``{state: "checking"}`` (on the event loop;
    raises ValueError, :class:`PriceCheckUnavailable`, :class:`PriceCheckRunning`, :class:`PriceCheckTooSoon`)."""
    addon: str
    topics: Tuple[str, ...]
    latest: Callable[[User, str, Optional[Mapping[str, Any]]], Optional[Dict[str, Any]]]
    start: Callable[[User, str, Optional[Mapping[str, Any]]], Dict[str, str]]


class UnknownCompany(ValueError):
    """A company id no loaded provider brings (the API answers 400 ``unknown_company``)."""


_companies: Dict[str, Company] = {}
_rates: List[Rates] = []
_checks: List[PriceChecks] = []


def register_company(company: Company) -> Company:
    """Offer ``company`` (a provider's ``addon.py`` calls it at import). Registering an id again from
    the same addon replaces it.

    Raises:
        ValueError: an unknown kind, or another addon already brings that id.
    """
    if company.kind not in KINDS:
        raise ValueError(f"Money company {company.id!r}: unknown kind {company.kind!r} (one of {', '.join(KINDS)})")
    current = _companies.get(company.id)
    if current is not None and current.addon != company.addon:
        raise ValueError(f"Money company {company.id!r} is already brought by {current.addon!r}")
    _companies[company.id] = company
    return company


def register_rates(rates: Rates) -> Rates:
    """Offer reference rates (a provider's ``addon.py``); one per addon."""
    _rates[:] = [r for r in _rates if r.addon != rates.addon] + [rates]
    return rates


def register_price_checks(checks: PriceChecks) -> PriceChecks:
    """Offer price checks (a provider's ``addon.py``); one per addon."""
    _checks[:] = [c for c in _checks if c.addon != checks.addon] + [checks]
    return checks


def company(company_id: str) -> Optional[Company]:
    """The company ``company_id`` whoever has it (names, keys, card lines), None when no provider brings it."""
    return _companies.get(company_id)


def name_of(company_id: str) -> str:
    """The company's display name ("Bank Hapoalim"); the id itself when unknown."""
    c = _companies.get(company_id)
    return c.name if c else company_id


def available(user: User, company_id: str) -> bool:
    """Whether ``user`` can add and sync logins at ``company_id`` (its provider addon is on for them)."""
    c = _companies.get(company_id)
    return c is not None and connections.is_connected(user, c.addon)


def companies(user: User) -> List[Company]:
    """The companies ``user`` can add, in registration order."""
    return [c for c in _companies.values() if connections.is_connected(user, c.addon)]


def get(user: User, company_id: str) -> Company:
    """The company ``company_id`` for ``user``.

    Raises:
        UnknownCompany: no loaded provider brings it.
        NotConnected: its provider addon is not on for the user (403 ``not_connected``).
    """
    c = _companies.get(company_id)
    if c is None:
        known = ", ".join(x.id for x in companies(user)) or "none"
        raise UnknownCompany(f"Unknown company {company_id!r} (known: {known})")
    if not connections.is_connected(user, c.addon):
        raise NotConnected(user, c.addon)
    return c


def rates(user: User) -> Optional[Rates]:
    """The reference rates ``user`` has (the first provider of theirs that brings some), None without."""
    return next((r for r in _rates if connections.is_connected(user, r.addon)), None)


def price_checks(user: User) -> Optional[PriceChecks]:
    """The price checks ``user`` has, None without."""
    return next((c for c in _checks if connections.is_connected(user, c.addon)), None)


def rate_on(values: Mapping[str, float], day: str) -> Optional[float]:
    """The rate of ISO ``day`` from :attr:`Rates.fx_rates`' answer, or of the last business day before
    it (within a week); None without one."""
    if day in values:
        return values[day]
    try:
        d = datetime.date.fromisoformat(day[:10])
    except ValueError:
        return None
    for back in range(1, 8):
        value = values.get((d - datetime.timedelta(days=back)).isoformat())
        if value is not None:
            return value
    return None
