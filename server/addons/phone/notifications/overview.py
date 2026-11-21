"""The Notifications screen in one call, rule suggestions and "clear the noise".

- :func:`overview` → ``GET /api/notifications/overview``: counts, apps (with the
  owner's rule in the screen's three words and the cached AI summary), quiet
  apps, and rule suggestions.
- Rules in the screen's words: ``alert`` (Interrupt me) = rule ``alert``;
  ``normal`` (Let Dana decide) = no rule, or ``important``; ``quiet`` (Keep
  quiet) = rule ``mute``.
- Noise = active, not snoozed notifications in the ``dismiss`` bucket ("Suggested
  to dismiss") plus anything from a quiet app / sender.
- Rule suggestions: an app the user keeps clearing without opening it (the triage's
  low engagement: ≥ 10 handled in 30 days, ≥ 90 % swiped) and that has no rule
  yet. Declining one is remembered for ``DECLINE_DAYS`` (60).

Everything here is the given user's: their notifications, rules and declines.
"""
from __future__ import annotations

import datetime
import logging
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

from core import clock, undo
from core.db import get_db_connection
from core.users import User
from addons.phone.notifications import buckets, rules as notification_rules, store, summary
from addons.phone.notifications.values import BUCKETS

logger = logging.getLogger(__name__)

DECLINE_DAYS = 60
MAX_RULE_SUGGESTIONS = 3
RULE_WORDS = {"alert": "alert", "mute": "quiet", "important": "normal"}


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _quiet_rules(conn: sqlite3.Connection, user: User) -> Tuple[Set[str], Set[Tuple[str, str]]]:
    """Muted packages, and muted (package, sender) pairs."""
    apps, senders = set(), set()
    for r in conn.execute("SELECT package, sender FROM notification_rules WHERE user_id = ? AND action = 'mute'",
                          (user.id,)).fetchall():
        if r["sender"]:
            senders.add((r["package"], r["sender"]))
        else:
            apps.add(r["package"])
    return apps, senders


def _active_rows(conn: sqlite3.Connection, user: User) -> List[Dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT id, package, app_name, title, text, sender, bucket, posted_at FROM notifications "
        "WHERE user_id = ? AND status = 'active' AND (snoozed_until IS NULL OR snoozed_until <= ?) "
        "ORDER BY posted_at DESC", (user.id, _utc_now())).fetchall()]


def _snoozed(conn: sqlite3.Connection, user: User) -> int:
    return conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND status = 'active' "
                        "AND snoozed_until > ?", (user.id, _utc_now())).fetchone()[0]


def _is_noise(n: Dict[str, Any], quiet_apps: Set[str], quiet_senders: Set[Tuple[str, str]]) -> bool:
    return n["bucket"] == "dismiss" or n["package"] in quiet_apps or (n["package"], n.get("sender") or "") in quiet_senders


def counts(user: User) -> Dict[str, int]:
    with get_db_connection() as conn:
        rows = _active_rows(conn, user)
        qa, qs = _quiet_rules(conn, user)
        snoozed = _snoozed(conn, user)
    noise = sum(1 for n in rows if _is_noise(n, qa, qs))
    needs = sum(1 for n in rows if n["bucket"] == "needs_you" and not _is_noise(n, qa, qs))
    return {"total": len(rows), "noise": noise, "needs_you": needs, "snoozed": snoozed}


def noise_ids(user: User) -> List[str]:
    with get_db_connection() as conn:
        rows = _active_rows(conn, user)
        qa, qs = _quiet_rules(conn, user)
    return [n["id"] for n in rows if _is_noise(n, qa, qs)]


def _declined(conn: sqlite3.Connection, user: User) -> Set[str]:
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=DECLINE_DAYS)).isoformat(timespec="seconds")
    return {r["package"] for r in conn.execute(
        "SELECT package FROM notification_rule_declines WHERE user_id = ? AND declined_at >= ?",
        (user.id, since)).fetchall()}


def rule_suggestions(user: User, conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """``user``'s apps to keep quiet: cleared again and again without being opened."""
    own = conn is None
    conn = conn or get_db_connection()
    try:
        since = (clock.now() - datetime.timedelta(days=30)).isoformat(timespec="seconds")
        ruled = {r["package"] for r in conn.execute(
            "SELECT package FROM notification_rules WHERE user_id = ? AND sender = ''", (user.id,))}
        declined = _declined(conn, user)
        # Never offer to quiet an app that has something waiting for the user
        # right now: the two would contradict each other on the same screen.
        waiting = {r["package"] for r in conn.execute(
            "SELECT DISTINCT package FROM notifications WHERE user_id = ? AND status = 'active' "
            "AND bucket = 'needs_you'", (user.id,))}
        out = []
        for r in conn.execute("SELECT package, MAX(app_name) AS app FROM notifications WHERE user_id = ? "
                              "AND posted_at >= ? GROUP BY package", (user.id, since)).fetchall():
            if r["package"] in ruled or r["package"] in declined or r["package"] in waiting:
                continue
            stats = notification_rules.engagement(conn, user, r["package"])
            if not notification_rules.is_low_engagement(stats):
                continue
            app = r["app"] or r["package"]
            opened = stats["opened"]
            tail = "without opening it" if not opened else f"and opened it only {opened} time{'s' if opened != 1 else ''}"
            out.append({"package": r["package"], "app": app, "action": "quiet", "cleared": stats["swiped"],
                        "opened": opened,
                        "reason": f"You cleared {app} {stats['swiped']} times this month {tail}."})
        out.sort(key=lambda s: -s["cleared"])
        return out[:MAX_RULE_SUGGESTIONS]
    finally:
        if own:
            conn.close()


def overview(user: User, summaries: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """``user``'s Notifications screen. ``summaries``: ``summary.get_summaries(user)``
    taken on the event loop (that is where it can start the background rewrite)."""
    if summaries is None:
        summaries = summary.get_summaries(user, refresh=True)
    cached = summaries.get("apps") or {}
    with get_db_connection() as conn:
        rows = _active_rows(conn, user)
        qa, qs = _quiet_rules(conn, user)
        rules = {r["package"]: r["action"] for r in conn.execute(
            "SELECT package, action FROM notification_rules WHERE user_id = ? AND sender = ''", (user.id,))}
        names = {r["package"]: r["app"] for r in conn.execute(
            "SELECT package, MAX(app_name) AS app FROM notifications WHERE user_id = ? GROUP BY package", (user.id,))}
        snoozed = _snoozed(conn, user)
        suggestions = rule_suggestions(user, conn)

    apps: Dict[str, Dict[str, Any]] = {}
    for n in rows:
        a = apps.get(n["package"])
        if a is None:
            a = apps[n["package"]] = {
                "package": n["package"], "app": n.get("app_name") or n["package"],
                "rule": RULE_WORDS.get(rules.get(n["package"]) or "", "normal"),
                "rule_action": rules.get(n["package"]), "count": 0, "needs_you": 0, "noise": 0,
                "summary": [], "summary_source": None, "latest_at": n["posted_at"], "_latest": n}
        a["count"] += 1
        noisy = _is_noise(n, qa, qs)
        a["noise"] += int(noisy)
        a["needs_you"] += int(n["bucket"] == "needs_you" and not noisy)
    for pkg, a in apps.items():
        pts = (cached.get(pkg) or {}).get("points") or []
        if pts:
            a["summary"], a["summary_source"] = pts, "ai"
        else:
            latest = a["_latest"]
            who = latest.get("sender") or latest.get("title") or ""
            text = " ".join((latest.get("text") or "").split())[:140]
            a["summary"] = [f"{who}: {text}" if who and text else (text or who)]
            a["summary_source"] = "latest"
        a.pop("_latest", None)
    # Quiet apps with nothing waiting are listed too, so the rule can be undone.
    for pkg, action in rules.items():
        if action == "mute" and pkg not in apps:
            apps[pkg] = {"package": pkg, "app": names.get(pkg) or pkg, "rule": "quiet", "rule_action": "mute",
                         "count": 0, "needs_you": 0, "noise": 0, "summary": [], "summary_source": None,
                         "latest_at": None}
    ordered = sorted(apps.values(), key=lambda a: a["latest_at"] or "", reverse=True)
    ordered.sort(key=lambda a: a["needs_you"] == 0)
    noise = sum(a["noise"] for a in ordered)
    needs = sum(a["needs_you"] for a in ordered)
    return {
        "counts": {"total": len(rows), "noise": noise, "needs_you": needs, "snoozed": snoozed},
        "apps": [a for a in ordered if a["rule"] != "quiet"],
        "quiet_apps": [a for a in ordered if a["rule"] == "quiet"],
        "rule_suggestions": suggestions,
        "summaries_refreshing": bool(summaries.get("refreshing")),
    }


def answer_rule_suggestion(user: User, package: str, accept: bool) -> Dict[str, Any]:
    """Accept → ``user``'s app gets the mute rule (re-applied to what's waiting);
    decline → not offered to them again for DECLINE_DAYS. Both can be undone."""
    package = (package or "").strip()
    if not package:
        raise ValueError("package is required")
    with get_db_connection() as conn:
        app = (conn.execute("SELECT MAX(app_name) AS a FROM notifications WHERE user_id = ? AND package = ?",
                            (user.id, package)).fetchone() or {"a": None})["a"] or package
        before_decline = conn.execute("SELECT declined_at FROM notification_rule_declines WHERE user_id = ? "
                                      "AND package = ?", (user.id, package)).fetchone()
    if accept:
        # Undo restores the previous rule and the buckets the mute moved.
        res = buckets.change_rule(user, package, "mute", source="rule_suggestion")
        return {"ok": True, "message": res["message"], "rule": "quiet", "undo_id": res["undo_id"]}
    with get_db_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO notification_rule_declines (user_id, package, declined_at) "
                     "VALUES (?, ?, ?)", (user.id, package, _utc_now()))
        conn.commit()
    label = f"Won't suggest that for {app} again"

    def _undo_decline():
        with get_db_connection() as conn:
            conn.execute("DELETE FROM notification_rule_declines WHERE user_id = ? AND package = ?",
                         (user.id, package))
            if before_decline:
                conn.execute("INSERT INTO notification_rule_declines (user_id, package, declined_at) "
                             "VALUES (?, ?, ?)", (user.id, package, before_decline["declined_at"]))
            conn.commit()
    return {"ok": True, "message": label, "undo_id": undo.record(label, _undo_decline, source="rule_suggestion")}


def ids_for(user: User, package: Optional[str] = None, bucket: Optional[str] = None) -> List[str]:
    """``user``'s active (not snoozed) notifications to clear by app, or by
    bucket; the ``dismiss`` bucket ("clear the noise") also covers quiet apps."""
    if package:
        return [n["id"] for n in store.list_notifications(
            user, status="active", package=package, limit=1000, with_suggestions=False)]
    if bucket in BUCKETS:
        ids = [n["id"] for n in store.list_notifications(
            user, status="active", bucket=bucket, limit=1000, with_suggestions=False)]
        if bucket == "dismiss":
            ids += [i for i in noise_ids(user) if i not in set(ids)]
        return ids
    return []
