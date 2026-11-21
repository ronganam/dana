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
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, TypeVar, Union

from core.access import CAPABILITIES
from core.users import User
from core.addons.hooks import Channel, Handler, Hook, Job, LlmJob, Service, Web, Worker
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
# The Approve card of one call of a tool: the call's arguments → a
# core.addons.hooks.Approval (typed Any: tools import hooks lazily-free).
DescribeRule = Callable[[Dict[str, Any]], Any]
# Event names: <addon>.<thing>.<what happened> (core: core.<thing>.<what>).
EVENT_NAME = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+\.[a-z0-9_]+$")
DAILY = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# Composer hooks: their functions are plain (not async): composers call them in
# a worker thread, next to their own database reads.
SYNC_HOOKS = ("now_cards", "day_items", "briefing", "search", "selfcheck", "context", "context_card")


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


@dataclass(frozen=True)
class Field:
    """One credential or setting of an addon.

    ``default`` (settings only): the value a connection gets while the user has
    not set one. ``required`` (credentials only): connecting without it is
    refused. ``type``: one of :data:`FIELD_TYPES` (what the value must look
    like and who may set it).
    """
    name: str
    description: str = ""
    default: Any = None
    required: bool = True
    type: str = TEXT

    def __post_init__(self) -> None:
        if self.type not in FIELD_TYPES:
            raise ValueError(f"Field {self.name!r} has unknown type {self.type!r}")

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
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def addons(self) -> Tuple[str, ...]:
        """Every addon a user needs connected to have this tool."""
        return (self.addon, *self.needs)


_addons: Dict[str, "Addon"] = {}
_tools: Dict[str, Tool] = {}
# The fixed addon order (set once by addons/__init__.py); addons declared
# later (tests) follow in declaration order.
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
        needs: Other addons it builds on: loaded (and their migrations run)
            before it; a site / installed addon whose ``needs`` are missing is
            not loaded.
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
        web: Its page in the web app (:class:`~core.addons.hooks.Web`): files served from its ``web/``.
        worker: Its sidecar (:class:`~core.addons.hooks.Worker`), built from its ``worker/``.
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
            disconnect of the addon for them (``homeassistant``: reconcile the
            entity watchers now); a failure is logged.
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

    Hooks are declared with the decorators below (``@addon.job``,
    ``@addon.on``, ``@addon.now_cards``…); docs/writing-an-addon.md has an
    example of each.
    """

    def __init__(self, name: str, *, title: str = "", description: str = "", version: str = "",
                 needs: Sequence[str] = (), credentials: Sequence[Field] = (), settings: Sequence[Field] = (),
                 default_on: bool = False, everyone: bool = False, requires: Optional[str] = None,
                 prompt_parts: Sequence[PromptPart] = (), llm_jobs: Optional[Mapping[str, Union[str, LlmJob]]] = None,
                 web: Optional[Web] = None, worker: Optional[Worker] = None, modules: Sequence[str] = (),
                 order: Optional[int] = None,
                 on_connect: Optional[Callable[[User], None]] = None,
                 on_credentials_change: Optional[Callable[[User], None]] = None,
                 on_change: Optional[Callable[[User], None]] = None,
                 prompt_section: Optional[PromptSection] = None,
                 test: Optional[ConnectionTest] = None, options: Optional[SettingOptions] = None,
                 change_requires: Optional[str] = None, validate: Optional[SettingsRule] = None,
                 blocked: Optional[ConnectionBlock] = None, connect_url: Optional[str] = None,
                 connect_label: str = "", realtime: bool = True) -> None:
        if not name.isidentifier():
            raise ValueError(f"Addon {name!r} must be an identifier")
        if name in _addons:
            raise ValueError(f"Addon {name!r} is declared twice")
        for capability in (requires, change_requires):
            if capability is not None and capability not in CAPABILITIES:
                raise ValueError(f"Addon {name!r} requires unknown capability {capability!r}")
        if default_on and any(f.required for f in credentials):
            raise ValueError(f"Addon {name!r} is default_on but needs credentials")
        self.name = name
        self.title = title or name.replace("_", " ").capitalize()
        self.description = description
        self.version = version
        self.needs: Tuple[str, ...] = tuple(needs)
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
        self.web = web
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

    @property
    def has_credentials(self) -> bool:
        return bool(self.credentials)

    def setting_defaults(self) -> Dict[str, Any]:
        return {f.name: f.default for f in self.settings}

    def allowed_for(self, user: User) -> bool:
        """The user's access level lets them connect this addon."""
        return self.requires is None or user.level.allows(self.requires)

    def changeable_by(self, actor: Optional[User]) -> bool:
        """``actor`` may connect / update / disconnect this addon (for anyone);
        None = the server itself or an operator with shell access."""
        return actor is None or self.change_requires is None or actor.level.allows(self.change_requires)

    def field(self, name: str) -> Optional[Field]:
        """The credential or setting called ``name``."""
        return next((f for f in (*self.credentials, *self.settings) if f.name == name), None)

    def tool(self, name: Optional[str] = None, *, is_write: bool = False, confirm: ConfirmRule = False,
             acts_outside: bool = False, needs: Sequence[str] = (), available: Optional[AvailabilityRule] = None,
             untrusted_result: bool = False, describe: Optional[DescribeRule] = None,
             **extra: Any) -> Callable[[F], F]:
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
                first in a turn that carries untrusted content (the phone
                screen the user shared, ``device_screen``): see
                :func:`core.addons.registry.needs_confirmation`.
            needs: Other addons the tool also requires; it is offered and runs
                only for a user who has all of them connected.
            available: A function of the user, asked on top of the addons
                both when listing and when executing: the tool is offered and
                runs only while it returns True (``share_item``: more than one
                user on the server). It must be cheap (asked for every tool
                list) and never raise.
            untrusted_result: The tool's result carries text written by other
                people (an email). Once it ran, the rest of that turn counts
                as carrying untrusted content: every ``acts_outside`` tool
                asks first (``core/agent.py``).
            describe: The Approve card's words for one call: a function of
                the call's arguments returning a
                :class:`~core.addons.hooks.Approval` (``core/approval_text.py``
                uses it before its own descriptions).

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
            for key, value in extra.items():
                # Unknown flags are kept (and logged) instead of breaking the import.
                logger.warning(f"Tool {tool_name}: unknown tool() flag {key}={value!r}")
            registered = Tool(name=tool_name, addon=self.name, func=func, schema=generate_schema(tool_name, func),
                              is_write=bool(is_write or confirm), confirm=confirm, acts_outside=bool(acts_outside),
                              needs=needs_t, available=available, untrusted_result=bool(untrusted_result),
                              describe=describe, extra=dict(extra))
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
            at_start: bool = False, delay: float = 0.0, name: Optional[str] = None) -> Callable[[F], F]:
        """``async def f(user)`` run for every user who has this addon, each as
        that user (``users.acting_as``), one failing user never stopping the
        next (core/addons/scheduler.py). Exactly one of ``every`` (an interval:
        ``minutes(5)``), ``daily`` ("HH:MM", local time; also run on the day the
        server starts after that time) or ``at_start`` (once, when the server
        starts). ``delay``: seconds after the start before its first pass. Each pass
        writes a heartbeat (:func:`core.addons.scheduler.last_run`)."""
        if sum(bool(x) for x in (every, daily, at_start)) != 1:
            raise ValueError(f"{self.name}: a job takes exactly one of every=, daily=, at_start=")
        if daily is not None and not DAILY.match(daily):
            raise ValueError(f"{self.name}: daily={daily!r} must be HH:MM")
        if every is not None and every.total_seconds() <= 0:
            raise ValueError(f"{self.name}: every= must be positive")

        def decorator(func: F) -> F:
            _require_async(self.name, "job", func)
            self.jobs.append(Job(self.name, name or func.__name__, func, every=every, daily=daily,
                                 at_start=at_start, delay=delay))
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
            if kind not in SYNC_HOOKS:
                _require_async(self.name, kind, func)
            if key and any(h.kind == kind and h.key == key for h in self.hooks):
                raise ValueError(f"{self.name}: @addon.{kind}({key!r}) is declared twice")
            self.hooks.append(Hook(self.name, kind, func, key, tuple(sorted(options.items()))))
            return func
        return decorator

    def now_cards(self, func: F) -> F:
        """``def f(user, ctx: NowContext) -> list[Card]``: cards on the Now screen."""
        return self._hook("now_cards")(func)

    def day_items(self, func: F) -> F:
        """``def f(user, ctx: NowContext) -> list[DayItem]``: items in the Now day strip."""
        return self._hook("day_items")(func)

    def now_action(self, prefix: str) -> Callable[[F], F]:
        """``async def f(user, ref, action, args, now) -> dict``: acting on this
        addon's Now cards whose id is ``<prefix>:<ref>`` (``POST /api/now/act``);
        raise ``core.cards.ActError`` to refuse, return ``core.cards.result(...)``.
        The user must have this addon (403 ``not_connected`` otherwise)."""
        return self._hook("now_action", prefix)

    def briefing(self, func: F) -> F:
        """``def f(user, ctx: BriefingContext) -> Section | None``: a section of
        the morning briefing or the weekly review (``ctx.kind``)."""
        return self._hook("briefing")(func)

    def review_action(self, item_type: str, actions: Sequence[str]) -> Callable[[F], F]:
        """``async def f(user, item, action, decision, today) -> str``: applying
        a weekly-review item of ``item_type`` (``actions``: what the model may
        propose for it; ``keep`` is always allowed). Returns the result's words."""
        return self._hook("review_action", item_type, actions=tuple(actions))

    def search(self, func: F) -> F:
        """``def f(user, query, limit) -> list[Hit]``: at most ``limit`` results in
        the global search (``core.search.match_score`` / ``like_pattern`` help match)."""
        return self._hook("search")(func)

    def selfcheck(self, key: str, label: str, *, server: bool = False, after: str = "") -> Callable[[F], F]:
        """``def f(user, now) -> Check``: a line of the self-checks
        (``/api/checks``) for users who have this addon; ``server=True``: a check
        of the server itself, shown to users who may see everyone instead.
        ``after``: the check it is listed after (default: at the end)."""
        return self._hook("selfcheck", key, label=label, server=server, after=after)

    def context(self, kind: str) -> Callable[[F], F]:
        """``def f(user, ref_id, req: ContextRequest) -> str | None``: the agent's
        context for a chat opened from this addon's item of ``kind``."""
        return self._hook("context", kind)

    def context_card(self, kind: str) -> Callable[[F], F]:
        """``def f(user, ref_id) -> dict``: the card attached to a chat opened
        from this addon's item of ``kind``; raise LookupError when it is not theirs."""
        return self._hook("context_card", kind)


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


def validate() -> None:
    """Every ``needs`` (of a tool or a prompt part) names a declared addon.

    Raises:
        ValueError: a tool or prompt part needs an unknown addon.
    """
    for t in _tools.values():
        unknown = [k for k in t.needs if k not in _addons]
        if unknown:
            raise ValueError(f"Tool {t.name!r} needs unknown addon(s) {unknown}")
    for k in _addons.values():
        for part in k.prompt_parts:
            unknown = [n for n in part.needs if n not in _addons]
            if unknown:
                raise ValueError(f"A prompt part of {k.name!r} needs unknown addon(s) {unknown}")


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
