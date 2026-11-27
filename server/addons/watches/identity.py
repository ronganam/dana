"""Who the user is and how Dana reaches them, for the watch compiler and a
watch's agent runs (docs/watches.md, "Who the user is").

A watch acts on the user's behalf later, without them in the chat: the model
must know that "me" / "I" / "myself" (and an event's sender shown as "me")
is the user, never guess another name for them, and know where a ``notify``
lands (the channels ``core.notify.send`` delivers on for them) and which
tools write to the user's own WhatsApp / Telegram chat. Tool lines appear only
when the user has those tools.
"""

from typing import Iterable, List

from core import user_profile, zones
from core.addons import registry
from core.users import User

# Words for the @addon.channel names core.notify.send delivers on.
CHANNEL_WORDS = {"push": "their phone (a notification from the Dana app)"}
SELF_WORDS = '"me", "I", "my", "myself", "אני", "לי", "שלי"'


def channels(user: User) -> List[str]:
    """Where a ``notify`` action reaches ``user``, in words (what ``core.notify.send`` delivers on for them)."""
    out = []
    if user.telegram_chat_id is not None:
        out.append("Telegram (their chat with Dana's bot)")
    for ch in registry.channels(user):
        out.append(CHANNEL_WORDS.get(ch.name, f"{ch.name} ({ch.addon})"))
    return out


def about_user(user: User, tools: Iterable[str]) -> str:
    """The identity block: the user's name, other names, pronoun, what "me" means, how to reach them, which tool
    gives the weather."""
    p = user_profile.for_user(user)
    have = set(tools)
    lines = [f"The user: {p.name or 'unknown name'}"
             + (f" (also called {user.other_names})" if user.other_names else "") + f"; pronoun: {p.pronoun}.",
             f"{SELF_WORDS} in the request mean the user, and so does an event whose sender or author is shown as "
             '"me", "Me", "You" or "אני" (Gmail and WhatsApp show the user\'s own messages that way; an email the '
             "user sent themself is from \"me\"). Never take another person's name (a partner, a contact) for the "
             "user.",
             "Reaching the user themself: a notify action reaches them on "
             + (", ".join(channels(user)) or "no channel yet (nothing would arrive)") + "."]
    if "send_whatsapp_message" in have:
        lines.append('"WhatsApp me": send_whatsapp_message with chat_id "me" writes to the user\'s own WhatsApp chat.')
    if "send_telegram_message_to_me" in have:
        lines.append('"Telegram me": send_telegram_message_to_me(text) writes to the user\'s own Telegram '
                     "(Saved Messages).")
    weather = sorted(t for t in have if "weather" in t.lower())
    if weather:
        lines.append(f"Weather: use {', '.join(weather)} (Dana's own, for the user's places), not a web search.")
    return "\n".join(lines)


def home_line(user: User) -> str:
    """The user's places for an agent run: Home first with its rough coordinates ("" without zones)."""
    zs = zones.list_zones(user)
    if not zs:
        return ""
    parts = [f"{z.name}{' (home)' if z.is_home else ''} ≈ {z.latitude:.2f},{z.longitude:.2f}" for z in zs[:8]]
    return "The user's places: " + "; ".join(parts) + "."
