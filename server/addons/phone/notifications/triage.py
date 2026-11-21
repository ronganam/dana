"""Notification triage with TypeSafe (Jev): typed judgments in, decisions in code.

Every question asked about a notification lives in this module, together with
the truth table that turns the answers into what the app shows:

* ``category``  (Choice) — what kind of notification it is.
* ``priority``  (Score)  — how much it matters to the user, 0 (noise) … 4 (now).
* ``needs_action`` / ``from_person`` / ``time_sensitive`` / ``promotional``
  (Nouls) — the atomic facts the decisions hang on.

:func:`decide` maps the answers (plus the user's rules and how they usually treat the
app) to ``priority``, ``bucket`` (``needs_you`` | ``normal`` | ``dismiss``),
``extract`` (worth asking Gemini for a task/event) and ``escalate``.

All questions about one notification go in one request (they share the state).
A failed call raises :class:`TriageError`; callers store the notification as
untriaged instead of guessing.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from core import clock, usage, user_profile

logger = logging.getLogger(__name__)

API_URL = os.getenv("TYPESAFE_API_URL", "https://api.typesafe.ai").rstrip("/") + "/v1/systemone"
MODEL = os.getenv("TYPESAFE_MODEL", "jev-latest")

# Noul bands (TypeSafe's suggested starting point): ≥ YES reads as yes, ≤ NO as no.
YES = 0.7
NO = 0.3

PRIORITY_LABELS = ["noise", "low", "normal", "important", "urgent"]

# Feeds written for an audience, not for the user (see decide()).
BROADCAST_CATEGORIES = ("social_media", "news")

def categories(me: str) -> Dict[str, str]:
    """What each category means, for ``me`` (the user the notification came to)."""
    return {
        "personal_message": f"A person writing to {me}, directly or in a group chat",
        "work": "Work: a colleague, a work tool, a meeting or a work email",
        "delivery": "A package, courier, food order or ride status",
        "money": "A bank, card, bill, payment, transfer or purchase receipt",
        "security": "A login alert, verification code, password change or account warning",
        "calendar_reminder": "An appointment, meeting or reminder from a calendar or reminders app",
        "promotion": "A sale, coupon, offer or marketing message",
        "social_media": "Likes, comments, follows or posts on a social network",
        "news": "A news headline or article",
        "smart_home": "Home automation: alarms, cameras, doorbell, appliances",
        "device_system": "Phone or app status: updates, battery, downloads, backups",
        "other": "None of the other kinds fits",
    }

CATEGORY_EMOJI = {
    "personal_message": "💬", "work": "💼", "delivery": "📦", "money": "💳", "security": "🔐",
    "calendar_reminder": "📅", "promotion": "🏷️", "social_media": "👍", "news": "📰",
    "smart_home": "🏠", "device_system": "⚙️", "other": "🔔",
}

def priority_levels(me: str, pronoun: str = "they") -> List[str]:
    """The priority score's levels 0–4, for ``me`` (``pronoun``: theirs)."""
    he, him, his, _ = user_profile.pronoun_forms(pronoun)
    return [
        f"Marketing, a social-media like, or a routine app message {me} never needs to see",
        f"Routine information {me} might glance at with nothing to do, such as a news headline, an app update or a finished download",
        f"Something {me} would want to read today, such as an ordinary message from a person or a status update on something {he} ordered",
        f"Something {me} should deal with soon, such as a direct question from a person, a bill, an appointment change or a delivery that needs {him}",
        f"Something that needs {me} right now, such as an emergency, a security warning on {his} accounts, someone waiting for {him} at this moment, or a deadline within the hour",
    ]


def questions(me: str, pronoun: str = "they") -> Dict[str, Any]:
    """Every question asked about a notification that came to ``me`` (the
    user's first name; e.g. "Robin"), worded with
    their ``pronoun``."""
    he, him, his, himself = user_profile.pronoun_forms(pronoun)
    pays = "pay" if pronoun not in ("he", "she") else "pays"
    return {
        "category": {
            "type": "choice",
            "instructions": "What kind of notification is `notification`?",
            "criteria": categories(me),
        },
        "priority": {
            "type": "score",
            "instructions": f"How much does `notification` matter to {me}?",
            "criteria": priority_levels(me, pronoun),
        },
        "needs_action": {
            "type": "noul",
            "instructions": f"Does `notification` give {me} something to do?",
            "criteria": {
                "true": f"{me} has a reply, task, payment, pickup or appointment to handle, e.g. 'can you buy milk on the way?', "
                        "'your bill is due Thursday', 'your package is waiting at the post office'",
                "false": f"{me} only needs to know it, e.g. 'your order was delivered', 'Noa liked your photo', a sale announcement",
            },
        },
        "from_person": {
            "type": "noul",
            "instructions": "Was the message in `notification` written by a person?",
            "criteria": {
                "true": "A friend, relative, colleague or contact typed it",
                "false": "An app, company, bot or automated system generated it",
            },
        },
        "time_sensitive": {
            "type": "noul",
            "instructions": "Would reading `notification` tomorrow instead of now be too late?",
            "criteria": {
                "true": "It expires within hours, e.g. a courier at the door, a meeting starting soon, a fraud warning, someone waiting for an answer now, "
                        "a person saying it's urgent",
                "false": "It can wait a day without consequence",
            },
        },
        "urgent_request": {
            "type": "noul",
            "instructions": f"Does the person who wrote `notification` say it's urgent or ask {me} to answer or act right away?",
            "criteria": {
                "true": "They say so outright, e.g. 'urgent', 'דחוף', 'ASAP', 'call me now', 'תענה', 'where are you??', "
                        "'emergency', or keep repeating the request",
                "false": "An ordinary message with no stated rush, or an app or company calling something urgent",
            },
        },
        "promotional": {
            "type": "noul",
            "instructions": "Is `notification` advertising or marketing something?",
            "criteria": {
                "true": f"A sale, coupon, offer, or an app nudging {me} to come back",
                "false": f"A message, alert or update about something {me} did or owns, such as a receipt or a delivery update",
            },
        },
        # --- Whether a to-do would help (feeds ``extract``; see decide()) ---
        "owner_wrote_it": {
            "type": "noul",
            "instructions": f"Did {me} {himself} write the message in `notification`?",
            "criteria": {
                "true": f"The message is {me}'s own, e.g. the sender is shown as \"You\" or as {me}",
                "false": "Someone else, or an app, wrote it",
            },
        },
        "addressed_to_owner": {
            "type": "noul",
            "instructions": f"Is the message in `notification` speaking to {me}?",
            "criteria": {
                "true": f"It asks or tells {me} something: a direct chat with {him}, {his} name, or a group message that answers or asks {him}",
                "false": f"In a group chat it speaks to someone else, by their name or by a form of address that doesn't fit {me} "
                         "(e.g. Hebrew feminine \"את\" to a woman), or it is a general announcement",
            },
        },
        "tracked_elsewhere": {
            "type": "noul",
            "instructions": f"Will the app that sent `notification` keep showing {me} this item until it is dealt with?",
            "criteria": {
                "true": "It lives in a tool with its own queue or schedule: a pull request review, a failed build, a ticket mention, "
                        f"an approval request in a work tool, or a reminder of a meeting already in {me}'s calendar",
                "false": f"Once the notification is gone nothing reminds {me}: a person's chat message, an SMS, an email asking {him} for something",
            },
        },
        "record_only": {
            "type": "noul",
            "instructions": "Is `notification` a record of something that already happened?",
            "criteria": {
                "true": "A card charge, a receipt, a transfer that went through, a statement or report ready to view, an invoice paid automatically",
                "false": f"Something still open that waits for {me}, such as a bill {he} {pays} {himself}, a form to submit or a benefit to claim",
            },
        },
        "self_clearing": {
            "type": "noul",
            "instructions": f"Is `notification` about a device or home state that {me} handles on the spot or that clears by itself?",
            "criteria": {
                "true": "A robot vacuum stuck, a water tank to empty, an alarm about to ring, a battery low, a door left open",
                "false": f"Something {me} has to plan for or remember later, such as a payment, a reply, a pickup or a renewal",
            },
        },
    }


# The questions' ids (the same for everyone).
QUESTION_IDS = tuple(questions(""))
CATEGORIES = tuple(categories(""))

# Asked in the same request, but older recorded answers (and tests) may lack
# them; a missing one counts as unsettled.
OPTIONAL_QUESTIONS = ("urgent_request", "owner_wrote_it", "addressed_to_owner", "tracked_elsewhere", "record_only", "self_clearing")


class TriageError(RuntimeError):
    pass


def build_state(n: Dict[str, Any], owner_name: str, owner_about: str) -> Dict[str, Any]:
    """What a person would look at: who the user is, the time, and the notification."""
    posted = n.get("posted_at") or ""
    note: Dict[str, Any] = {"app": n.get("app_name") or n.get("package")}
    for key, label in (("title", "title"), ("text", "text"), ("sub_text", "subtext"),
                       ("sender", "sender"), ("conversation", "conversation")):
        v = (n.get(key) or "").strip()
        if v:
            note[label] = v[:2000]
    if n.get("is_group"):
        note["group_conversation"] = True
    if posted:
        note["posted_at"] = str(posted)[:16].replace("T", " ")
    return {
        "owner": {"name": owner_name, "about": owner_about},
        "now": clock.now().strftime("%A %Y-%m-%d %H:%M"),
        "notification": note,
    }


async def ask(state: Dict[str, Any], *, me: str, pronoun: str = "they", api_key: Optional[str] = None,
              client: Optional[httpx.AsyncClient] = None, retries: int = 3, ref: str = "") -> Dict[str, Any]:
    """POST the bundled questions (worded for ``me``, the user's first name, and their ``pronoun``);
    returns the ``answers`` dict. Raises TriageError.
    The request is tracked in core/usage.py (``ref``: what it was for, ``notification:<id>``);
    TypeSafe bills input tokens only and reports none, so they are estimated from the request."""
    if not (api_key or os.getenv("TYPESAFE_API_KEY", "")):
        return await _ask(state, me=me, pronoun=pronoun, api_key=api_key, client=client,
                          retries=retries)  # raises, nothing was sent
    started = time.perf_counter()
    tokens = usage.estimate_tokens({"state": state, "questions": questions(me, pronoun)})

    def track(ok: bool, error: str = "") -> None:
        usage.record(kind="decision", purpose="notification_triage", source="phone", ref=ref, provider="typesafe",
                     model=MODEL, input_tokens=tokens, cost=usage.decision_cost(tokens) if ok else None, ok=ok,
                     error=error, ms=int((time.perf_counter() - started) * 1000))

    try:
        answers = await _ask(state, me=me, pronoun=pronoun, api_key=api_key, client=client, retries=retries)
    except Exception as e:
        track(False, str(e))
        raise
    track(True)
    return answers


async def _ask(state: Dict[str, Any], *, me: str, pronoun: str, api_key: Optional[str],
               client: Optional[httpx.AsyncClient], retries: int) -> Dict[str, Any]:
    key = api_key or os.getenv("TYPESAFE_API_KEY", "")
    if not key:
        raise TriageError("TYPESAFE_API_KEY is not set")
    payload = {"model": MODEL, "state": state, "questions": questions(me, pronoun)}
    logger.debug("TypeSafe request: %s", payload)
    last: Optional[str] = None
    own = client is None
    client = client or httpx.AsyncClient(timeout=30.0)
    try:
        for attempt in range(retries):
            try:
                resp = await client.post(API_URL, json=payload, headers={"Authorization": f"Bearer {key}"})
            except httpx.HTTPError as e:
                last = f"network: {e}"
                await asyncio.sleep(1.0 * (attempt + 1))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last = f"HTTP {resp.status_code}"
                try:
                    wait = float(resp.headers.get("retry-after", "")) or 0
                except ValueError:
                    wait = 0
                await asyncio.sleep(min(30.0, wait or 1.5 * (attempt + 1)))
                continue
            if resp.status_code != 200:
                raise TriageError(f"TypeSafe HTTP {resp.status_code}: {resp.text[:300]}")
            answers = (resp.json() or {}).get("answers") or {}
            missing = [q for q in QUESTION_IDS if q not in answers and q not in OPTIONAL_QUESTIONS]
            if missing:
                raise TriageError(f"TypeSafe answer missing {missing}")
            return answers
    finally:
        if own:
            await client.aclose()
    raise TriageError(f"TypeSafe failed after {retries} attempts ({last})")


def _noul(answers: Dict[str, Any], name: str) -> float:
    try:
        return float((answers.get(name) or {}).get("noul"))
    except (TypeError, ValueError):
        return 0.5  # unsettled — falls into the middle row of every rule


def decide(answers: Dict[str, Any], *, rule: Optional[str] = None,
           low_engagement: bool = False) -> Dict[str, Any]:
    """The truth table. ``rule`` is the user's per-app/sender rule ('mute'|'important'|'alert');
    ``low_engagement`` means they swipe away nearly everything from this app."""
    cat = (answers.get("category") or {}).get("choice") or "other"
    if cat not in CATEGORIES:
        cat = "other"
    pr = answers.get("priority") or {}
    try:
        score = float(pr.get("score"))
    except (TypeError, ValueError):
        score = 2.0
    action = _noul(answers, "needs_action")
    person = _noul(answers, "from_person")
    urgent_now = _noul(answers, "time_sensitive")
    promo = _noul(answers, "promotional")

    # Something to do only counts when it matters at least a little: "a system
    # update is ready to install" is technically an action, not a reason to
    # interrupt the user.
    has_action = action >= YES and promo < YES and score >= 1.5
    # Feeds (social posts, headlines) are written to sound urgent; they only
    # reach "needs you" when a person asks the user for something, or via a rule
    # (e.g. mark the Home Front Command app "important").
    broadcast = cat in BROADCAST_CATEGORIES and not (person >= YES and has_action)
    if broadcast:
        has_action = False
    # A to-do only helps when nothing else will remind the user: not their own
    # message, not one meant for someone else, not an item a work tool or the
    # calendar keeps showing, not a receipt, not a device state they fix on
    # the spot. Unsettled answers (0.3–0.7) don't block.
    own = _noul(answers, "owner_wrote_it")
    to_me = _noul(answers, "addressed_to_owner")
    tracked = _noul(answers, "tracked_elsewhere")
    record = _noul(answers, "record_only")
    clears = _noul(answers, "self_clearing")
    worth_a_task = (has_action and own < YES and to_me > NO and tracked < YES
                    and record < YES and clears < YES)

    # A person telling the user, in their own chat, that it's urgent ("דחוף תתרגם לי
    # את זה") is urgent whatever "too late tomorrow?" scored.
    asks_now = _noul(answers, "urgent_request")
    person_urgent = (asks_now >= YES and person >= YES and own < YES and to_me > NO
                     and cat not in BROADCAST_CATEGORIES and promo < YES)

    # Urgent = important AND too late tomorrow, or a person says it's urgent.
    if person_urgent or (score >= 2.5 and urgent_now >= YES):
        priority = "urgent"
    elif score >= 2.5:
        priority = "important"
    elif score >= 1.5:
        priority = "normal"
    elif score >= 0.5:
        priority = "low"
    else:
        priority = "noise"

    if rule in ("important", "alert"):
        bucket = "needs_you"
        if PRIORITY_LABELS.index(priority) < PRIORITY_LABELS.index("important"):
            priority = "important"
    elif rule == "mute":
        bucket = "dismiss"
    elif (priority in ("urgent", "important") and not broadcast) or has_action:
        bucket = "needs_you"
    elif promo >= YES and action < YES:
        bucket = "dismiss"
    elif score < 1.5 and action <= NO and person <= NO:
        bucket = "dismiss"
    elif low_engagement and score < 2.5 and action < YES and person < YES:
        bucket = "dismiss"
    else:
        bucket = "normal"

    return {
        "category": cat,
        "priority": priority,
        "priority_score": round(score, 3),
        "bucket": bucket,
        # Ask Gemini for a concrete task/event only when there is one to find.
        # (A calendar reminder is already in the calendar; a new event would duplicate it.)
        "extract": worth_a_task and rule != "mute" and cat not in ("security", "promotion", "calendar_reminder"),
        # A calendar reminder is already the alarm; announcing it twice is noise.
        # "alert" rule: every notification from that app/sender gets the alert screen.
        "escalate": rule == "alert" or (priority == "urgent" and rule != "mute" and cat != "calendar_reminder"
                                        and (not broadcast or rule == "important")),
        "signals": {
            "needs_action": round(action, 3), "from_person": round(person, 3),
            "time_sensitive": round(urgent_now, 3), "urgent_request": round(asks_now, 3), "promotional": round(promo, 3),
            "owner_wrote_it": round(own, 3), "addressed_to_owner": round(to_me, 3),
            "tracked_elsewhere": round(tracked, 3), "record_only": round(record, 3), "self_clearing": round(clears, 3),
            "category_probabilities": (answers.get("category") or {}).get("probabilities") or {},
            "priority_probabilities": pr.get("probabilities") or {},
        },
    }


def decide_rule_only(rule: str) -> Dict[str, Any]:
    """A muted app/sender is not sent to the model at all."""
    return {"category": "other", "priority": "noise", "priority_score": 0.0, "bucket": "dismiss",
            "extract": False, "escalate": False, "signals": {"rule": rule}}
