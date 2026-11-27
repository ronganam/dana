"""Who the user is and how Dana reaches them, for the watch compiler and a
watch's agent runs (docs/watches.md, "Who the user is").

A watch acts on the user's behalf later, without them in the chat: the model
must know the user's name (never guess another name for them), their own
addresses (to compare an event's real sender or recipient with, and to send
to them like to anyone else) and where a ``notify`` lands (the channels
``core.notify.send`` delivers on for them). Nothing here is a special rule or
a special recipient: the addresses are plain data read from the user's
connections.
"""

import asyncio
import logging
from typing import Any, Dict, Iterable, List, Optional

from core import user_profile, zones
from core.addons import registry, store
from core.users import User

logger = logging.getLogger(__name__)

# Words for the @addon.channel names core.notify.send delivers on.
CHANNEL_WORDS = {"push": "their phone (a notification from the Dana app)"}
# How long learning the WhatsApp account's own id from WAHA may take before a prompt goes without it.
WHATSAPP_WAIT_SECONDS = 2.0
# user id -> the WhatsApp account's own chat id, as last learned (see :func:`learn`).
_whatsapp_ids: Dict[str, str] = {}


def channels(user: User) -> List[str]:
    """Where a ``notify`` action reaches ``user``, in words (what ``core.notify.send`` delivers on for them)."""
    out = []
    if user.telegram_chat_id is not None:
        out.append("Telegram (their chat with Dana's bot)")
    for ch in registry.channels(user):
        out.append(CHANNEL_WORDS.get(ch.name, f"{ch.name} ({ch.addon})"))
    return out


def _connection(user: User, name: str) -> Any:
    try:
        return store.get(user, name)
    except Exception:  # not installed here, or its credentials unreadable: no address from it
        return None


async def learn(user: User) -> None:
    """Learn the user's WhatsApp account's own chat id when the connection
    does not hold it (asked from WAHA, at most :data:`WHATSAPP_WAIT_SECONDS`;
    kept for :func:`own_addresses`). Never raises; nothing to do without
    WhatsApp."""
    conn = _connection(user, "whatsapp")
    if conn is None:
        _whatsapp_ids.pop(user.id, None)
        return
    try:
        from addons.whatsapp import client as waha
        own = await asyncio.wait_for(waha.own_chat_id(conn), WHATSAPP_WAIT_SECONDS)
    except Exception as e:  # WAHA slow or down: the prompt goes without it
        logger.debug(f"WhatsApp own id of user {user.id} not learned: {e!r}")
        return
    if own:
        _whatsapp_ids[user.id] = own


def own_addresses(user: User) -> List[str]:
    """The user's own addresses from their connections, in words: the Gmail
    account, the WhatsApp account's chat id, the Telegram account."""
    out: List[str] = []
    gmail = _connection(user, "gmail")
    if gmail is not None and gmail.account:
        out.append(f"email {gmail.account}")
    wa = _connection(user, "whatsapp")
    if wa is not None and not wa.account_conflict:
        own: Optional[str] = None
        try:
            from addons.whatsapp import client as waha
            own = waha.own_jid(wa.account)
        except Exception:
            own = None
        own = own or _whatsapp_ids.get(user.id)
        if own:
            out.append(f"WhatsApp {own}")
    tg = _connection(user, "telegram_account")
    if tg is not None and tg.account:
        out.append(f"Telegram {tg.account}")
    return out


def about_user(user: User, tools: Iterable[str]) -> str:
    """The identity block: the user's name, other names, pronoun, their own addresses, how a notify reaches them,
    which tool gives the weather."""
    p = user_profile.for_user(user)
    have = set(tools)
    lines = [f"The user: {p.name or 'unknown name'}"
             + (f" (also called {user.other_names})" if user.other_names else "") + f"; pronoun: {p.pronoun}. "
             "Never take another person's name (a partner, a contact) for the user."]
    addresses = own_addresses(user)
    if addresses:
        lines.append("The user's own addresses: " + ", ".join(addresses) + ".")
    lines.append("A notify action reaches the user on "
                 + (", ".join(channels(user)) or "no channel yet (nothing would arrive)") + ".")
    weather = sorted(t for t in have if "weather" in t.lower())
    if weather:
        lines.append(f"Weather: use {', '.join(weather)} (Dana's own, for the user's places), not a web search.")
    return "\n".join(lines)


def remembered(user: User) -> str:
    """What Dana remembers about the user (the About block of their prompt, when the about addon has one), and
    what to do when something the request needs about them is missing."""
    try:
        from addons.about import prompt as about_prompt
        block = about_prompt.section(user)
    except Exception:
        block = ""
    return (block + "\nWhen the request needs something about the user that is not known here (their phone "
            "number, an address, an account), do not guess: say so in `unsupported`, so Dana asks them and "
            "remembers the answer.")


def home_line(user: User) -> str:
    """The user's places for an agent run: Home first with its rough coordinates ("" without zones)."""
    zs = zones.list_zones(user)
    if not zs:
        return ""
    parts = [f"{z.name}{' (home)' if z.is_home else ''} ≈ {z.latitude:.2f},{z.longitude:.2f}" for z in zs[:8]]
    return "The user's places: " + "; ".join(parts) + "."
