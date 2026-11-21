"""The types an addon declares and its hooks return (../docs/addons.md, "The
manifest"; docs/writing-an-addon.md). Plain data: nothing here imports the
rest of the app.

Manifest values: :class:`Web` / :class:`Nav` (a page in the web app),
:class:`Worker` (a sidecar), :class:`LlmJob` (an LLM purpose), and the
schedule helpers :func:`minutes` / :func:`hours` for ``@addon.job(every=)``.

What hooks return, each with ``to_json()`` giving the exact payload the core
composers have always produced:

- :class:`Card` — a card on the Now screen (``@addon.now_cards``);
- :class:`DayItem` — an item in the Now screen's day strip (``@addon.day_items``);
- :class:`Section` — a part of the morning briefing or the weekly review (``@addon.briefing``);
- :class:`Hit` — a result of the global search (``@addon.search``);
- :class:`Check` — a line of the self-checks (``@addon.selfcheck``);
- :class:`Approval` — the Approve card of a tool call (``@addon.tool(describe=)``).

What the framework keeps per hook registration: :class:`Job`,
:class:`Service`, :class:`Handler`, :class:`Channel`, :class:`Hook`.
"""

import datetime
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# --- Manifest values ----------------------------------------------------------------


@dataclass(frozen=True)
class Nav:
    """The addon's entry in the web app's navigation."""
    label: str
    icon: str = ""
    order: int = 100


@dataclass(frozen=True)
class Web:
    """A page in the web app: ``panel`` (JavaScript) and ``style`` (CSS) are
    paths inside the addon's ``web/`` folder, served at ``/addons/<name>/web/``
    and listed by ``GET /api/addons`` (core/addons_api.py)."""
    panel: str = ""
    style: str = ""
    nav: Optional[Nav] = None


@dataclass(frozen=True)
class Worker:
    """A sidecar process built from the addon's ``worker/`` folder (a
    ``Dockerfile``): the port it listens on and the env variable that tells
    Dana its URL. ``bin/deploy`` builds it as ``<image>-<addon>``;
    ``compose.yaml`` runs it under a profile named after the addon
    (docs/operations.md, "Workers")."""
    port: int
    url_env: str


@dataclass(frozen=True)
class LlmJob:
    """An LLM purpose of the addon: its default model profile (``fast`` /
    ``balanced`` / ``deep``) and how the Usage screen names it ("" = from the
    purpose's name)."""
    profile: str
    label: str = ""


def minutes(n: float) -> datetime.timedelta:
    """``@addon.job(every=minutes(5))``."""
    return datetime.timedelta(minutes=n)


def hours(n: float) -> datetime.timedelta:
    """``@addon.job(every=hours(6))``."""
    return datetime.timedelta(hours=n)


# --- Registrations ------------------------------------------------------------------


@dataclass(frozen=True)
class Job:
    """``@addon.job``: ``func(user)`` run for every user who has the addon,
    either ``every`` interval, ``daily`` at "HH:MM" local time, or once at start."""
    addon: str
    name: str
    func: Callable[..., Any]
    every: Optional[datetime.timedelta] = None
    daily: Optional[str] = None
    at_start: bool = False
    # Seconds to wait after the server starts before the first pass.
    delay: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.addon}.{self.name}"


@dataclass(frozen=True)
class Service:
    """``@addon.service``: ``func(app)`` started once with the server, restarted with backoff."""
    addon: str
    name: str
    func: Callable[..., Any]

    @property
    def key(self) -> str:
        return f"{self.addon}.{self.name}"


@dataclass(frozen=True)
class Handler:
    """``@addon.on(event)``: ``func(user, data)`` called in the background when ``event`` is emitted."""
    addon: str
    event: str
    func: Callable[..., Any]


@dataclass(frozen=True)
class Channel:
    """``@addon.channel(name)``: ``func(user, message) -> bool`` delivering a :func:`core.notify.send`."""
    addon: str
    name: str
    func: Callable[..., Any]


@dataclass(frozen=True)
class Hook:
    """A composer hook (``now_cards``, ``day_items``, ``now_action``, ``briefing``,
    ``review_action``, ``search``, ``selfcheck``, ``context``, ``context_card``):
    ``key`` names what it is for when the hook has one (the card kind it acts on,
    the context kind, the check key), ``options`` its decorator's other values."""
    addon: str
    kind: str
    func: Callable[..., Any]
    key: str = ""
    options: Tuple[Tuple[str, Any], ...] = ()

    def option(self, name: str, default: Any = None) -> Any:
        return dict(self.options).get(name, default)


# --- Now ------------------------------------------------------------------------------


@dataclass
class Card:
    """A card in the Now queue (docs/now.md). ``id`` is ``<kind prefix>:<id>``;
    the prefix routes ``POST /api/now/act`` to the addon's ``@addon.now_action``.
    ``primary`` / ``secondary`` are actions (``core.cards.action``)."""
    id: str
    kind: str
    source: str
    title: str
    why: str = ""
    primary: Optional[Dict[str, Any]] = None
    secondary: List[Dict[str, Any]] = field(default_factory=list)
    schedulable: bool = True
    ref: Optional[Dict[str, Any]] = None
    blocks: List[Dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "source": self.source, "title": self.title, "why": self.why,
                "primary": self.primary, "secondary": list(self.secondary), "schedulable": self.schedulable,
                "ref": self.ref, "blocks": list(self.blocks)}


@dataclass
class DayItem:
    """An item of the Now day strip: at an hour today (``at``), or in the
    ``home`` / ``wake`` slot (``slot``); ``fields`` are extra keys of its payload
    (``{"task_id": ...}``)."""
    title: str
    kind: str
    at: Optional[datetime.datetime] = None
    slot: str = ""
    fields: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"title": self.title, "kind": self.kind, **self.fields}
        if self.at is not None and not self.slot:
            out["time"] = self.at.strftime("%H:%M")
        return out


@dataclass(frozen=True)
class NowContext:
    """What a Now hook gets besides the user: the moment, the cards the user
    hid (``now_hidden``: item id → row) and today's calendar events (None:
    not read)."""
    now: datetime.datetime
    hidden: Dict[str, Any]
    events: Optional[List[Dict[str, Any]]] = None


# --- Briefing / weekly review ---------------------------------------------------------

BRIEFING = "briefing"
WEEKLY_REVIEW = "weekly_review"


@dataclass(frozen=True)
class BriefingContext:
    """What ``@addon.briefing`` gets: ``kind`` (:data:`BRIEFING` or
    :data:`WEEKLY_REVIEW`) and the moment."""
    kind: str
    now: datetime.datetime

    @property
    def today(self) -> str:
        return self.now.date().isoformat()


@dataclass
class Section:
    """An addon's part of a briefing or weekly review.

    ``key``: where ``data`` goes in the stored briefing / review data (and the
    model's prompt). Briefing: ``points`` are the plain lines used when the
    model is not, ``headline`` is added to the plain headline, ``counts`` go to
    the phone push. Weekly review: ``items`` are things to decide (each with
    ``ref``, ``type``, ``title``, ``why``, ``facts``), acted on by the addon's
    ``@addon.review_action(type)``.
    """
    key: str
    data: Any = None
    points: List[str] = field(default_factory=list)
    headline: str = ""
    counts: Dict[str, int] = field(default_factory=dict)
    items: List[Dict[str, Any]] = field(default_factory=list)


# --- Search ---------------------------------------------------------------------------


@dataclass
class Hit:
    """A global-search result; ``score`` orders results (higher first),
    ``extra`` are more keys of its payload (``{"project_id": ...}``)."""
    kind: str
    id: str
    title: str
    subtitle: str = ""
    score: float = 0.5
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        return {"kind": self.kind, "id": self.id, "title": self.title, "subtitle": self.subtitle, **self.extra}


# --- Self-checks ----------------------------------------------------------------------


@dataclass
class Check:
    """A self-check line (``/api/checks``; a failing one is a Now card).
    ``title`` / ``why`` / ``blocks`` describe the card when ``ok`` is False."""
    ok: bool
    detail: str
    since: Optional[datetime.datetime] = None
    title: str = ""
    why: str = ""
    blocks: List[Dict[str, Any]] = field(default_factory=list)

    def to_json(self, key: str, label: str) -> Dict[str, Any]:
        return {"key": key, "label": label, "ok": self.ok, "detail": self.detail,
                "since": self.since.isoformat(timespec="seconds") if self.since else None,
                "title": self.title, "why": self.why, "blocks": list(self.blocks)}


# --- Context chats --------------------------------------------------------------------


@dataclass(frozen=True)
class ContextRequest:
    """What ``@addon.context`` gets besides the user and item id: ``block(kind,
    ref_id)`` gives another item's description for the prompt (a task's source
    call), "" when there is none."""
    block: Callable[[str, str], str]


# --- Approval -------------------------------------------------------------------------


def clean(value: Any, limit: int = 600) -> str:
    """A value as one tidy string for a person to read (JSON for dicts / lists), cut at ``limit``."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    s = re.sub(r"[ \t]+", " ", str(value)).strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


@dataclass
class Approval:
    """The words of an Approve card (Telegram, the app, the web): the action
    as a sentence (``title``), the header (``heading`` + ``emoji``), the
    content (``body`` under ``body_label``), other details (``facts``: label,
    value; empty values are left out) and the ``question``."""
    title: str
    heading: str
    emoji: str
    question: str
    body_label: str = ""
    body: Any = ""
    facts: Sequence[Tuple[str, Any]] = ()
    fact_limit: int = 300

    def to_json(self) -> Dict[str, Any]:
        return {"title": self.title, "heading": self.heading, "emoji": self.emoji,
                "body_label": self.body_label if self.body else "", "body": clean(self.body, 1500),
                "facts": [(k, clean(v, self.fact_limit)) for k, v in self.facts if clean(v)],
                "question": self.question}
