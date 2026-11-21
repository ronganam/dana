"""How phone notifications are split into headline, sender and body, and why a
needs-you notification is in the Now queue (used by the phone addon's Now cards,
search results and suggestion sources)."""
from __future__ import annotations

import datetime
import re
from typing import Any, Dict, List, Optional, Tuple

from core import cards, clock
from addons.phone.notifications.triage import NO, YES

# "3 new messages", "(2) הודעות חדשות": a count, not content and not a sender.
_COUNT_LINE = re.compile(r"^\(?\d+\)?\s+(new\s+)?(messages?|emails?|notifications?|chats?|הודעות(\s+חדשות)?)\b", re.I)
# WhatsApp's "Dan (2 messages)".
_COUNT_SUFFIX = re.compile(r"\s*\(\d+\s+[^)]{1,20}\)\s*$")
_SPEAKER = re.compile(r"^([^:\n]{1,30}):\s+(.+)$")


def _cmp(s: str) -> str:
    return re.sub(r"\W+", " ", (s or "").casefold()).strip()


def _title_is_content(title: str, first_line: str) -> bool:
    """Whether a non-chat notification's title is its message ("Your package has
    arrived" over "Tap to track") rather than who / what it is from ("Bright Data"
    over the subject, "homelab" over "CI passed on #3")."""
    words = len(title.split())
    return (title.rstrip()[-1:] in "?!.…:" or words >= 5
            or (words >= 3 and len(title) > len(first_line)))


def notification_parts(n: Dict[str, Any]) -> Dict[str, str]:
    """``headline`` (what the notification says), ``who`` (sender / where it is
    from, '' when the app or the headline already says it) and ``rest`` (the
    text after the headline, for the card's text block).

    Chats (a sender or a conversation): the newest message is the headline, the
    sender (" in <group>") is who. Everything else (mail, GitHub, banks, …): the
    first meaningful line of the text is the headline and the title is who,
    unless the title itself reads as the message."""
    app = (n.get("app_name") or "").strip()
    title = _COUNT_SUFFIX.sub("", (n.get("title") or "").strip())
    lines = [l.strip() for l in (n.get("text") or "").split("\n") if l.strip()]
    sender = (n.get("sender") or "").strip()
    conv = (n.get("conversation") or "").strip()
    if sender or conv:
        who = sender or conv
        if sender and conv and n.get("is_group") and _cmp(conv) != _cmp(sender):
            who = f"{sender} in {conv}"
        last = lines[-1] if lines else ""
        m = _SPEAKER.match(last)
        if m and (n.get("is_group") or _cmp(m.group(1)) == _cmp(sender)):
            last = m.group(2).strip()
        return {"headline": last or title or app, "who": who if _cmp(who) != _cmp(app) else "", "rest": ""}
    useful = [l for l in lines if _cmp(l) != _cmp(title) and not _COUNT_LINE.match(l)]
    is_label = title and not _COUNT_LINE.match(title) and _cmp(title) != _cmp(app)
    if not useful:
        return {"headline": title or app, "who": "", "rest": ""}
    if title and not _COUNT_LINE.match(title) and _title_is_content(title, useful[0]):
        return {"headline": title, "who": "", "rest": "\n".join(useful)}
    headline = useful[0]
    who = title if is_label and _cmp(title) not in _cmp(headline) else ""
    return {"headline": headline, "who": who, "rest": "\n".join(useful[1:])}


def rule_subject(conn, n: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """The rule of the notification's user that applies (sender rule first, as
    the triage reads it) and what it was set on: the sender's name or the app's."""
    sender = n.get("sender") or ""
    if sender:
        r = conn.execute("SELECT action FROM notification_rules WHERE user_id = ? AND package = ? AND sender = ?",
                         (n["user_id"], n["package"], sender)).fetchone()
        if r:
            return r["action"], sender
    r = conn.execute("SELECT action FROM notification_rules WHERE user_id = ? AND package = ? AND sender = ''",
                     (n["user_id"], n["package"])).fetchone()
    return (r["action"] if r else None), (n.get("app_name") or "this app")


def notification_why(n: Dict[str, Any], rule: Optional[str], subject: str = "") -> Optional[str]:
    """Why a needs-you notification is in the queue, from what the triage
    answered (``notifications.triage``) and the user's rule — or None when
    there is nothing more specific to say than "it's here"."""
    tri = cards.loads(n.get("triage"), {})
    tri = tri if isinstance(tri, dict) else {}

    def sig(name: str) -> float:
        try:
            return float(tri.get(name))
        except (TypeError, ValueError):
            return 0.5  # not asked / not answered: unsettled

    person, action, soon = sig("from_person"), sig("needs_action"), sig("time_sensitive")
    asks_now, to_me, own = sig("urgent_request"), sig("addressed_to_owner"), sig("owner_wrote_it")
    cat = n.get("category") or ""
    who = (n.get("sender") or "").strip()
    if not who and person >= YES:
        parts = notification_parts(n)
        who = parts["who"]
    subject = subject or n.get("app_name") or "this app"

    main = ""
    if asks_now >= YES and person >= YES and own < YES and to_me > NO:
        return f"{who or 'Someone'} says it's urgent"
    if person >= YES and action >= YES and own < YES and to_me > NO:
        main = f"{who} is waiting for an answer" if who else "Someone is waiting for an answer"
    elif rule == "alert":
        main = f"You asked to be interrupted for {subject}"
    elif rule == "important":
        main = f"You marked {subject} as important"
    elif cat == "security":
        main = "Security alert"
    elif cat == "money" and action >= YES and sig("record_only") < YES:
        main = "A payment to handle"
    elif cat == "money":
        main = "About your money"
    elif cat == "delivery" and action >= YES:
        main = "A delivery needs you"
    if soon >= YES:
        return f"{main} · time-sensitive" if main else "Time-sensitive"
    return main or None


def _chat_lines(text: str) -> List[Dict[str, Any]]:
    out = []
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^([^:\n]{1,30}):\s+(.+)$", line)
        msg: Dict[str, Any] = {"mine": False, "text": cards.clip(m.group(2) if m else line, 300)}
        if m:
            msg["who"] = m.group(1).strip()
        out.append(msg)
    return out


def chat_block(conn, n: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The last few messages of the conversation this notification belongs to."""
    if not (n.get("sender") or n.get("conversation")):
        return None
    since = (cards.parse_local(n.get("posted_at")) or clock.now()) - datetime.timedelta(hours=12)
    if n.get("conversation"):
        cond, val = "conversation = ?", n["conversation"]
    else:
        cond, val = "sender = ?", n["sender"]
    rows = conn.execute(
        f"SELECT text FROM notifications WHERE user_id = ? AND package = ? AND {cond} AND posted_at >= ? "
        f"AND posted_at <= ? ORDER BY posted_at DESC LIMIT 6",
        (n["user_id"], n["package"], val, since.isoformat(timespec="seconds"), n.get("posted_at") or "")).fetchall()
    msgs: List[Dict[str, Any]] = []
    for r in reversed(rows):
        for m in _chat_lines(r["text"] or ""):
            if not msgs or msgs[-1]["text"] != m["text"]:
                msgs.append(m)
    if not msgs:
        return None
    label = "The chat" + (f" · {n['conversation']}" if n.get("conversation") and n.get("is_group") else "")
    return {"type": "chat", "label": label, "messages": msgs[-5:]}
