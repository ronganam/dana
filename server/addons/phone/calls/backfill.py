"""Backfill the structured call analysis onto calls uploaded before it existed.

    python -m addons.phone.calls.backfill                 # dry run → report only
    python -m addons.phone.calls.backfill --apply         # write labels/analysis/suggestions
    python -m addons.phone.calls.backfill --apply --redo  # also re-run calls that already have an analysis
    python -m addons.phone.calls.backfill --user <id>     # one user (default: every user with phone)

Runs per user, as that user (usage rows are theirs), over their own calls
with their own context. Per call:
- unusable transcript → status 'failed' (no model call);
- an empty call at the same time/contact as a real one → status 'duplicate';
- otherwise one structured analysis (from the stored transcript): labels,
  importance, participants, key facts, action items, project link. The
  existing title/summary are kept unless they are broken (raw list/dict text,
  failed analyses, placeholders);
- suggestions only for action items dated in the future.

Each user's report (JSON) is written next to the DB as
``call_backfill_report_<user id>.json``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from collections import Counter
from typing import List, Optional

from core import db, paths, users
from core.db import get_db_connection
from core.users import User
from addons.phone import config as phone_config
from addons.phone.calls import store as calls_store
from addons.phone.calls.analysis import analyze_transcript
from addons.phone.calls.context import gather_context
from addons.phone.calls.labels import label_text
from addons.phone.calls.processing import apply_analysis
from addons.phone.calls.suggest import replace_pending_suggestions, suggestions_from_analysis
from addons.phone.calls.values import transcript_usable

logger = logging.getLogger("call_backfill")

BROKEN_TITLES = {"call recording", "transcript analysis failed", ""}
BROKEN_SUMMARY_MARKERS = ("Failed to analyze", "No summary generated", "could not be processed",
                          "Summary unavailable", "No summary available")


def text_is_broken(title: str, summary: str) -> bool:
    t = (title or "").strip()
    s = (summary or "").strip()
    if t.lower() in BROKEN_TITLES or t.startswith("Call with ") or t.startswith("Call to "):
        return True
    if s.startswith("[") or s.startswith("{"):
        return True
    return any(m in s for m in BROKEN_SUMMARY_MARKERS)


async def run(user: User, apply: bool, redo: bool, limit: int | None) -> dict:
    """Backfill ``user``'s calls, as ``user``; returns the report (also written to a file)."""
    with users.acting_as(user):
        return await _run(user, apply, redo, limit)


async def _run(user: User, apply: bool, redo: bool, limit: int | None) -> dict:
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM phone_calls WHERE user_id = ? ORDER BY call_timestamp",
                                              (user.id,)).fetchall()]

    # Empty twins of real calls (same contact + timestamp) are duplicates.
    by_key: dict = {}
    for r in rows:
        by_key.setdefault((r["caller"], r["receiver"], r["call_timestamp"]), []).append(r)
    duplicates = set()
    for group in by_key.values():
        if len(group) > 1 and any(transcript_usable(g["transcript"]) for g in group):
            for g in group:
                if not transcript_usable(g["transcript"]):
                    duplicates.add(g["id"])

    ctx = await gather_context(user)
    report = {"user_id": user.id, "apply": apply, "calls": [], "label_counts": {}, "suggestions": 0, "regenerated_text": 0,
              "failed": 0, "duplicates": 0, "errors": 0}
    labels_counter: Counter = Counter()
    done = 0

    for r in rows:
        entry = {"id": r["id"], "when": r["call_timestamp"], "contact": calls_store.contact_of(r),
                 "old_title": r["title"]}
        if r["id"] in duplicates:
            entry["result"] = "duplicate"
            report["duplicates"] += 1
            if apply:
                with get_db_connection() as conn:
                    # scoped: ids from this user's calls (query above)
                    conn.execute("UPDATE phone_calls SET status = 'duplicate' WHERE id = ?", (r["id"],))
                    conn.commit()
            report["calls"].append(entry)
            continue
        if not transcript_usable(r["transcript"]):
            entry["result"] = "failed (no usable transcript)"
            report["failed"] += 1
            if apply:
                with get_db_connection() as conn:
                    # scoped: ids from this user's calls (query above)
                    conn.execute("UPDATE phone_calls SET status = 'failed', error = 'No usable transcript' WHERE id = ?",
                                 (r["id"],))
                    conn.commit()
            report["calls"].append(entry)
            continue
        if r.get("analysis") and not redo:
            entry["result"] = "skipped (already analysed)"
            report["calls"].append(entry)
            continue
        if limit is not None and done >= limit:
            entry["result"] = "skipped (limit)"
            report["calls"].append(entry)
            continue
        done += 1

        call = calls_store.call_dict(r)
        try:
            analysis = await analyze_transcript(user, r["transcript"], call, ctx)
        except Exception as e:
            entry["result"] = f"error: {e}"
            report["errors"] += 1
            report["calls"].append(entry)
            continue

        regenerate = text_is_broken(r["title"], r["summary"])
        suggestions = suggestions_from_analysis(user, call, analysis, only_future=True,
                                                open_tasks=ctx["open_tasks"])
        labels_counter.update(analysis["labels"])
        report["suggestions"] += len(suggestions)
        report["regenerated_text"] += int(regenerate)
        entry.update({
            "result": "analysed",
            "labels": [label_text(user, l) for l in analysis["labels"]],
            "importance": analysis["importance"],
            "project_id": analysis.get("project_id"),
            "regenerated_text": regenerate,
            "new_title": analysis["title"] if regenerate else None,
            "action_items": [f"[{i['owner']}/{i['kind']} {i['confidence']}] {i['text']}" for i in analysis["action_items"]],
            "suggestions": [f"{s['kind']}: {s['payload'].get('title')}" for s in suggestions],
            "security_alert": analysis.get("security_alert"),
        })
        if apply:
            apply_analysis(user, r["id"], analysis, replace_text=regenerate, create_suggestions=False)
            with get_db_connection() as conn:
                replace_pending_suggestions(conn, user, r["id"], suggestions)
                conn.commit()
        report["calls"].append(entry)
        print(f"{r['call_timestamp'][:16]} {entry['contact'][:18]:18s} {', '.join(entry['labels']):40s} "
              f"{'✎ ' + analysis['title'] if regenerate else ''} {'(' + str(len(suggestions)) + ' sugg)' if suggestions else ''}",
              flush=True)

    report["label_counts"] = dict(labels_counter.most_common())
    out = os.path.join(str(paths.DATA_DIR), f"call_backfill_report_{user.id}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != "calls"}, ensure_ascii=False, indent=2))
    print(f"Report: {out}")
    return report


async def run_users(user_id: Optional[str], apply: bool, redo: bool, limit: int | None) -> List[dict]:
    """:func:`run` for one user (``user_id``) or every user with ``phone``, one after another."""
    db.migrate()
    targets = [users.get(user_id)] if user_id else phone_config.phone_users()
    reports = []
    for user in targets:
        if user is None:
            raise SystemExit(f"No user {user_id}")
        reports.append(await run(user, apply, redo, limit))
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the results (default: dry run)")
    parser.add_argument("--redo", action="store_true", help="re-analyse calls that already have an analysis")
    parser.add_argument("--limit", type=int, default=None, help="analyse at most N calls (per user)")
    parser.add_argument("--user", default=None, help="the user id to backfill (default: every user with phone)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run_users(args.user, args.apply, args.redo, args.limit))


if __name__ == "__main__":
    main()
