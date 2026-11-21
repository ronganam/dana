"""Integration kinds and the tools they own (docs/access.md, "Integration").

A kind is declared once, in its package under ``integrations/``::

    whatsapp = Integration("whatsapp", credentials=(Field("url"), ...), settings=(...))

    @whatsapp.tool(confirm=True)
    async def send_whatsapp_message(conn, chat_id: str, text: str) -> str: ...

Declaring a kind registers it; kinds are kept in declaration order, which
``integrations/__init__.py`` fixes with explicit imports. That order, then the
order tools are defined in, is the order of every tool list.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, TypeVar, Union

from core.access import CAPABILITIES
from core.users import User
from integrations.framework.schema import generate_schema

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])
ConfirmRule = Union[bool, Callable[[Dict[str, Any]], bool]]
# Whether a tool is offered to a user beyond their connected kinds (e.g.
# share_item: only on a server with more than one user).
AvailabilityRule = Callable[[User], bool]
# A kind's part of the agent's system prompt for one user (About-you facts,
# the home map): text appended as is, "" for nothing.
PromptSection = Callable[[User], str]
# The three hooks below take the user's integrations.framework.store.Connection
# (typed Any here: store imports this module).
# Why the kind's tools cannot run for a connection right now, or None.
ConnectionBlock = Callable[[Any], Optional[str]]
# A cheap request that tells whether a stored connection works: None when it
# does, else a short reason without secrets.
ConnectionCheck = Callable[[Any], Awaitable[Optional[str]]]
# Values a client can offer for the kind's settings, read from the user's own
# service: {setting name: [{"value": ..., "label": ...}]}.
SettingOptions = Callable[[Any], Awaitable[Dict[str, List[Dict[str, Any]]]]]
# A kind's own rule over a connect / update: the user it is for, the values
# given (credentials and settings by name; None: removed / back to the
# default), the credentials and the settings after the change (defaults
# filled in) and who makes the change (None: the server itself / an operator
# with shell access). Raises InvalidValue to refuse.
SettingsRule = Callable[[User, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], Optional[User]], None]


@dataclass(frozen=True)
class PromptPart:
    """A piece of the agent's instructions that applies only to a user who has
    the kind (and every kind in ``needs``) connected.

    ``slot`` names the place in the instructions (core/agent.py lists the
    slots and how each joins its parts: a phrase in a list, a sentence, a
    paragraph); ``rank`` orders the parts of one slot (lower first; ties in
    the fixed kind order)."""
    slot: str
    text: str
    rank: int = 50
    needs: Tuple[str, ...] = ()


# Field types (docs/access.md, "Security rules"): what a value must look like
# and who may set it, checked by integrations.framework.store on connect / update.
TEXT = "text"
# A base URL the server sends requests to (integrations/framework/addresses.py):
# validated and stored normalised for everyone; a user without
# ``manage_users`` may only use an origin an admin allowed for the kind.
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
    """One credential or setting of a kind.

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
    """A registered tool: its kind, function, schema and approval flags."""
    name: str
    kind: str
    func: Callable[..., Any]
    schema: Dict[str, Any]
    is_write: bool
    confirm: ConfirmRule
    acts_outside: bool
    needs: Tuple[str, ...]
    available: Optional[AvailabilityRule] = None
    untrusted_result: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def kinds(self) -> Tuple[str, ...]:
        """Every kind a user needs connected to have this tool."""
        return (self.kind, *self.needs)


_kinds: Dict[str, "Integration"] = {}
_tools: Dict[str, Tool] = {}
# The fixed kind order (set once by integrations/__init__.py); kinds declared
# later (tests) follow in declaration order.
_order: Tuple[str, ...] = ()
# Bumped whenever a kind or tool is registered (tool list caches key on it).
_version = 0


def _bump() -> None:
    global _version
    _version += 1


def version() -> int:
    return _version


class Integration:
    """A kind of integration (one service): what connecting needs, the user's
    settings, the access capability it requires, and the tools it owns.

    Args:
        kind: Unique id (``"homeassistant"``).
        credentials: Fields needed to connect, stored encrypted.
        settings: Per-user choices, stored as plain JSON (defaults allowed).
        requires: An :class:`core.access.AccessLevel` capability the user's
            level must have to connect it (``"ops"``), or None.
        everyone: Every user has it, with no row and no way to disconnect
            (the ``core`` kind).
        description: What the service is, in a few words.
        on_connect: Called with the user right after the kind is connected
            for them (e.g. ``tasks`` gives them the default task lists); a
            failure is logged and does not undo the connection.
        on_credentials_change: Called with the user right after
            :func:`integrations.framework.store.update` changed the stored
            credentials (another address or account: ``whatsapp`` learns its
            account again); a failure is logged and does not undo the change.
        on_change: Called with the user right after any connect, update or
            disconnect of the kind for them (``homeassistant``: reconcile the
            entity watchers now); a failure is logged.
        prompt_section: The kind's section of the agent's system prompt for
            a user (``about``: their facts), asked on every turn through
            :func:`integrations.framework.registry.prompt_section` only while
            the user has the kind connected. It must be cheap (cached), never
            block and return "" for nothing.
        prompt_parts: The kind's pieces of the agent's fixed instructions
            (:class:`PromptPart`), included only for a user who has the kind
            (:func:`integrations.framework.registry.prompt_parts`).
        check: An async function of a stored connection making one cheap
            request: None when the connection works, else a short reason
            (never a secret). ``POST /api/integrations/{kind}/test``.
        options: An async function of a stored connection returning values
            a client can offer for its settings, read from the user's own
            service. ``GET /api/integrations/{kind}/options``.
        change_requires: A capability the user who *makes* a change needs to
            connect, update or disconnect the kind, for themselves or anyone
            (``cameras``: ``manage_users``, so an admin decides who sees
            which cameras). Existing connections keep working for their user.
        validate: The kind's own rule over the values a change would store
            (:data:`SettingsRule`; ``shopping``: one list per Telegram group,
            the active list; ``whatsapp``: one user per WAHA session).
        blocked: A function of the user's connection returning why its tools
            cannot run for it right now, or None (``whatsapp``: the account
            its WAHA reports is held by another user's connection). Asked by
            :func:`integrations.framework.registry.execute` before each of the
            kind's tools; the reason becomes the tool's result
            (``"Error: <reason>"``), the tool function is not called.
        connect_url: A path of this server that connects the kind by a
            sign-in at the service instead of typed credentials (the Google
            kinds: ``/api/google/connect``); clients offer it as a button.
        connect_label: That button's words ("Connect Telegram"); empty: the
            Google sign-in's.
        realtime: False keeps the kind's tools out of realtime voice calls,
            which have no Approve card and no rule for untrusted content
            (``gmail``): there they are neither offered nor run.
    """

    def __init__(self, kind: str, *, credentials: Sequence[Field] = (), settings: Sequence[Field] = (),
                 requires: Optional[str] = None, everyone: bool = False, description: str = "",
                 on_connect: Optional[Callable[[User], None]] = None,
                 on_credentials_change: Optional[Callable[[User], None]] = None,
                 on_change: Optional[Callable[[User], None]] = None,
                 prompt_section: Optional[PromptSection] = None, prompt_parts: Sequence[PromptPart] = (),
                 check: Optional[ConnectionCheck] = None, options: Optional[SettingOptions] = None,
                 change_requires: Optional[str] = None, validate: Optional[SettingsRule] = None,
                 blocked: Optional[ConnectionBlock] = None, connect_url: Optional[str] = None,
                 connect_label: str = "", realtime: bool = True) -> None:
        if not kind.isidentifier():
            raise ValueError(f"Integration kind {kind!r} must be an identifier")
        if kind in _kinds:
            raise ValueError(f"Integration kind {kind!r} is declared twice")
        for capability in (requires, change_requires):
            if capability is not None and capability not in CAPABILITIES:
                raise ValueError(f"Integration kind {kind!r} requires unknown capability {capability!r}")
        self.kind = kind
        self.description = description
        self.credentials: Tuple[Field, ...] = tuple(credentials)
        self.settings: Tuple[Field, ...] = tuple(settings)
        self.requires = requires
        self.everyone = everyone
        self.on_connect = on_connect
        self.on_credentials_change = on_credentials_change
        self.on_change = on_change
        self.prompt_section = prompt_section
        self.prompt_parts: Tuple[PromptPart, ...] = tuple(prompt_parts)
        self.check = check
        self.options = options
        self.change_requires = change_requires
        self.validate = validate
        self.blocked = blocked
        self.connect_url = connect_url
        self.connect_label = connect_label
        self.realtime = realtime
        self.tools: List[Tool] = []
        _kinds[kind] = self
        _bump()

    def __repr__(self) -> str:
        return f"Integration({self.kind!r})"

    @property
    def has_credentials(self) -> bool:
        return bool(self.credentials)

    def setting_defaults(self) -> Dict[str, Any]:
        return {f.name: f.default for f in self.settings}

    def allowed_for(self, user: User) -> bool:
        """The user's access level lets them connect this kind."""
        return self.requires is None or user.level.allows(self.requires)

    def changeable_by(self, actor: Optional[User]) -> bool:
        """``actor`` may connect / update / disconnect this kind (for anyone);
        None = the server itself or an operator with shell access."""
        return actor is None or self.change_requires is None or actor.level.allows(self.change_requires)

    def field(self, name: str) -> Optional[Field]:
        """The credential or setting called ``name``."""
        return next((f for f in (*self.credentials, *self.settings) if f.name == name), None)

    def tool(self, name: Optional[str] = None, *, is_write: bool = False, confirm: ConfirmRule = False,
             acts_outside: bool = False, needs: Sequence[str] = (), available: Optional[AvailabilityRule] = None,
             untrusted_result: bool = False, **extra: Any) -> Callable[[F], F]:
        """Register a function as a tool of this kind. The function's first
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
                :func:`integrations.framework.registry.needs_confirmation`.
            needs: Other kinds the tool also requires; it is offered and runs
                only for a user who has all of them connected.
            available: A function of the user, asked on top of the kinds
                both when listing and when executing: the tool is offered and
                runs only while it returns True (``share_item``: more than one
                user on the server). It must be cheap (asked for every tool
                list) and never raise.
            untrusted_result: The tool's result carries text written by other
                people (an email). Once it ran, the rest of that turn counts
                as carrying untrusted content: every ``acts_outside`` tool
                asks first (``core/agent.py``).

        Re-registering a name in the same kind replaces that tool in place
        (tests swap a tool for a fake this way); a name owned by another kind
        is refused.
        """
        needs_t = tuple(needs)

        def decorator(func: F) -> F:
            tool_name = name or func.__name__
            existing = _tools.get(tool_name)
            if existing is not None and existing.kind != self.kind:
                raise ValueError(f"Tool {tool_name!r} already belongs to the {existing.kind!r} integration")
            for key, value in extra.items():
                # Unknown flags are kept (and logged) instead of breaking the import.
                logger.warning(f"Tool {tool_name}: unknown tool() flag {key}={value!r}")
            registered = Tool(name=tool_name, kind=self.kind, func=func, schema=generate_schema(tool_name, func),
                              is_write=bool(is_write or confirm), confirm=confirm, acts_outside=bool(acts_outside),
                              needs=needs_t, available=available, untrusted_result=bool(untrusted_result),
                              extra=dict(extra))
            if existing is not None:
                self.tools[self.tools.index(existing)] = registered
                logger.info(f"Replaced tool: {tool_name} ({self.kind})")
            else:
                self.tools.append(registered)
            _tools[tool_name] = registered
            _bump()
            logger.debug(f"Registered tool: {tool_name} ({self.kind}, is_write={registered.is_write}, "
                         f"confirm={bool(confirm)})")
            return func
        return decorator


def set_order(order: Sequence[str]) -> None:
    """Fix the kind order: every declared kind, each once.

    Raises:
        ValueError: ``order`` misses a declared kind, repeats one or names an unknown one.
    """
    global _order
    if len(set(order)) != len(order) or set(order) != set(_kinds):
        raise ValueError(f"The kind order {list(order)} must list each declared kind once: {sorted(_kinds)}")
    _order = tuple(order)
    _bump()


def kinds() -> List[Integration]:
    """Every declared kind, in the fixed order."""
    rank = {k: i for i, k in enumerate(_order)}
    declared = list(_kinds)
    return sorted(_kinds.values(), key=lambda k: (rank.get(k.kind, len(rank)), declared.index(k.kind)))


def get_kind(kind: str) -> Integration:
    """The kind called ``kind``.

    Raises:
        KeyError: no such kind.
    """
    try:
        return _kinds[kind]
    except KeyError:
        raise KeyError(f"Unknown integration kind {kind!r}") from None


def find_tool(name: str) -> Optional[Tool]:
    return _tools.get(name)


def all_tools() -> List[Tool]:
    """Every tool of every kind, in the fixed order."""
    return [t for k in kinds() for t in k.tools]


def validate() -> None:
    """Every ``needs`` (of a tool or a prompt part) names a declared kind.

    Raises:
        ValueError: a tool or prompt part needs an unknown kind.
    """
    for t in _tools.values():
        unknown = [k for k in t.needs if k not in _kinds]
        if unknown:
            raise ValueError(f"Tool {t.name!r} needs unknown integration kind(s) {unknown}")
    for k in _kinds.values():
        for part in k.prompt_parts:
            unknown = [n for n in part.needs if n not in _kinds]
            if unknown:
                raise ValueError(f"A prompt part of {k.kind!r} needs unknown integration kind(s) {unknown}")


def check_values(integration: Integration, credentials: Optional[Mapping[str, Any]],
                 settings: Optional[Mapping[str, Any]]) -> None:
    """Refuse credential / setting names the kind does not declare.

    Raises:
        ValueError: an unknown name.
    """
    for label, given, declared in (("credential", credentials, integration.credentials),
                                   ("setting", settings, integration.settings)):
        names = {f.name for f in declared}
        unknown = sorted(set(given or {}) - names)
        if unknown:
            raise ValueError(f"The {integration.kind} integration has no {label} {', '.join(unknown)}"
                             f" (known: {', '.join(sorted(names)) or 'none'})")
