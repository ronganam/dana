"""Changing a user's rule and re-bucketing what it covers, with undo.

Changing or removing a rule re-buckets the app's active notifications from
the stored triage answers (:func:`stored_answers` → ``triage.decide()``, no
model call), so un-muting an app puts them back where the triage put them;
ones muted at ingest (never answered) get their first triage.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from core import undo
from core.db import get_db_connection
from core.users import User
from addons.phone.notifications import processing, rules, triage
from addons.phone.notifications.values import s

_NOUL_QUESTIONS = ("needs_action", "from_person", "time_sensitive", "urgent_request", "promotional",
                   "owner_wrote_it", "addressed_to_owner", "tracked_elsewhere", "record_only", "self_clearing")


def stored_answers(n: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The triage answers as ``decide()`` takes them, rebuilt from what the
    triage stored (``triage`` signals + ``category`` + ``priority_score``).
    None when the model never answered (muted at ingest, failed, pending)."""
    try:
        tri = json.loads(n.get("triage") or "{}") or {}
    except (TypeError, ValueError):
        return None
    if not isinstance(tri, dict) or "needs_action" not in tri:
        return None
    answers: Dict[str, Any] = {
        "category": {"choice": n.get("category") or "other", "probabilities": tri.get("category_probabilities") or {}},
        "priority": {"score": n.get("priority_score"), "probabilities": tri.get("priority_probabilities") or {}},
    }
    for q in _NOUL_QUESTIONS:
        if q in tri:
            answers[q] = {"noul": tri[q]}
    return answers


def reapply_rule(user: User, package: str, sender: str, action: str, *,
                 before: Optional[List[Dict[str, Any]]] = None) -> int:
    """Re-bucket ``user``'s active notifications a rule change covers, as the
    triage would now. Each row is judged under the rule that applies to it
    after the change (a sender rule over the app's): with stored answers,
    through ``triage.decide()`` (no model call) — so removing a rule puts the
    rows back where the triage put them without it; rows the model never
    answered (muted at ingest) get mute → dismiss, important / alert →
    needs_you, no rule → 'normal' and are queued for their first triage.
    ``before`` collects ``{id, bucket, priority}`` of every row changed (for
    :func:`restore_buckets`). Returns how many rows the rule covers.
    ``action`` is kept for callers; the stored rules decide."""
    where = "user_id = ? AND package = ? AND status = 'active'" + (" AND sender = ?" if sender else "")
    params: List[Any] = [user.id, package] + ([sender] if sender else [])
    untriaged: List[str] = []
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT id, sender, bucket, priority, priority_score, category, triage, triage_status "
            f"FROM notifications WHERE {where}", params)]
        low = rules.is_low_engagement(rules.engagement(conn, user, package)) if rows else False
        for r in rows:
            rule = rules.rule_for(conn, user, package, r.get("sender") or "")
            answers = stored_answers(r)
            if answers is not None:
                d = triage.decide(answers, rule=rule, low_engagement=low)
                bucket, pr = d["bucket"], d["priority"]
            elif rule == "mute":
                bucket, pr = "dismiss", r["priority"]
            elif rule in ("important", "alert"):
                bucket = "needs_you"
                pr = r["priority"] if r["priority"] in ("important", "urgent") else "important"
            else:
                bucket, pr = "normal", r["priority"]
                if r.get("triage_status") != "done":
                    untriaged.append(r["id"])
            if (bucket, pr) == (r["bucket"], r["priority"]):
                continue
            if before is not None:
                before.append({"id": r["id"], "bucket": r["bucket"], "priority": r["priority"]})
            conn.execute("UPDATE notifications SET bucket = ?, priority = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (bucket, pr, r["id"]))
        conn.commit()
    processing.schedule(user, untriaged)
    return len(rows)


def restore_buckets(user: User, before: List[Dict[str, Any]]) -> int:
    """Undo of :func:`reapply_rule`: put bucket and priority back."""
    n = 0
    with get_db_connection() as conn:
        for b in before:
            n += conn.execute("UPDATE notifications SET bucket = ?, priority = ?, updated_at = CURRENT_TIMESTAMP "
                              "WHERE id = ? AND user_id = ?", (b["bucket"], b["priority"], b["id"], user.id)).rowcount
        conn.commit()
    return n


def change_rule(user: User, package: str, action: str, sender: str = "", *, apply: bool = True,
                source: str = "notification_rules") -> Dict[str, Any]:
    """Set (or remove, ``none``) ``user``'s rule, re-bucket what it covers, and
    register an undo that restores the previous rule and the buckets it moved.

    Returns ``{package, sender, action, reapplied?, moved, message, undo_id}``."""
    package, sender = s(package, 200), s(sender, 200)
    with get_db_connection() as conn:
        prev = conn.execute("SELECT action FROM notification_rules WHERE user_id = ? AND package = ? AND sender = ?",
                            (user.id, package, sender)).fetchone()
        app = rules.app_name(conn, user, package)
    prev_action = prev["action"] if prev else None
    rule = rules.set_rule(user, package, action, sender)  # validates; ValueError for a bad request
    before: List[Dict[str, Any]] = []
    if apply:
        rule["reapplied"] = reapply_rule(user, package, sender, action, before=before)
    label = rules.rule_label(app, sender, action)

    def _undo():
        rules.set_rule(user, package, prev_action or "none", sender)
        restore_buckets(user, before)
    rule.update(moved=len(before), message=label, undo_id=undo.record(label, _undo, source=source))
    return rule
