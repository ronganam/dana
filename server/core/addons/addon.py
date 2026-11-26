"""Addons, the tools they own and their hooks (../docs/addons.md; docs/access.md, "Addon").

An addon is declared once, in its folder's ``addon.py``::

    addon = Addon("whatsapp", title="WhatsApp", credentials=(Field("url"), ...), settings=(...))

    @addon.tool(confirm=True)                     # in its tools.py
    async def send_whatsapp_message(conn, chat_id: str, text: str) -> str: ...

    @addon.job(every=minutes(10))                 # in any module the loader imports
    async def refresh(user): ...

Declaring an addon registers it. The loader (core/addons/loader.py) imports
every addon and fixes their order (:func:`set_order`); that order, then the
order tools are defined in, is the order of every tool list and of every
hook's results.
"""

import datetime
import inspect
import logging
import re
from dataclasses import dataclass
from typing import (Any, Awaitable, Callable, Collection, Dict, List, Mapping, Optional, Sequence, Tuple, TypeVar,
                    Union)

from core.access import CAPABILITIES
from core.users import User
from core.addons.hooks import (ADDON as ADDON_NEED, CONNECT, WIDGET_SIZES, Channel, EventType, Fix, Handler, Hook, Job, LlmJob,
                               Need, Page, Service, Touches, Worker)
from core.addons.schema import generate_schema

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])
ConfirmRule = Union[bool, Callable[[Dict[str, Any]], bool]]
# Whether a tool is offered to a user beyond their connected addons (e.g.
# share_item: only on a server with more than one user).
AvailabilityRule = Callable[[User], bool]
# An addon's part of the agent's system prompt for one user (About-you facts,
# the home map): text appended as is, "" for nothing.
PromptSection = Callable[[User], str]
# The three hooks below take the user's core.addons.store.Connection
# (typed Any here: store imports this module).
# Why the addon's tools cannot run for a connection right now, or None.
ConnectionBlock = Callable[[Any], Optional[str]]
# A cheap request that tells whether a stored connection works: None when it
# does, else a short reason without secrets.
ConnectionTest = Callable[[Any], Awaitable[Optional[str]]]
# Values a client can offer for the addon's settings, read from the user's own
# service: {setting name: [{"value": ..., "label": ...}]}.
SettingOptions = Callable[[Any], Awaitable[Dict[str, List[Dict[str, Any]]]]]
# An addon's own rule over a connect / update: the user it is for, the values
# given (credentials and settings by name; None: removed / back to the
# default), the credentials and the settings after the change (defaults
# filled in) and who makes the change (None: the server itself / an operator
# with shell access). Raises InvalidValue to refuse.
SettingsRule = Callable[[User, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], Optional[User]], None]
# The Approve card of one call of a tool: (the user, the call's arguments) → a
# core.addons.hooks.Approval (typed Any here).
DescribeRule = Callable[[User, Dict[str, Any]], Any]
# Event names: <addon>.<thing>.<what happened> (core: core.<thing>.<what>).
EVENT_NAME = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+\.[a-z0-9_]+$")
DAILY = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# Composer hooks: their functions are plain (not async): composers call them in
# a worker thread, next to their own database reads. ``FLEX_HOOKS`` accept either
# sync or async functions (e.g. ``briefing`` sections that fetch weather, or ``provide``).
SYNC_HOOKS = ("now_cards", "day_items", "review", "search", "selfcheck", "context", "context_card",
              "context_block", "demo")
FLEX_HOOKS = ("briefing", "provide", "widget")
# Hooks declared for a key (a card prefix, a section key, a check key, an item
# type, a context kind): one addon per key and kind across every addon (the
# loader fails the later addon that repeats one). A provider key (``provide``)
# may be declared by several addons, ranked (core/addons/registry.py ``providers``).
KEYED_HOOKS = ("now_action", "briefing", "review", "review_action", "selfcheck", "fix", "context", "context_card",
               "context_block", "widget")
_MISSING = object()


@dataclass(frozen=True)
class PromptPart:
    """A piece of the agent's instructions that applies only to a user who has
    the addon (and every addon in ``needs``) connected.

    ``slot`` names the place in the instructions (core/agent.py lists the
    slots and how each joins its parts: a phrase in a list, a sentence, a
    paragraph); ``rank`` orders the parts of one slot (lower first; ties in
    the fixed addon order)."""
    slot: str
    text: str
    rank: int = 50
    needs: Tuple[str, ...] = ()


# Field types (docs/access.md, "Security rules"): what a value must look like
# and who may set it, checked by core.addons.store on connect / update.
TEXT = "text"
# A base URL the server sends requests to (core/addons/addresses.py):
# validated and stored normalised for everyone; a user without
# ``manage_users`` may only use an origin an admin allowed for the addon.
ADDRESS = "address"
# A Telegram chat id the server sends to (or reads from): a user without
# ``manage_users`` may only name one of their own Telegram ids (their private
# chat with the bot).
TELEGRAM_CHAT = "telegram_chat"
# A short name that goes into a request path (a WAHA session): letters,
# digits, ``_``, ``-``, ``.``.
NAME = "name"
FIELD_TYPES = (TEXT, ADDRESS, TELEGRAM_CHAT, NAME)


class InvalidValue(ValueError):
    """A credential or setting value that is refused (``field`` names it):
    the API answers 400 ``{"error": "invalid", "field", "message"}``."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


# Limits of the words a manifest gives the Addons page (checked when it is declared).
MAX_LABEL = 40
MAX_HELP = 200
MAX_PLACEHOLDER = 80
MAX_LINE = 200
MAX_LINES = 8
ICON = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
# A dashboard widget's key, and the file of the addon's web/ folder that draws it.
WIDGET_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
WIDGET_SCRIPT = re.compile(r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*\.js$")
MAX_WIDGET_SETTINGS = 8
# The shortest interval of @addon.live_refresh (core/live.py ticks every 5 seconds).
MIN_LIVE_EVERY = 5


def _https_url(owner: str, url: str) -> None:
    """Refuse a help link that is not a plain ``https://`` address."""
    if url and (not url.startswith("https://") or len(url) <= len("https://") or any(c.isspace() for c in url)):
        raise ValueError(f"{owner}: url {url!r} must be an https:// address")


def _short(owner: str, what: str, text: str, limit: int) -> None:
    if len(text) > limit:
        raise ValueError(f"{owner}: {what} is {len(text)} characters; keep it to {limit}")


@dataclass(frozen=True)
class Field:
    """One credential or setting of an addon.

    ``default`` (settings only): the value a connection gets while the user has
    not set one. ``required`` (credentials only): connecting without it is
    refused. ``type``: one of :data:`FIELD_TYPES` (what the value must look
    like and who may set it).

    What the Addons page shows beside the input, for someone setting it up:
    ``label`` (its short name, "Access token"; default: the name in words),
    ``help`` (where to find the value, one sentence of plain words: "In Home
    Assistant: your profile → Security → Long-lived access tokens → Create
    token"), ``url`` (an ``https://`` page where it is found or made, shown as
    a link) and ``placeholder`` (an example value inside the empty input).
    ``description`` stays what the value is for.
    """
    name: str
    description: str = ""
    default: Any = None
    required: bool = True
    type: str = TEXT
    label: str = ""
    help: str = ""
    url: str = ""
    placeholder: str = ""

    def __post_init__(self) -> None:
        if self.type not in FIELD_TYPES:
            raise ValueError(f"Field {self.name!r} has unknown type {self.type!r}")
        owner = f"Field {self.name!r}"
        _short(owner, "label", self.label, MAX_LABEL)
        _short(owner, "help", self.help, MAX_HELP)
        _short(owner, "placeholder", self.placeholder, MAX_PLACEHOLDER)
        _https_url(owner, self.url)

    @property
    def title(self) -> str:
        """Its name for people: ``label``, else the name in words ("api_key" → "Api key")."""
        return self.label or self.name.replace("_", " ").capitalize()

    @property
    def secret(self) -> bool:
        """As a credential: its value is write-only (only whether it is set is
        ever returned). Every credential is, except an address: an address is
        not a secret, so it is returned to the connection's own user (and to
        an admin acting for them) like a setting. Storage is the same for
        both (encrypted with the other credentials)."""
        return self.type != ADDRESS


@dataclass
class Tool:
    """A registered tool: its addon, function, schema and approval flags."""
    name: str
    addon: str
    func: Callable[..., Any]
    schema: Dict[str, Any]
    is_write: bool
    confirm: ConfirmRule
    acts_outside: bool
    needs: Tuple[str, ...]
    available: Optional[AvailabilityRule] = None
    untrusted_result: bool = False
    describe: Optional[DescribeRule] = None
    shortcut: ConfirmRule = False
    shortcut_prep: bool = False
    shortcut_describe: Optional[Callable[[Optional[User], Dict[str, Any]], str]] = None

    @property
    def addons(self) -> Tuple[str, ...]:
        """Every addon a user needs connected to have this tool."""
        return (self.addon, *self.needs)


_addons: Dict[str, "Addon"] = {}
_tools: Dict[str, Tool] = {}
# The fixed addon order (set by the loader); addons declared later (tests)
# follow in declaration order.
_order: Tuple[str, ...] = ()
# Bumped whenever an addon or tool is registered (tool list caches key on it).
_version = 0


def _bump() -> None:
    global _version
    _version += 1


def version() -> int:
    return _version


class Addon:
    """An addon: what connecting needs, the user's settings, who may turn it
    on, the tools it owns and its hooks (../docs/addons.md, "The manifest").

    Args:
        name: Unique id (``"homeassistant"``), the folder's name.
        title: Its name for people ("Home Assistant"); default: the name, capitalised.
        description: What it does, in a sentence.
        version: Its own version ("1.0.0").
        needs: What running it takes, a :class:`~core.addons.hooks.Need`
            each: env values, accounts, hardware, the phone app, a region,
            other addons (required or ``optional``). The required addon needs
            are :attr:`depends`: loaded (and their migrations run) before it,
            on for a user before it is active for them; a site / installed
            addon whose required addons are missing is not loaded.
        touches: What it may touch beyond Dana's own data
            (:class:`~core.addons.hooks.Touches`: hosts, acting for the user
            outside Dana, stored credentials, other addons' data). An addon
            with ``credentials`` must say it stores them.
        credentials: Fields needed to connect, stored encrypted.
        settings: Per-user choices, stored as plain JSON (defaults allowed).
        default_on: New users get it turned on (it must need no credentials):
            :func:`core.addons.store.connect_defaults`.
        everyone: Every user has it, with no row and no way to turn it off
            (the ``essentials`` addon).
        requires: An :class:`core.access.AccessLevel` capability the user's
            level must have to connect it (``"ops"``), or None.
        prompt_parts: The addon's pieces of the agent's fixed instructions
            (:class:`PromptPart`), included only for a user who has the addon
            (:func:`core.addons.registry.prompt_parts`); the slot must be one
            of ``core.agent.SLOTS`` (checked when loading).
        llm_jobs: Its LLM purposes and their default model profile
            (``{"task_emoji": "fast"}``, or an :class:`~core.addons.hooks.LlmJob`
            with a label): ``core.llm`` accepts them as ``purpose=``.
        web: Its pages in the web app, any number: a tuple of
            :class:`~core.addons.hooks.Page` (files served from its ``web/``;
            page ids unique across addons).
        worker: Its sidecar (:class:`~core.addons.hooks.Worker`): a published image, or built from its ``worker/``.
        modules: More modules of its folder the loader imports after
            ``addon.py`` and ``tools.py`` (dotted, relative: ``"calls.tools"``):
            where its other tools and hooks are declared. ``hooks.py`` is
            imported without being named.
        order: Its place among the official addons (lower first; others follow by name).
        on_connect: Called with the user right after the addon is connected
            for them (e.g. ``tasks`` gives them the default task lists); a
            failure is logged and does not undo the connection.
        on_credentials_change: Called with the user right after
            :func:`core.addons.store.update` changed the stored
            credentials (another address or account: ``whatsapp`` learns its
            account again); a failure is logged and does not undo the change.
        on_change: Called with the user right after any connect, update or
            disconnect of the addon for them (``money``: delete the user's
            money data once it is disconnected); a failure is logged.
        prompt_section: The addon's section of the agent's system prompt for
            a user (``about``: their facts), asked on every turn through
            :func:`core.addons.registry.prompt_section` only while
            the user has the addon connected. It must be cheap (cached), never
            block and return "" for nothing.
        test: An async function of a stored connection making one cheap
            request: None when the connection works, else a short reason
            (never a secret). ``POST /api/addons/{name}/test``.
        options: An async function of a stored connection returning values
            a client can offer for its settings, read from the user's own
            service. ``GET /api/addons/{name}/options``.
        change_requires: A capability the user who *makes* a change needs to
            connect, update or disconnect the addon, for themselves or anyone
            (``cameras``: ``manage_users``, so an admin decides who sees
            which cameras). Existing connections keep working for their user.
        validate: The addon's own rule over the values a change would store
            (:data:`SettingsRule`; ``shopping``: one list per Telegram group,
            the active list; ``whatsapp``: one user per WAHA session).
        blocked: A function of the user's connection returning why its tools
            cannot run for it right now, or None (``whatsapp``: the account
            its WAHA reports is held by another user's connection). Asked by
            :func:`core.addons.registry.execute` before each of the
            addon's tools; the reason becomes the tool's result
            (``"Error: <reason>"``), the tool function is not called.
        connect_url: A path of this server that connects the addon by a
            sign-in at the service instead of typed credentials (the Google
            addons: ``/api/google/connect``); clients offer it as a button.
        connect_label: That button's words ("Connect Telegram"); empty: the
            Google sign-in's.
        realtime: False keeps the addon's tools out of realtime voice calls,
            which have no Approve card and no rule for untrusted content
            (``gmail``): there they are neither offered nor run.
        icon: Its icon on the Addons page: a Lucide icon name (``"house"``).
        features: What it adds for the user, a short line each in plain words
            ("Turn lights and devices on and off"); empty: the first line of
            each tool's docstring.
        setup: Steps done outside Dana before connecting, in order, one
            sentence each ("Install the WAHA server", "Scan its QR code with
            WhatsApp"); the Addons page numbers them before its own steps.
        examples: Things to say to Dana once it is on ("Turn off the living
            room lights"): shown after connecting.
        disconnect_warning: What disconnecting deletes, in one sentence
            (``money``: the logins and transactions); empty: nothing is deleted.
        reconnect_fix: Where its Reconnect button goes when
            :func:`core.addons.store.needs_reconnect` says a connection's
            credentials stopped working (a :class:`~core.addons.hooks.Fix`);
            None: its own page on the Addons page (``Fix.connect``). ``money``
            sends it to the Money page, where each bank login is fixed.
        events: The events it emits (:class:`~core.addons.hooks.EventType`),
            offered to watches (``registry.event_types``).

    Hooks are declared with the decorators below (``@addon.job``,
    ``@addon.on``, ``@addon.now_cards``, ``@addon.live_refresh``…);
    docs/writing-an-addon.md has an example of each.
    """

    def __init__(self, name: str, *, title: str = "", description: str = "", version: str = "",
                 needs: Sequence[Need] = (), touches: Touches = Touches(),
                 credentials: Sequence[Field] = (), settings: Sequence[Field] = (),
                 default_on: bool = False, everyone: bool = False, requires: Optional[str] = None,
                 prompt_parts: Sequence[PromptPart] = (), llm_jobs: Optional[Mapping[str, Union[str, LlmJob]]] = None,
                 web: Sequence[Page] = (), worker: Optional[Worker] = None, modules: Sequence[str] = (),
                 order: Optional[int] = None,
                 on_connect: Optional[Callable[[User], None]] = None,
                 on_credentials_change: Optional[Callable[[User], None]] = None,
                 on_change: Optional[Callable[[User], None]] = None,
                 prompt_section: Optional[PromptSection] = None,
                 test: Optional[ConnectionTest] = None, options: Optional[SettingOptions] = None,
                 change_requires: Optional[str] = None, validate: Optional[SettingsRule] = None,
                 blocked: Optional[ConnectionBlock] = None, connect_url: Optional[str] = None,
                 connect_label: str = "", realtime: bool = True, icon: str = "", features: Sequence[str] = (),
                 setup: Sequence[str] = (), examples: Sequence[str] = (), disconnect_warning: str = "",
                 reconnect_fix: Optional[Fix] = None, events: Sequence[EventType] = ()) -> None:
        if not name.isidentifier():
            raise ValueError(f"Addon {name!r} must be an identifier")
        owner = f"Addon {name!r}"
        if icon and not ICON.match(icon):
            raise ValueError(f"{owner}: icon {icon!r} must be a Lucide icon name (lower-case words joined by -)")
        for what, lines in (("features", features), ("setup", setup), ("examples", examples)):
            if isinstance(lines, str):
                raise ValueError(f"{owner}: {what} must be a tuple of strings, not one string")
            if len(lines) > MAX_LINES:
                raise ValueError(f"{owner}: {what} has {len(lines)} lines; keep it to {MAX_LINES}")
            for line in lines:
                if not isinstance(line, str) or not line.strip():
                    raise ValueError(f"{owner}: every line of {what} must be words")
                _short(owner, f"a line of {what}", line, MAX_LINE)
        _short(owner, "disconnect_warning", disconnect_warning, MAX_LINE)
        if name in _addons:
            raise ValueError(f"Addon {name!r} is declared twice")
        for capability in (requires, change_requires):
            if capability is not None and capability not in CAPABILITIES:
                raise ValueError(f"Addon {name!r} requires unknown capability {capability!r}")
        if default_on and any(f.required for f in credentials):
            raise ValueError(f"Addon {name!r} is default_on but needs credentials")
        if isinstance(web, Page) or not all(isinstance(p, Page) for p in web):
            raise TypeError(f"Addon {name!r}: web= takes a tuple of Page: web=(Page(...),)")
        page_ids = [p.id for p in web]
        if len(set(page_ids)) != len(page_ids):
            raise ValueError(f"Addon {name!r} declares a page id twice: {page_ids}")
        if isinstance(needs, Need) or not all(isinstance(n, Need) for n in needs):
            raise TypeError(f"{owner}: needs= takes a tuple of Need: needs=(Need(\"addon\", \"tasks\", \"why\"),)")
        seen = [(n.kind, n.what) for n in needs]
        if len(set(seen)) != len(seen):
            raise ValueError(f"{owner}: needs names one thing twice")
        if (ADDON_NEED, name) in seen:
            raise ValueError(f"{owner}: needs itself")
        if not isinstance(touches, Touches):
            raise TypeError(f"{owner}: touches= takes a Touches(...)")
        if credentials and not touches.stores_credentials:
            raise ValueError(f"{owner}: it declares credentials, so touches= must say stores_credentials=True")
        if reconnect_fix is not None and (not isinstance(reconnect_fix, Fix) or reconnect_fix.kind == "server"):
            raise TypeError(f"{owner}: reconnect_fix= takes a Fix that opens something (connect, screen or link)")
        self.name = name
        self.title = title or name.replace("_", " ").capitalize()
        self.description = description
        self.version = version
        self.needs: Tuple[Need, ...] = tuple(needs)
        self.touches = touches
        # The addons it cannot work without: loaded first, on for a user before it is active for them.
        self.depends: Tuple[str, ...] = tuple(n.what for n in needs if n.kind == ADDON_NEED and not n.optional)
        self.credentials: Tuple[Field, ...] = tuple(credentials)
        self.settings: Tuple[Field, ...] = tuple(settings)
        self.default_on = default_on
        self.requires = requires
        self.everyone = everyone
        self.on_connect = on_connect
        self.on_credentials_change = on_credentials_change
        self.on_change = on_change
        self.prompt_section = prompt_section
        self.prompt_parts: Tuple[PromptPart, ...] = tuple(prompt_parts)
        self.llm_jobs: Dict[str, LlmJob] = {k: v if isinstance(v, LlmJob) else LlmJob(v)
                                            for k, v in (llm_jobs or {}).items()}
        self.web: Tuple[Page, ...] = tuple(web)
        self.worker = worker
        self.modules: Tuple[str, ...] = tuple(modules)
        self.order = order
        self.test = test
        self.options = options
        self.change_requires = change_requires
        self.validate = validate
        self.blocked = blocked
        self.connect_url = connect_url
        self.connect_label = connect_label
        self.realtime = realtime
        self.icon = icon
        self.features: Tuple[str, ...] = tuple(features)
        self.setup: Tuple[str, ...] = tuple(setup)
        self.examples: Tuple[str, ...] = tuple(examples)
        self.disconnect_warning = disconnect_warning
        self.reconnect_fix: Fix = reconnect_fix or Fix(CONNECT, name, "Reconnect")
        # The events it emits, declared for the watches engine (registry.event_types).
        self.events: Tuple[EventType, ...] = tuple(events)
        self.tools: List[Tool] = []
        self.jobs: List[Job] = []
        self.services: List[Service] = []
        self.handlers: List[Handler] = []
        self.channels: List[Channel] = []
        self.hooks: List[Hook] = []
        _addons[name] = self
        _bump()

    def __repr__(self) -> str:
        return f"Addon({self.name!r})"

    def setting_defaults(self) -> Dict[str, Any]:
        return {f.name: f.default for f in self.settings}

    def allowed_for(self, user: User) -> bool:
        """The user's access level lets them connect this addon."""
        return self.requires is None or user.level.allows(self.requires)

    def changeable_by(self, actor: Optional[User]) -> bool:
        """``actor`` may connect / update / disconnect this addon (for anyone);
        None = the server itself or an operator with shell access."""
        return actor is None or self.change_requires is None or actor.level.allows(self.change_requires)

    def feature_lines(self) -> Tuple[str, ...]:
        """What it adds, for people: ``features``, else the first docstring line of its first tools."""
        if self.features:
            return self.features
        lines = ((inspect.getdoc(t.func) or "").strip().splitlines() for t in self.tools)
        return tuple(dict.fromkeys(first[0].strip() for first in lines if first and first[0].strip()))[:MAX_LINES]

    def field(self, name: str) -> Optional[Field]:
        """The credential or setting called ``name``."""
        return next((f for f in (*self.credentials, *self.settings) if f.name == name), None)

    def tool(self, name: Optional[str] = None, *, is_write: bool = False, confirm: ConfirmRule = False,
             acts_outside: bool = False, needs: Sequence[str] = (), available: Optional[AvailabilityRule] = None,
             untrusted_result: bool = False, describe: Optional[DescribeRule] = None,
             shortcut: ConfirmRule = False, shortcut_prep: bool = False,
             shortcut_describe: Optional[Callable[[Optional[User], Dict[str, Any]], str]] = None
             ) -> Callable[[F], F]:
        """Register a function as a tool of this addon. The function's first
        parameter is ``conn`` (the user's connection), never in the schema.

        Args:
            name: Tool name (defaults to the function name).
            is_write: The tool changes something (data, devices, other people's
                inboxes). Informational on its own: a write tool runs at once
                and registers an undo where it can (``core.undo``).
            confirm: The agent suspends and asks the user before running it
                (Approve / Deny). Only for tools that send something to another
                person or destroy something. Either a bool, or a callable that
                gets the call's arguments and returns whether this particular
                call needs asking (e.g. add_event only when it invites people).
                Implies ``is_write``.
            acts_outside: The tool acts on the world outside Dana and cannot be
                undone (home devices, the TV, the cluster, running code), or it
                writes something that feeds every later prompt (what Dana
                remembers about the user). It runs at once normally, but asks
                first in a turn that carries text other people wrote (the
                phone screen the user shared, an ``untrusted_result`` tool's
                result, a context or message framed by
                ``core.text.untrusted``): see
                :func:`core.addons.registry.needs_confirmation`.
            needs: Other addons the tool also requires; it is offered and runs
                only for a user who has all of them connected.
            available: A function of the user, asked on top of the addons
                both when listing and when executing: the tool is offered and
                runs only while it returns True (``share_item``: more than one
                user on the server). It must be cheap (asked for every tool
                list) and never raise.
            untrusted_result: The tool's result carries text written by other
                people (an email, a chat message, a notification, a web page).
                The model reads the result framed by ``core.text.untrusted``
                (unless the tool framed it itself), and once it ran the rest
                of that turn counts as carrying untrusted content: every
                ``acts_outside`` tool asks first (``core/agent.py``).
            describe: The Approve card's words for one call: ``f(user,
                args)`` returning a :class:`~core.addons.hooks.Approval`
                (``core/approval_text.py`` uses it before its own
                descriptions; when it raises, the default card is shown).
            shortcut: Calls of this tool may become learned phrases
                (core/shortcuts.py): a bool, or a function of the call's
                arguments saying whether this call may.
            shortcut_prep: A read-only lookup allowed before the shortcut's call
                in a turn that teaches a phrase.
            shortcut_describe: ``f(user, args) -> str``: what a learned phrase
                for this call does, in words (default: the name and arguments).

        Re-registering a name in the same addon replaces that tool in place
        (tests swap a tool for a fake this way); a name owned by another addon
        is refused.
        """
        needs_t = tuple(needs)

        def decorator(func: F) -> F:
            tool_name = name or func.__name__
            existing = _tools.get(tool_name)
            if existing is not None and existing.addon != self.name:
                raise ValueError(f"Tool {tool_name!r} already belongs to the {existing.addon!r} addon")
            registered = Tool(name=tool_name, addon=self.name, func=func, schema=generate_schema(tool_name, func),
                              is_write=bool(is_write or confirm), confirm=confirm, acts_outside=bool(acts_outside),
                              needs=needs_t, available=available, untrusted_result=bool(untrusted_result),
                              describe=describe, shortcut=shortcut, shortcut_prep=bool(shortcut_prep),
                              shortcut_describe=shortcut_describe)
            if existing is not None:
                self.tools[self.tools.index(existing)] = registered
                logger.info(f"Replaced tool: {tool_name} ({self.name})")
            else:
                self.tools.append(registered)
            _tools[tool_name] = registered
            _bump()
            logger.debug(f"Registered tool: {tool_name} ({self.name}, is_write={registered.is_write}, "
                         f"confirm={bool(confirm)})")
            return func
        return decorator

    # --- Background work --------------------------------------------------------

    def job(self, *, every: Optional[datetime.timedelta] = None, daily: Optional[str] = None,
            at_start: bool = False, delay: float = 0.0, timeout: float = 600.0,
            name: Optional[str] = None) -> Callable[[F], F]:
        """``async def f(user)`` run for every user who has this addon, each as
        that user (``users.acting_as``), one failing user never stopping the
        next (core/addons/scheduler.py). Exactly one of ``every`` (an interval:
        ``minutes(5)``; a restart waits out what is left of it), ``daily``
        ("HH:MM", local time; once a day, also on the day the server starts after
        that time, never twice on one day across restarts) or ``at_start`` (once,
        when the server starts). ``delay``: seconds after the start before its
        first pass. ``timeout``: seconds one user's run may take before it is
        cancelled. Each pass writes a heartbeat (:func:`core.addons.scheduler.last_run`)."""
        if sum(bool(x) for x in (every, daily, at_start)) != 1:
            raise ValueError(f"{self.name}: a job takes exactly one of every=, daily=, at_start=")
        if daily is not None and not DAILY.match(daily):
            raise ValueError(f"{self.name}: daily={daily!r} must be HH:MM")
        if every is not None and every.total_seconds() <= 0:
            raise ValueError(f"{self.name}: every= must be positive")
        if timeout <= 0:
            raise ValueError(f"{self.name}: timeout= must be positive")

        def decorator(func: F) -> F:
            _require_async(self.name, "job", func)
            self.jobs.append(Job(self.name, name or func.__name__, func, every=every, daily=daily,
                                 at_start=at_start, delay=delay, timeout=timeout))
            return func
        return decorator

    def service(self, func: F) -> F:
        """``async def f(app)``: a long-running task started once with the
        server (watchers, websockets), restarted with backoff when it ends or raises."""
        _require_async(self.name, "service", func)
        self.services.append(Service(self.name, func.__name__, func))
        return func

    def on(self, event: str) -> Callable[[F], F]:
        """``async def f(user, data)`` called in the background, as ``user``,
        whenever any addon or core emits ``event`` for a user who has this addon
        (core/events.py). A failure is logged."""
        if not EVENT_NAME.match(event):
            raise ValueError(f"{self.name}: event {event!r} must look like <addon>.<thing>.<what happened>")

        def decorator(func: F) -> F:
            _require_async(self.name, "event handler", func)
            self.handlers.append(Handler(self.name, event, func))
            return func
        return decorator

    def channel(self, name: str) -> Callable[[F], F]:
        """``async def f(user, message) -> bool``: a delivery channel of
        :func:`core.notify.send` for users who have this addon; True when it
        delivered the message (core/notify.py)."""
        def decorator(func: F) -> F:
            _require_async(self.name, "channel", func)
            self.channels.append(Channel(self.name, name, func))
            return func
        return decorator

    # --- Composer hooks (plain functions, see SYNC_HOOKS) ------------------------

    def _hook(self, kind: str, key: str = "", **options: Any) -> Callable[[F], F]:
        def decorator(func: F) -> F:
            if kind in SYNC_HOOKS and inspect.iscoroutinefunction(func):
                raise TypeError(f"{self.name}: @addon.{kind} takes a plain function (composers call it in a "
                                f"worker thread), not {func.__name__!r} (async)")
            if kind not in SYNC_HOOKS and kind not in FLEX_HOOKS:
                _require_async(self.name, kind, func)
            if key and any(h.kind == kind and h.key == key for h in self.hooks):
                raise ValueError(f"{self.name}: @addon.{kind}({key!r}) is declared twice")
            self.hooks.append(Hook(self.name, kind, func, key, tuple(sorted(options.items()))))
            return func
        return decorator

    def now_cards(self, func: Optional[F] = None, *, order: int = 50) -> Any:
        """``def f(user, ctx: NowContext) -> list[Card]``: cards on the Now screen.
        Usable as ``@addon.now_cards`` or ``@addon.now_cards(order=20)``."""
        dec = self._hook("now_cards", order=order)
        return dec(func) if func is not None else dec

    def day_items(self, func: F) -> F:
        """``def f(user, ctx: NowContext) -> list[DayItem]``: items in the Now day strip."""
        return self._hook("day_items")(func)

    def now_action(self, prefix: str) -> Callable[[F], F]:
        """``async def f(user, ref, action, args, now) -> dict``: acting on this
        addon's Now cards whose id is ``<prefix>:<ref>`` (``POST /api/now/act``);
        raise ``core.cards.ActError`` to refuse, return ``core.cards.result(...)``.
        The user must have this addon (403 ``not_connected`` otherwise)."""
        return self._hook("now_action", prefix)

    def briefing(self, key: str, *, counts: Sequence[str] = (), default: Any = _MISSING,
                 rank: int = 50) -> Callable[[F], F]:
        """``def f(user, ctx: BriefingContext) -> Section | None`` (or async): this addon's
        section of the morning briefing, stored under ``key`` in the briefing's
        data (unique across addons). ``counts``: the keys of ``Section.counts``
        it may give the briefing's push (unique across addons; others are dropped).
        ``default``: stored under ``key`` when the addon is not active for a user
        or returns None. ``rank``: orders plain-briefing points (lower first)."""
        if not key:
            raise ValueError(f"{self.name}: @addon.briefing needs a section key")
        opts: Dict[str, Any] = {"counts": tuple(counts), "rank": rank}
        if default is not _MISSING:
            opts["has_default"] = True
            opts["default"] = default
        return self._hook("briefing", key, **opts)

    def review(self, key: str) -> Callable[[F], F]:
        """``def f(user, ctx: BriefingContext) -> Section | None``: this addon's
        section of the weekly review (``key`` unique across addons): its
        ``items`` are the decisions, acted on by its ``@addon.review_action``,
        its ``data`` joins the review's facts."""
        if not key:
            raise ValueError(f"{self.name}: @addon.review needs a section key")
        return self._hook("review", key)

    def review_action(self, item_type: str, actions: Sequence[str], *, primary: bool = True) -> Callable[[F], F]:
        """``async def f(user, item, action, decision, today) -> str``: applying
        a weekly-review item of ``item_type`` (``actions``: what the model may
        propose for it; ``keep`` is always allowed). ``primary=False`` marks a
        type that enriches a review without making a user eligible on its own.
        Returns the result's words."""
        return self._hook("review_action", item_type, actions=tuple(actions), primary=primary)

    def search(self, func: F) -> F:
        """``def f(user, query, limit) -> list[Hit]``: at most ``limit`` results in
        the global search (``core.addons.helpers.match_score`` / ``like_pattern`` help match)."""
        return self._hook("search")(func)

    def selfcheck(self, key: str, label: str, *, server: bool = False, after: str = "") -> Callable[[F], F]:
        """``def f(user, now) -> Check``: a line of the self-checks
        (``/api/checks``) for users who have this addon; ``server=True``: a check
        of the server itself, shown to users who may see everyone instead.
        ``after``: the check it is listed after (default: at the end)."""
        return self._hook("selfcheck", key, label=label, server=server, after=after)

    def fix(self, key: str) -> Callable[[F], F]:
        """``async def f(user, action) -> str``: carries out the server action
        a failing check ``key`` of this addon offers (its ``Check.fix`` is
        ``Fix.run(action, label)``) for ``user``, who pressed its button;
        returns the result's words. Raise ``ValueError`` to refuse (the client
        shows the message). Only called while that check fails for that user
        and offers ``action``."""
        return self._hook("fix", key)

    def context(self, kind: str) -> Callable[[F], F]:
        """``def f(user, ref_id, req: ContextRequest) -> str | None``: the agent's
        context for a chat opened from this addon's item of ``kind``."""
        return self._hook("context", kind)

    def context_card(self, kind: str) -> Callable[[F], F]:
        """``def f(user, ref_id) -> dict``: the card attached to a chat opened
        from this addon's item of ``kind``; raise LookupError when it is not theirs."""
        return self._hook("context_card", kind)

    def context_block(self, kind: str) -> Callable[[F], F]:
        """``def f(user, ref_id) -> str``: a plain description of this addon's
        item of ``kind`` for another addon's context prompt (e.g. a task's source call)."""
        return self._hook("context_block", kind)

    def demo(self, func: F) -> F:
        """``def f(user, ctx: DemoContext) -> int``: write this addon's sample
        data for ``user``, a member of the demo household (core/demo.py), and
        return how many rows it wrote. Only into tables whose rows go with the
        user (``user_id ... ON DELETE CASCADE``, or rows of such rows): removing
        the demo deletes its users, and nothing else is cleaned up. Never
        through the model or an outside service."""
        return self._hook("demo")(func)

    def widget(self, key: str, *, title: str, script: str, sizes: Sequence[str] = WIDGET_SIZES,
               settings: Sequence[Field] = (), description: str = "", icon: str = "",
               refresh: int = 60) -> Callable[[F], F]:
        """``def f(user, ctx: WidgetContext) -> dict`` (or async): the data of this
        addon's dashboard widget ``key`` (unique across addons) for ``user``, as
        JSON. Offered on the dashboards of users who have the addon; on one of a
        user who does not, it answers ``not_connected``. ``script``: the file of
        the addon's ``web/`` folder that draws it, registering with
        ``Dana.widget(key, …)`` (static/dana.js). ``sizes``: the sizes it can be
        drawn at, of ``small`` / ``medium`` / ``large`` (the first is its
        default). ``settings``: what each placed widget lets the user choose
        (:class:`Field`; values are text, the default used while one is
        empty). ``refresh``: seconds between its data refreshes on a dashboard
        (0: only when the page loads or its script asks)."""
        owner = f"{self.name}: @addon.widget({key!r})"
        if not WIDGET_KEY.match(key or ""):
            raise ValueError(f"{owner}: the key must be lower-case letters, digits and _, starting with a letter")
        if not title or len(title) > MAX_LABEL:
            raise ValueError(f"{owner}: title must be words, up to {MAX_LABEL} characters")
        _short(owner, "description", description, MAX_LINE)
        if not WIDGET_SCRIPT.match(script or ""):
            raise ValueError(f"{owner}: script must be a .js file of its web/ folder (\"widget.js\")")
        if isinstance(sizes, str) or not sizes or len(set(sizes)) != len(sizes) \
                or any(s not in WIDGET_SIZES for s in sizes):
            raise ValueError(f"{owner}: sizes must name some of {WIDGET_SIZES}, each once")
        if isinstance(settings, Field) or not all(isinstance(f, Field) for f in settings):
            raise TypeError(f"{owner}: settings= takes a tuple of Field")
        names = [f.name for f in settings]
        if len(set(names)) != len(names) or len(names) > MAX_WIDGET_SETTINGS:
            raise ValueError(f"{owner}: settings must have distinct names, at most {MAX_WIDGET_SETTINGS}")
        if any(f.type != TEXT for f in settings):
            raise ValueError(f"{owner}: a widget setting is plain text (type TEXT)")
        if icon and not ICON.match(icon):
            raise ValueError(f"{owner}: icon {icon!r} must be a Lucide icon name")
        if not isinstance(refresh, int) or refresh < 0 or 0 < refresh < 10:
            raise ValueError(f"{owner}: refresh is 0 or at least 10 seconds")
        return self._hook("widget", key, title=title, script=script, sizes=tuple(sizes), settings=tuple(settings),
                          description=description, icon=icon, refresh=refresh)

    def provide(self, key: str, *, rank: int = 50, source: str = "", label: str = "") -> Callable[[F], F]:
        """Register a named capability ``key`` (sync or async) that core composers
        look up through :func:`core.addons.registry.providers` without importing the addon.

        Several addons may provide the same key: they come ranked, lower
        ``rank`` first (ties in the fixed addon order), and a caller takes the
        first that answers (:func:`core.addons.registry.answer`). ``source`` /
        ``label``: who answers, when the key is a signal's (core/signals.py:
        ``"phone"`` / ``"Dana's phone app"``); default the addon's name and title."""
        if not key:
            raise ValueError(f"{self.name}: @addon.provide needs a key")
        if source and not re.match(r"^[a-z][a-z0-9_]*$", source):
            raise ValueError(f"{self.name}: @addon.provide source {source!r} must be lower case letters, digits, _")
        _short(self.name, "a provider label", label, MAX_LABEL)
        return self._hook("provide", key, rank=int(rank), source=source or self.name, label=label or self.title)


    # --- Live activities (core/live.py, docs/live.md) -----------------------------

    def _once(self, kind: str) -> None:
        if any(h.kind == kind for h in self.hooks):
            raise ValueError(f"{self.name}: @addon.{kind} is declared twice (one per addon)")

    def live_refresh(self, *, every: int = 60) -> Callable[[F], F]:
        """``async def f(user, current: live.Current) -> live.HookResult``: keeps
        this addon's running live activities current (``from core import live``,
        server/docs/live.md). Dana calls it for each of them, as their user and
        only while the addon is active for them, when it is due: at the
        activity's ``refresh_at`` when it set one later than the last call, else
        ``every`` seconds (at least 5) after the last call or the start. An
        ``Activity`` updates it (one that changes nothing on the wire is
        dropped), ``live.END`` ends it, None leaves it as it is. A failure (or
        a call over 30 s) is logged and never ends it; each call runs on its
        own, never two at once for one activity, so a slow one holds up no
        other. One per addon."""
        if isinstance(every, bool) or not isinstance(every, (int, float)) or every < MIN_LIVE_EVERY:
            raise ValueError(f"{self.name}: @addon.live_refresh(every=) is seconds, at least {MIN_LIVE_EVERY}")

        def decorator(func: F) -> F:
            self._once("live_refresh")
            return self._hook("live_refresh", every=every)(func)
        return decorator

    def live_action(self, func: F) -> F:
        """``async def f(user, current: live.Current, action, choice) -> live.HookResult``:
        a person acted on one of this addon's live activities (the
        ``/api/live/{id}`` routes: act, select, stop). ``live.HookResult`` is a
        ``live.Activity`` (update it), ``live.END`` (end it) or None (leave it).
        ``action`` is a ``server`` :class:`~core.live.Action`'s id (``choice``:
        the choice it was pressed on), ``"select"`` once Dana stored a new
        selection (``choice``: its key), or ``"stop"`` once Dana ended the
        activity (clean up your own rows; the return value is ignored). A
        failure is logged. One per addon; only for users who have the addon active."""
        self._once("live_action")
        return self._hook("live_action")(func)


def _require_async(addon: str, what: str, func: Callable[..., Any]) -> None:
    if not inspect.iscoroutinefunction(func):
        raise TypeError(f"{addon}: a {what} must be an async function ({func.__name__!r} is not)")


def set_order(order: Sequence[str]) -> None:
    """Fix the addon order: every declared addon, each once.

    Raises:
        ValueError: ``order`` misses a declared addon, repeats one or names an unknown one.
    """
    global _order
    if len(set(order)) != len(order) or set(order) != set(_addons):
        raise ValueError(f"The addon order {list(order)} must list each declared addon once: {sorted(_addons)}")
    _order = tuple(order)
    _bump()


def forget(name: str) -> None:
    """Remove a declared addon and its tools (the loader, for a site or
    installed addon that failed to load after declaring itself)."""
    addon = _addons.pop(name, None)
    if addon is None:
        return
    for t in addon.tools:
        if _tools.get(t.name) is t:
            del _tools[t.name]
    global _order
    _order = tuple(n for n in _order if n != name)
    _bump()


def all_addons() -> List[Addon]:
    """Every declared addon, in the fixed order."""
    rank = {k: i for i, k in enumerate(_order)}
    declared = list(_addons)
    return sorted(_addons.values(), key=lambda k: (rank.get(k.name, len(rank)), declared.index(k.name)))


def get_addon(name: str) -> Addon:
    """The addon called ``name``.

    Raises:
        KeyError: no such addon.
    """
    try:
        return _addons[name]
    except KeyError:
        raise KeyError(f"Unknown addon {name!r}") from None


def find_tool(name: str) -> Optional[Tool]:
    return _tools.get(name)


def all_tools() -> List[Tool]:
    """Every tool of every addon, in the fixed order."""
    return [t for k in all_addons() for t in k.tools]


def needs_problem(addon: Addon, known: Collection[str]) -> Optional[str]:
    """Why ``addon``'s tools or prompt parts cannot be used (one ``needs`` an
    addon not in ``known``), or None."""
    for t in addon.tools:
        unknown = [k for k in t.needs if k not in known]
        if unknown:
            return f"tool {t.name!r} needs {', '.join(unknown)}, which is not loaded"
    for part in addon.prompt_parts:
        unknown = [n for n in part.needs if n not in known]
        if unknown:
            return f"a prompt part needs {', '.join(unknown)}, which is not loaded"
    return None


def key_clash(addon: Addon, others: Sequence[Addon]) -> Optional[str]:
    """Why ``addon`` cannot load next to ``others`` (the addons already loaded):
    it declares a page id, a keyed hook (:data:`KEYED_HOOKS`) key or a briefing
    count key another addon has; None when every key is its own."""
    pages = {p.id: o.name for o in others for p in o.web}
    for p in addon.web:
        if p.id in pages:
            return f"the page id {p.id!r} is already the {pages[p.id]} addon's"
    taken = {(h.kind, h.key): o.name for o in others for h in o.hooks if h.kind in KEYED_HOOKS}
    counts = {c: o.name for o in others for h in o.hooks if h.kind == "briefing" for c in h.option("counts", ())}
    for h in addon.hooks:
        if h.kind in KEYED_HOOKS and (h.kind, h.key) in taken:
            return f"@addon.{h.kind}({h.key!r}) is already declared by the {taken[(h.kind, h.key)]} addon"
        if h.kind == "briefing":
            for c in h.option("counts", ()):
                if c in counts:
                    return f"the briefing count {c!r} is already declared by the {counts[c]} addon"
    return None


def check_values(addon: Addon, credentials: Optional[Mapping[str, Any]],
                 settings: Optional[Mapping[str, Any]]) -> None:
    """Refuse credential / setting names the addon does not declare.

    Raises:
        ValueError: an unknown name.
    """
    for label, given, declared in (("credential", credentials, addon.credentials),
                                   ("setting", settings, addon.settings)):
        names = {f.name for f in declared}
        unknown = sorted(set(given or {}) - names)
        if unknown:
            raise ValueError(f"The {addon.name} addon has no {label} {', '.join(unknown)}"
                             f" (known: {', '.join(sorted(names)) or 'none'})")
