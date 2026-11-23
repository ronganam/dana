"""The types an addon declares and its hooks return (../docs/addons.md, "The
manifest"; docs/writing-an-addon.md). Plain data: nothing here imports the
rest of the app.

Manifest values: :class:`Page` / :class:`Nav` (a page in the web app),
:class:`Worker` (a sidecar), :class:`LlmJob` (an LLM purpose),
:class:`Need` (what running it takes) / :class:`Touches` (what it may touch), and the
schedule helpers :func:`minutes` / :func:`hours` for ``@addon.job(every=)``.

What hooks get besides the user: :class:`NowContext`, :class:`BriefingContext`,
:class:`ContextRequest`, :class:`DemoContext`, :class:`WidgetContext`. What they return (``to_json()`` gives a payload):

- :class:`Card` — a card on the Now screen (``@addon.now_cards``);
- :class:`DayItem` — an item in the Now screen's day strip (``@addon.day_items``);
- :class:`Section` — a part of the morning briefing (``@addon.briefing``) or the weekly review (``@addon.review``);
- :class:`Hit` — a result of the global search (``@addon.search``);
- :class:`Check` — a line of the self-checks (``@addon.selfcheck``), with an optional :class:`Fix`;
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
    """The addon's entry in the web app's navigation: ``order`` places it
    among the others, ``primary`` asks for a place in the default navigation
    bar (Chat and up to three such pages; each user may choose their own:
    docs/web-design.md, "Navigation")."""
    label: str
    icon: str = ""
    order: int = 100
    primary: bool = False


# The web app's own screens: no addon page may take one of these ids (static/app-nav.js, CORE_SCREENS).
CORE_SCREENS = ("now", "chat", "settings", "attention", "addons", "devices", "household", "usage", "debug", "review")
PAGE_ID = re.compile(r"^[a-z][a-z0-9_-]*$")


@dataclass(frozen=True)
class Page:
    """A page of the addon in the web app: ``id`` names the screen (its
    ``#panel-<id>`` element, ``/<id>`` and ``#<id>`` open it; unique across
    addons, not a core screen's), ``panel`` (JavaScript) and ``style`` (CSS)
    are paths inside the addon's ``web/`` folder, served at
    ``/addons/<name>/web/`` and listed by ``GET /api/addons``
    (core/addons_api.py); ``nav`` puts a button in the navigation (None: the
    page opens only from a link or another page)."""
    id: str
    panel: str
    style: str = ""
    nav: Optional[Nav] = None

    def __post_init__(self) -> None:
        if not PAGE_ID.match(self.id):
            raise ValueError(f"Page id {self.id!r}: lowercase letters, digits, _ or -, starting with a letter")
        if self.id in CORE_SCREENS:
            raise ValueError(f"Page id {self.id!r} is a core screen's")
        if not self.panel:
            raise ValueError(f"Page {self.id!r} needs its panel script")


@dataclass(frozen=True)
class Worker:
    """A sidecar process next to Dana: the port it listens on and the env
    variable that tells Dana its URL. ``image``: the published image it runs
    (``ghcr.io/<owner>/<name>``, the addon's repository building it from its
    ``worker/`` folder; a tag of its own, else ``bin/deploy`` pins it to the
    commit the addon was installed at, so an addon installed from a folder
    names a tag); empty: nothing is published, the ``worker/`` folder (a
    ``Dockerfile``) is built where it runs (``bin/dana addons dev``, your own
    compose file). ``compose.yaml`` runs it under a profile named after the
    addon (docs/operations.md, "Workers")."""
    port: int
    url_env: str
    image: str = ""


# What a Need can be: a server env value, an account at a service, hardware or a server you run, the
# Dana phone app, a place it only works in, another addon.
ENV, ACCOUNT, HARDWARE, APP, REGION, ADDON = "env", "account", "hardware", "app", "region", "addon"
NEED_KINDS = (ENV, ACCOUNT, HARDWARE, APP, REGION, ADDON)
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
ADDON_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
MAX_WHAT = 80
MAX_WHY = 160
MAX_HOST = 80


def _line(owner: str, what: str, text: Any, limit: int) -> None:
    """Refuse ``text`` unless it is one short line of words."""
    if not isinstance(text, str) or not text.strip() or "\n" in text:
        raise ValueError(f"{owner}: {what} must be one line of words")
    if len(text) > limit:
        raise ValueError(f"{owner}: {what} is {len(text)} characters; keep it to {limit}")


@dataclass(frozen=True)
class Need:
    """Something the addon needs from whoever runs it, shown before it is
    turned on (the Addons page, ``bin/dana addons table``, the README).

    ``kind`` is one of :data:`NEED_KINDS`: ``env`` (``what`` is the env
    variable, ``GOOGLE_CLIENT_ID``), ``account`` (an account at a service),
    ``hardware`` (a device, or a server you run: Home Assistant, WAHA),
    ``app`` (the Dana Android app), ``region`` (where it works at all: a
    country), ``addon`` (``what`` is the other addon's name). Other kinds'
    ``what`` is a phrase that reads inside a sentence ("a Google account").
    ``why`` says in one line what it is for. ``optional``: the addon works without it, with
    less. A required ``addon`` need is loaded first and must be on for the
    addon to work (``Addon.depends``)."""
    kind: str
    what: str
    why: str
    optional: bool = False

    def __post_init__(self) -> None:
        owner = f"Need({self.kind!r}, {self.what!r})"
        if self.kind not in NEED_KINDS:
            raise ValueError(f"{owner}: kind must be one of {', '.join(NEED_KINDS)}")
        _line(owner, "what", self.what, MAX_WHAT)
        _line(owner, "why", self.why, MAX_WHY)
        if self.kind == ENV and not ENV_NAME.match(self.what):
            raise ValueError(f"{owner}: an env need names the variable (upper case, digits, _)")
        if self.kind == ADDON and not ADDON_NAME.match(self.what):
            raise ValueError(f"{owner}: an addon need names the addon (lower case, digits, _)")

    def to_json(self) -> Dict[str, Any]:
        return {"kind": self.kind, "what": self.what, "why": self.why, "optional": self.optional}


@dataclass(frozen=True)
class Touches:
    """What the addon may touch beyond Dana's own data, shown before it is
    turned on and before it is installed: the network ``hosts`` it contacts
    (a domain, or words for an address the user gives: "your Home Assistant
    server"), whether it ``acts_outside`` (sends messages or mail, changes
    things at a service or on devices for the user), whether it
    ``stores_credentials`` (passwords, tokens, sign-ins; encrypted), and the
    other addons whose data it ``reads``. A declaration, not a sandbox: every
    addon runs with the server's full access (SECURITY.md). Installing or
    updating an addon whose touches grew asks the admin again
    (core/addons/install.py)."""
    hosts: Tuple[str, ...] = ()
    acts_outside: bool = False
    stores_credentials: bool = False
    reads: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for what, items in (("hosts", self.hosts), ("reads", self.reads)):
            if not isinstance(items, tuple):
                raise ValueError(f"Touches: {what} must be a tuple of strings: {what}=(\"...\",)")
            if len(set(items)) != len(items):
                raise ValueError(f"Touches: {what} names one twice")
        for host in self.hosts:
            _line("Touches", "a host", host, MAX_HOST)
        for name in self.reads:
            if not isinstance(name, str) or not ADDON_NAME.match(name):
                raise ValueError(f"Touches: reads names addons (lower case, digits, _), not {name!r}")
        for what in ("acts_outside", "stores_credentials"):
            if not isinstance(getattr(self, what), bool):
                raise ValueError(f"Touches: {what} must be True or False")

    @property
    def empty(self) -> bool:
        """It touches nothing beyond Dana's own data."""
        return not (self.hosts or self.acts_outside or self.stores_credentials or self.reads)

    def added_since(self, before: "Touches") -> "Touches":
        """What this declares that ``before`` did not (empty: nothing new)."""
        return Touches(hosts=tuple(h for h in self.hosts if h not in before.hosts),
                       acts_outside=self.acts_outside and not before.acts_outside,
                       stores_credentials=self.stores_credentials and not before.stores_credentials,
                       reads=tuple(r for r in self.reads if r not in before.reads))

    def to_json(self) -> Dict[str, Any]:
        return {"hosts": list(self.hosts), "acts_outside": self.acts_outside,
                "stores_credentials": self.stores_credentials, "reads": list(self.reads)}

    @classmethod
    def from_json(cls, data: Any) -> "Touches":
        """The touches a payload gives (``to_json``'s shape; missing keys: none).

        Raises:
            ValueError: not that shape.
        """
        if not isinstance(data, dict):
            raise ValueError("touches must be an object: {hosts, acts_outside, stores_credentials, reads}")
        unknown = sorted(set(data) - {"hosts", "acts_outside", "stores_credentials", "reads"})
        if unknown:
            raise ValueError(f"touches has no {unknown[0]!r}")
        hosts, reads = data.get("hosts", []), data.get("reads", [])
        if not isinstance(hosts, list) or not isinstance(reads, list):
            raise ValueError("touches: hosts and reads must be lists")
        return cls(hosts=tuple(hosts), acts_outside=data.get("acts_outside", False),
                   stores_credentials=data.get("stores_credentials", False), reads=tuple(reads))


@dataclass(frozen=True)
class LlmJob:
    """An LLM purpose of the addon: its default model profile (``fast`` /
    ``balanced`` / ``deep``), how the Usage screen names it ("" = from the
    purpose's name), and whether it is ``background`` work Dana does on its
    own (the monthly budget pauses it; core/budget.py) rather than something a
    person asked for."""
    profile: str
    label: str = ""
    background: bool = False


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
    # Seconds one user's run may take before it is cancelled (logged as failed for that user).
    timeout: float = 600.0

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
    """A hook declared with ``@addon.<kind>`` (``now_cards``, ``day_items``,
    ``now_action``, ``briefing``, ``review``, ``review_action``, ``search``,
    ``selfcheck``, ``context``, ``context_card``, ``context_block``,
    ``provide``, ``widget``, ``fix``): ``key`` names what it is for when the hook has one (the card
    prefix it acts on, the section key, the item type, the context kind, the
    check (``selfcheck``, ``fix``), provider or widget key; unique across addons), ``options`` its decorator's
    other values."""
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
    ``primary`` / ``secondary`` are actions (``core.cards.action``). Set ``why=None``
    to omit the key from the payload; ``extra`` adds top-level keys."""
    id: str
    kind: str
    source: str
    title: str
    why: Optional[str] = ""
    primary: Optional[Dict[str, Any]] = None
    secondary: List[Dict[str, Any]] = field(default_factory=list)
    schedulable: bool = True
    ref: Optional[Dict[str, Any]] = None
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"id": self.id, "kind": self.kind, "source": self.source, "title": self.title}
        if self.why is not None:
            out["why"] = self.why
        out.update({"primary": self.primary, "secondary": list(self.secondary), "schedulable": self.schedulable,
                    "ref": self.ref, "blocks": list(self.blocks), **self.extra})
        return out


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


# The sizes a dashboard widget can be drawn at (``@addon.widget``; docs/dashboard.md).
WIDGET_SIZES = ("small", "medium", "large")


@dataclass(frozen=True)
class WidgetContext:
    """What a dashboard widget's function gets besides the user (``@addon.widget``):
    this widget's ``settings`` on the dashboard (every setting it declares, the
    default filled in where the dashboard has none), the ``size`` it is drawn at
    and the moment ``now`` (aware, the user's zone)."""
    settings: Dict[str, Any]
    size: str
    now: datetime.datetime


@dataclass(frozen=True)
class DemoContext:
    """What ``@addon.demo`` gets besides the user: the moment, the demo
    household's users (oldest first; ``household[0]`` is the one an admin
    views first) and the user's place in it."""
    now: datetime.datetime
    household: Tuple[Any, ...]
    index: int

    def ago(self, days: float = 0, hours: float = 0, minutes: float = 0) -> datetime.datetime:
        """``now`` minus the given time (aware, the server's zone)."""
        return self.now - datetime.timedelta(days=days, hours=hours, minutes=minutes)


# --- Briefing / weekly review ---------------------------------------------------------


@dataclass(frozen=True)
class BriefingContext:
    """What ``@addon.briefing`` and ``@addon.review`` get besides the user: the moment."""
    now: datetime.datetime

    @property
    def today(self) -> str:
        return self.now.date().isoformat()


@dataclass
class Section:
    """An addon's part of a briefing (``@addon.briefing``) or weekly review
    (``@addon.review``), stored under the key its decorator declares.

    ``data``: what goes in the stored briefing / review data (and the model's
    prompt). Briefing: ``points`` are the plain lines used when the model is
    not, ``headline`` is added to the plain headline, ``counts`` go to the
    phone push (only the keys the decorator declares). Weekly review:
    ``items`` are things to decide (each with ``ref``, ``type``, ``title``,
    ``why``, ``facts``), acted on by the addon's ``@addon.review_action(type)``.
    """
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


# What a fix does: open the addon's connect page, open a screen of the app, open a web address (each done by
# the client), or run an action the check's addon handles on the server (``@addon.fix``).
CONNECT, SCREEN, URL, SERVER = "connect", "screen", "url", "server"
FIX_KINDS = (CONNECT, SCREEN, URL, SERVER)
FIX_ACTION = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MAX_FIX_LABEL = 40


@dataclass(frozen=True)
class Fix:
    """The button that fixes a failing check (docs/now.md, "Fixes"): its
    ``label`` and what it does. Build one with :meth:`connect` (the addon's
    connect page on the Addons page: sign in again, type a new key),
    :meth:`screen` (a screen of the app: ``devices``, ``money``),
    :meth:`link` (an https address) or :meth:`run` (an action the check's
    addon carries out on the server, its ``@addon.fix(<check key>)``
    receiving ``target`` as the action's name)."""
    kind: str
    target: str
    label: str

    def __post_init__(self) -> None:
        owner = f"Fix({self.kind!r}, {self.target!r})"
        if self.kind not in FIX_KINDS:
            raise ValueError(f"{owner}: kind must be one of {', '.join(FIX_KINDS)}")
        _line(owner, "label", self.label, MAX_FIX_LABEL)
        if self.kind == CONNECT and not ADDON_NAME.match(self.target):
            raise ValueError(f"{owner}: a connect fix names the addon")
        if self.kind == SCREEN and not PAGE_ID.match(self.target):
            raise ValueError(f"{owner}: a screen fix names a screen id (lower case, digits, _ or -)")
        if self.kind == URL and not re.match(r"^https://[^\s\"'<>]+$", self.target):
            raise ValueError(f"{owner}: a link fix is an https:// address")
        if self.kind == SERVER and not FIX_ACTION.match(self.target):
            raise ValueError(f"{owner}: a server fix names its action (lower case, digits, _)")

    @classmethod
    def connect(cls, addon: str, label: str = "Reconnect") -> "Fix":
        """Open ``addon``'s page on the Addons page (its sign-in or its credentials)."""
        return cls(CONNECT, addon, label)

    @classmethod
    def screen(cls, screen: str, label: str) -> "Fix":
        """Open a screen of the app: a core screen (``devices``, ``usage``…) or an addon's page id."""
        return cls(SCREEN, screen, label)

    @classmethod
    def link(cls, url: str, label: str) -> "Fix":
        """Open an https address (the service's own settings page)."""
        return cls(URL, url, label)

    @classmethod
    def run(cls, action: str, label: str) -> "Fix":
        """Run ``action`` on the server: the check's addon's ``@addon.fix`` gets the user and the action."""
        return cls(SERVER, action, label)

    def to_json(self) -> Dict[str, Any]:
        key = {CONNECT: "addon", SCREEN: "screen", URL: "url", SERVER: "action"}[self.kind]
        return {"kind": self.kind, "label": self.label, key: self.target}


@dataclass
class Check:
    """A self-check line (``/api/checks``; a failing one is a Now card and a
    line of Settings → Needs attention). ``title`` / ``why`` / ``blocks``
    describe the card when ``ok`` is False; ``fix`` is its button (none: the
    card only explains)."""
    ok: bool
    detail: str
    since: Optional[datetime.datetime] = None
    title: str = ""
    why: str = ""
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    fix: Optional[Fix] = None

    def to_json(self, key: str, label: str) -> Dict[str, Any]:
        return {"key": key, "label": label, "ok": self.ok, "detail": self.detail,
                "since": self.since.isoformat(timespec="seconds") if self.since else None,
                "title": self.title, "why": self.why, "blocks": list(self.blocks),
                "fix": self.fix.to_json() if self.fix is not None else None}


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
