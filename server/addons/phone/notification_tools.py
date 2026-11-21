"""The phone addon's notification tools: the user's notifications Inbox
(search, list, summarize, dismiss, snooze), each on the acting user's own
notifications (``conn.user``)."""
import datetime
from typing import Optional

from core import clock
from core.db import get_db_connection
from core.undoable import safe_record
from core.users import User
from core.addons import Connection
from addons.phone.addon import addon
from addons.phone.notifications import shade, store, summary as ns
from addons.phone.notifications.values import BUCKETS

def _line(n: dict) -> str:
    when = str(n.get("posted_at") or "")[:16].replace("T", " ")
    who = f" ({n['sender']})" if n.get("sender") and n.get("sender") != n.get("title") else ""
    text = (n.get("text") or "").replace("\n", " ")[:220]
    tags = f"{n.get('priority') or '?'}/{n.get('category') or 'untriaged'}"
    sugg = f" · {len([s for s in n.get('suggestions') or [] if s['status'] == 'pending'])} pending suggestion(s)" \
        if n.get("suggestions") else ""
    state = f"snoozed until {str(n['snoozed_until'])[:16].replace('T', ' ')}" if n.get("snoozed_until") else n.get("status")
    return f"- {n['id']} [{when}] {n.get('app_name')}{who}: {n.get('title') or ''} — {text} [{tags}, {state}]{sugg}"


@addon.tool()
async def search_notifications(conn: Connection, query: str, limit: int = 15) -> str:
    """Full-text search over the notifications the user's phone received (last 30 days), e.g. a bank message, a courier update or what someone wrote.

    Args:
        query: Words to search for (any language).
        limit: Max results (default 15).
    """
    items = store.list_notifications(conn.user, status="all", q=query, limit=max(1, min(limit, 50)))
    if not items:
        return f"No notifications match '{query}'."
    return f"{len(items)} notification(s):\n" + "\n".join(_line(n) for n in items)


@addon.tool()
async def list_notifications(conn: Connection, bucket: Optional[str] = None, app: Optional[str] = None, hours: int = 24,
                             include_dismissed: bool = False, limit: int = 30) -> str:
    """List recent phone notifications with Dana's triage (priority, category, whether they need the user).

    Args:
        bucket: 'needs_you' (important or with something to do), 'normal', or 'dismiss' (suggested to dismiss). Empty = all.
        app: Only this app (app name or Android package, e.g. 'WhatsApp').
        hours: How far back to look (default 24).
        include_dismissed: Include notifications already dismissed/opened.
        limit: Max results (default 30).
    """
    since = (clock.now() - datetime.timedelta(hours=max(1, hours))).isoformat(timespec="seconds")
    items = store.list_notifications(
        conn.user, status="all" if include_dismissed else "active",
        bucket=bucket if bucket in BUCKETS else None,
        since=since, limit=500)
    if app:
        a = app.strip().lower()
        items = [n for n in items if a in (n.get("app_name") or "").lower() or a in n["package"].lower()]
    items = items[:max(1, min(limit, 100))]
    if not items:
        return "No matching notifications."
    c = store.counts(conn.user)
    head = (f"Active now: {c['needs_you']} need you, {c['normal']} normal, {c['dismiss']} suggested to dismiss; "
            f"{c['pending_suggestions']} pending suggestion(s).\n")
    return head + "\n".join(_line(n) for n in items)


@addon.tool()
async def summarize_notifications(conn: Connection) -> str:
    """Summarize what is waiting in the user's phone notifications Inbox: a short briefing plus one summary per app (who wrote what, what needs the user). Use for "what did I miss?" or "anything important on my phone?".
    """
    data = ns.get_summaries(conn.user, refresh=False)
    if any(a["stale"] for a in data["apps"].values()) or (data["overall"] or {}).get("stale"):
        await ns.refresh_now(conn.user)
        data = ns.get_summaries(conn.user, refresh=False)
    if not data["overall"] and not data["apps"]:
        return "The notifications Inbox is (almost) empty — nothing to summarize. Use list_notifications to see single items."
    lines = []
    if data["overall"] and data["overall"].get("summary"):
        lines.append(f"Briefing ({data['overall']['count']} active): " + "; ".join(data["overall"]["points"]))
    for a in sorted(data["apps"].values(), key=lambda a: -a["count"]):
        if a.get("summary"):
            lines.append(f"- {a['app_name']} ({a['count']}): " + "; ".join(a["points"]))
    lines.append("(Apps with a single notification are not summarized; list_notifications shows every item.)")
    return "\n".join(lines)


def _snapshot(user: User, ids: list) -> dict:
    """id → {status, snoozed_until} of ``user``'s notifications before a change (for Undo)."""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    with get_db_connection() as conn:
        rows = conn.execute(f"SELECT id, status, snoozed_until FROM notifications WHERE user_id = ? AND id IN ({marks})",
                            [user.id, *ids]).fetchall()
    return {r["id"]: {"status": r["status"], "snoozed_until": r["snoozed_until"]} for r in rows}


def _future(iso: str):
    try:
        t = datetime.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return t if t > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=30) else None


def _resnooze(user: User, groups: dict) -> None:
    for until, ids in groups.items():
        if ids:
            shade.snooze(user, ids, until)


def _record_dismiss_undo(user: User, ids: list, before: dict) -> None:
    def _undo():
        now = _snapshot(user, ids)
        # Only the ones still dismissed: one restored or archived since is left alone.
        back = [i for i in ids if (now.get(i) or {}).get("status") == "dismissed"]
        if not back:
            return
        shade.set_status(user, back, "active")
        groups: dict = {}
        for i in back:
            until = _future(before[i].get("snoozed_until"))
            if until:
                groups.setdefault(until, []).append(i)
        _resnooze(user, groups)

    safe_record(f"Dismissed {len(ids)} notification(s)", _undo, "dismiss_notifications")


def _record_snooze_undo(user: User, ids: list, before: dict) -> None:
    if not ids:
        return
    after = _snapshot(user, ids)

    def _undo():
        now = _snapshot(user, ids)
        # Only the ones still snoozed exactly as this call left them.
        mine = [i for i in ids if now.get(i) == after.get(i)]
        groups: dict = {}
        for i in mine:
            groups.setdefault(_future(before[i].get("snoozed_until")), []).append(i)
        _resnooze(user, groups)

    safe_record(f"Snoozed {len(ids)} notification(s)", _undo, "snooze_notifications")


@addon.tool(is_write=True)
async def dismiss_notifications(conn: Connection, notification_ids: str = "", bucket: Optional[str] = None) -> str:
    """Dismiss notifications in Dana's Inbox (they are also cleared from the phone's notification shade on its next sync).

    Pass either specific IDs or a bucket. Dismissed notifications can be restored from the app.

    Args:
        notification_ids: Comma-separated notification IDs (from list_notifications / search_notifications).
        bucket: Dismiss every active notification in this bucket instead, usually 'dismiss' (suggested to dismiss).
    """
    user = conn.user
    ids = [i.strip() for i in (notification_ids or "").split(",") if i.strip()]
    if not ids and bucket in BUCKETS:
        ids = [n["id"] for n in store.list_notifications(user, status="active", bucket=bucket, limit=1000,
                                                         with_suggestions=False)]
    if not ids:
        return "Nothing to dismiss: give notification_ids or a bucket."
    before = _snapshot(user, ids)
    result = shade.set_status(user, ids, "dismissed", reason="dana")
    was_active = [i for i, r in before.items() if r["status"] == "active"]
    if result["updated"] and was_active:
        _record_dismiss_undo(user, was_active, before)
    return f"Dismissed {result['updated']} notification(s)."


@addon.tool(is_write=True)
async def snooze_notifications(conn: Connection, notification_ids: str, minutes: int = 0, until: str = "") -> str:
    """Snooze notifications: hide them from Dana's Inbox and the phone's notification shade until a time, then they come back.

    Give either minutes or until.

    Args:
        notification_ids: Comma-separated notification IDs (from list_notifications / search_notifications).
        minutes: Snooze for this many minutes (e.g. 60).
        until: Snooze until this local time, ISO format (e.g. '2026-11-10T20:00').
    """
    user = conn.user
    ids = [i.strip() for i in (notification_ids or "").split(",") if i.strip()]
    if not ids:
        return "Nothing to snooze: give notification_ids."
    try:
        if until:
            t = datetime.datetime.fromisoformat(until)
            when = t if t.tzinfo else t.replace(tzinfo=clock.now().tzinfo)
        elif minutes and minutes > 0:
            when = clock.now() + datetime.timedelta(minutes=minutes)
        else:
            return "Give minutes or until."
        before = _snapshot(user, ids)
        result = shade.snooze(user, ids, when)
    except ValueError as e:
        return f"Couldn't snooze: {e}"
    if result["updated"]:
        _record_snooze_undo(user, [i for i, r in before.items() if r["status"] == "active"], before)
    return f"Snoozed {result['updated']} notification(s) until {str(result['until'])[:16].replace('T', ' ')}."
