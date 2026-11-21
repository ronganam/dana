import logging
from typing import Callable, Literal, Optional

from core import undo
from core.text import escape_markdown
from addons.about import facts, legacy
from addons.about.addon import addon
from addons.about.values import FACT_MAX_CHARS, SECTIONS, clean_text, looks_sensitive, section_for_category
from core.addons import Connection

logger = logging.getLogger(__name__)

# --- "About you" facts: the model's memory tools ------------------------------
# The tool names are the ones the system prompt and old conversations use; they
# are backed by addons/about/facts.py: the connection's user's own facts,
# always in their system prompt. They act at once and register an undo
# (core/undo.py); nothing waits for approval.
# Registered without is_write on purpose: then no registry version ever asks
# for approval, and they are safe to run alongside read tools (no await inside,
# so each call is atomic on the event loop).
# ``**kwargs`` keeps the old key/value/category arguments working (Telegram
# /memory, old history) without showing them to the model.

Section = Literal["people", "home", "habits", "other"]


def _record_undo(label: str, fn: Callable[[], object], source: str) -> None:
    try:
        undo.record(label, fn, source=source)
    except Exception:
        logger.warning(f"Could not register undo for {source}", exc_info=True)


def _fmt_fact(f: dict) -> str:
    return f"- {f['text']} ({f['section']}, from {f['source']}; id {f['id']})"


# acts_outside: a remembered fact is put into every later prompt, so in a turn
# that carries untrusted content (a shared phone screen) saving one asks first;
# text on a page must not be able to plant something Dana then "knows".
@addon.tool(acts_outside=True)
async def remember_info(conn: Connection, fact: str = "", section: Optional[Section] = None, replaces: Optional[str] = None,
                        **kwargs) -> str:
    """Save a durable fact about the owner in "About you". It is kept at once (no approval) and the owner gets an Undo button.

    Call it on your own, without being asked, whenever you learn something that will still be true and useful in a month: who a person is to the owner, what a nickname, place or device name refers to, a standing preference or routine. Also call it when the owner says "remember ...". Never save passwords, codes, account or card numbers, health or money details, plans or one-time events, or guesses the owner did not confirm. Saved facts are already in your instructions in every conversation, so do not re-save what is there; reply briefly ("Noted.") after saving.

    Args:
        fact: One short plain sentence in the owner's language, e.g. "Dan is Maya's brother" or "'The gate' means the front gate". Required.
        section: people, home, habits or other. Guessed when omitted.
        replaces: When this corrects or sharpens a fact you already have, that fact's id or text; it is rewritten instead of adding a second one.
    """
    user = conn.user
    text = fact
    if not (text or "").strip() and kwargs.get("value"):
        text = legacy.legacy_text(str(kwargs.get("key") or ""), str(kwargs["value"]))
    if section is None and kwargs.get("category"):
        section = section_for_category(str(kwargs["category"]), str(text or ""))
    text = clean_text(text)
    if not text:
        return "Error: say the fact in one short sentence (the 'fact' argument)."
    if len(text) > FACT_MAX_CHARS:
        return f"Error: too long ({len(text)} characters). Keep a fact to one short sentence."
    if looks_sensitive(text):
        return "Not saved: it looks like a password, code or account number, and About you never keeps those."
    try:
        if replaces:
            old, cands = facts.resolve(user, replaces)
            if old:
                snapshot = facts.snapshot(user, old["id"])
                new = facts.update_fact(user, old["id"], text=text, section=section, source_kind="told")
                _record_undo(f"Changed back: {old['text']}", lambda: facts.restore_snapshot(user, snapshot),
                             "remember_info")
                return f"Updated in About you: {escape_markdown(new['text'])}"
            if cands:
                return "Which fact does it replace? Call again with its id:\n" + \
                    escape_markdown("\n".join(_fmt_fact(c) for c in cands))
        if facts.is_forgotten(user, text):
            return ("Not saved: the owner removed this from About you before. Don't save it again; if they want it "
                    "back they can add it on the About you page.")
        new, created = facts.add_fact(user, text, section, source_kind="told")
        if not created:
            return f"Already known: {escape_markdown(new['text'])}"
        fid = new["id"]
        _record_undo(f"Saved: {new['text']}", lambda: facts.forget_fact(user, fid), "remember_info")
        return f"Saved in About you ({new['section']}): {escape_markdown(new['text'])}"
    except (ValueError, LookupError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.error(f"Error saving fact: {e}", exc_info=True)
        return f"Error saving fact: {e}"


@addon.tool()
async def recall_info(conn: Connection, query: str) -> str:
    """Search what you know about the owner ("About you" facts, plus a few older saved notes). Everything in About you is already in your instructions, so call this only to look for something specific that is not there, or to get a fact's id.

    Args:
        query: Words to look for, e.g. a name ("Dan", "אמא") or a topic ("gate", "wifi").
    """
    q = (query or "").strip()
    if not q:
        return "Error: Search query cannot be empty."
    try:
        found = facts.search(conn.user, q)
        older = legacy.unmigrated_rows(conn.user, q)
        if not found and not older:
            return f"Nothing in About you matches '{escape_markdown(q)}'."
        lines = [f"Found {len(found) + len(older)}:"]
        lines += [_fmt_fact(f) for f in found]
        lines += [f"- (older note) {r['key']}: {r['value']}" for r in older]
        return escape_markdown("\n".join(lines))
    except Exception as e:
        logger.error(f"Error recalling facts: {e}", exc_info=True)
        return f"Error recalling facts: {e}"


@addon.tool()
async def forget_info(conn: Connection, fact: str = "", **kwargs) -> str:
    """Remove a fact from "About you" when the owner asks you to forget it or says it is wrong. It is removed at once (Undo is offered) and will not be learned again.

    Args:
        fact: The fact's id (from recall_info / list_memories) or its text. Required.
    """
    user = conn.user
    target = (fact or str(kwargs.get("key") or "")).strip()
    if not target:
        return "Error: say which fact to forget (its id or text)."
    try:
        match, cands = facts.resolve(user, target)
        if match:
            facts.forget_fact(user, match["id"])
            fid = match["id"]
            _record_undo(f"Forgot: {match['text']}", lambda: facts.restore_fact(user, fid), "forget_info")
            return f"Forgot: {escape_markdown(match['text'])}"
        if cands:
            return "Which one? Call again with its id:\n" + escape_markdown("\n".join(_fmt_fact(c) for c in cands))
        # An old memory key (the Telegram /memory picker still lists them to their owner).
        key = target.lower().replace(" ", "_")
        row = legacy.old_memory(user, key)
        if row and legacy.delete_old_memory(user, key):
            _record_undo(f"Deleted old memory {key}", lambda: legacy.restore_old_memory(row), "forget_info")
            return f"Deleted old memory '{escape_markdown(key)}'."
        return f"No fact matches '{escape_markdown(target)}'."
    except Exception as e:
        logger.error(f"Error forgetting fact: {e}", exc_info=True)
        return f"Error forgetting fact: {e}"


@addon.tool()
async def list_memories(conn: Connection, section: Optional[Section] = None, **kwargs) -> str:
    """List the facts in "About you", optionally one section. They are already in your instructions; use this when the owner asks what you know about them, or to get ids.

    Args:
        section: people, home, habits or other. All sections when omitted.
    """
    cat = kwargs.get("category")
    if section is None and cat and str(cat).strip().lower() not in ("all", ""):
        section = section_for_category(str(cat))
    try:
        listed = facts.list_facts(conn.user)
        if section:
            listed = [f for f in listed if f["section"] == section]
        if not listed:
            return f"Nothing in About you{f' under {section}' if section else ''} yet."
        lines = []
        for key, label in SECTIONS:
            group = [f for f in listed if f["section"] == key]
            if group:
                lines.append(f"{label}:")
                lines += [_fmt_fact(f) for f in group]
        return escape_markdown("\n".join(lines))
    except Exception as e:
        logger.error(f"Error listing facts: {e}", exc_info=True)
        return f"Error listing facts: {e}"
