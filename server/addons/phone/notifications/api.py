"""HTTP routes for phone notifications: the phone's sync, the Inbox and the
Notifications screen, rules, summaries, the digest and actions carried out on
the phone (docs/notifications.md).

Every route answers for the request's user, who must have ``phone`` (else 403
``not_connected``). The fixed paths (``/apps``, ``/overview``, ``/digest``, …)
are registered before ``/api/notifications/{id}``.
"""

import asyncio
import datetime
import logging

from aiohttp import web

from core import clock, undo
from core.db import get_db_connection
from addons.phone import config as phone_config, suggestions as phone_suggestions
from addons.phone.access import phone_user
from addons.phone.calls import actions as suggestion_actions
from addons.phone.notifications import (
    actions as notification_actions,
    buckets as notification_buckets,
    digest as notification_digest,
    ingest as notification_ingest,
    overview as notification_overview,
    processing as notification_processing,
    rules as notification_rules,
    shade as notification_shade,
    store as notification_store,
    summary as notification_summary,
)
from addons.phone.notifications.values import BUCKETS as NOTIFICATION_BUCKETS

logger = logging.getLogger(__name__)


async def notifications_batch_api(request: web.Request) -> web.Response:
    """POST /api/notifications/batch {posted: [...], removed: [{key, reason, at}], active_keys: [...], snoozed_keys?: [...]}

    → {accepted, duplicates, dropped, removed, ids, cancel_keys, snooze_keys}.
    ``cancel_keys`` are notifications still in the phone's shade that were
    dismissed in Dana (app, web or agent), or parked in its snooze list
    (``snoozed_keys``); the phone cancels them.
    ``snooze_keys`` [{key, until_ms}] were snoozed in Dana; the phone snoozes them."""
    user = phone_user(request)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if not isinstance(data, dict):
        return web.json_response({"error": "Expected an object"}, status=400)
    result = await asyncio.to_thread(notification_ingest.ingest, user, data.get("posted") or [],
                                     data.get("removed") or [])
    notification_processing.schedule(user, result["ids"])
    phone_config.mark("notification_sync", user.id, True, f"{result['accepted']} new")
    # Snoozed ones are parked outside the shade; a dismissal must reach them too.
    parked = [k for k in (data.get("snoozed_keys") or []) if isinstance(k, str)]
    result["cancel_keys"] = await asyncio.to_thread(
        notification_shade.keys_to_cancel, user, list(data.get("active_keys") or []) + parked)
    result["snooze_keys"] = await asyncio.to_thread(notification_shade.keys_to_snooze, user,
                                                    data.get("active_keys") or [])
    return web.json_response(result)


async def notifications_list_api(request: web.Request) -> web.Response:
    """GET /api/notifications?status=active|snoozed|dismissed|archived|all&bucket=&category=&package=&q=&since=&limit="""
    user = phone_user(request)
    q = request.query
    items = await asyncio.to_thread(
        notification_store.list_notifications, user, status=q.get("status", "active"), bucket=q.get("bucket") or None,
        category=q.get("category") or None, package=q.get("package") or None, q=q.get("q") or None,
        since=q.get("since") or None, limit=int(q.get("limit", "200") or 200))
    return web.json_response({"items": items, "counts": notification_store.counts(user)})


async def notification_detail_api(request: web.Request) -> web.Response:
    """GET /api/notifications/{id}"""
    n = notification_store.get_notification(phone_user(request), request.match_info["id"])
    if not n:
        return web.json_response({"error": "Notification not found"}, status=404)
    return web.json_response(n)


async def notifications_status_api(request: web.Request) -> web.Response:
    """POST /api/notifications/status {ids: [...] | package: "…" | bucket: "dismiss", status: dismissed|archived|active}

    → {updated, device_keys, undo_id} (the phone cancels device_keys in its
    shade). ``package`` = everything active from that app ("Clear" on the
    Notifications screen); ``bucket: "dismiss"`` also covers quiet (muted) apps
    ("Clear the noise"). Undo restores them in Dana, not in the phone's shade."""
    user = phone_user(request)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    ids = [i for i in (data.get("ids") or []) if isinstance(i, str)]
    if not ids and (data.get("package") or data.get("bucket") in NOTIFICATION_BUCKETS):
        ids = notification_overview.ids_for(user, package=str(data.get("package") or "") or None,
                                            bucket=None if data.get("package") else data.get("bucket"))
    status = data.get("status") or "dismissed"
    snap = notification_shade.snapshot(user, ids)
    try:
        result = notification_shade.set_status(user, ids, status)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    dismissed_sugg = []
    if data.get("dismiss_suggestions") and ids:
        for s in phone_suggestions.list_suggestions(user, "pending", source="notification", limit=1000):
            if s["source_id"] in set(ids):
                suggestion_actions.dismiss_suggestion(user, s["id"])
                dismissed_sugg.append(s["id"])
    if result["updated"]:
        def _undo():
            notification_shade.restore(user, snap)
            if dismissed_sugg:
                with get_db_connection() as conn:
                    for sid in dismissed_sugg:
                        conn.execute("UPDATE call_suggestions SET status = 'pending', resolved_at = NULL "
                                     "WHERE id = ? AND status = 'dismissed' AND user_id = ?", (sid, user.id))
                    conn.commit()
        n = result["updated"]
        verb = {"dismissed": "Cleared", "archived": "Archived", "active": "Restored"}.get(status, "Updated")
        result["undo_id"] = undo.record(f"{verb} {n} notification{'s' if n != 1 else ''}", _undo,
                                        source="notifications.status")
    return web.json_response(result)


async def notifications_overview_api(request: web.Request) -> web.Response:
    """GET /api/notifications/overview — the Notifications screen in one call
    (counts, apps with rule + cached summary, quiet apps, rule suggestions)."""
    user = phone_user(request)
    # On the loop, so a stale summary starts its background rewrite (never waited for).
    summaries = notification_summary.get_summaries(user, refresh=True)
    return web.json_response(await asyncio.to_thread(notification_overview.overview, user, summaries))


async def notifications_rule_suggestion_api(request: web.Request) -> web.Response:
    """POST /api/notifications/rule-suggestions {package, accept} → {ok, message, undo_id}

    accept → the app is kept quiet (mute rule); decline → not suggested again for 60 days."""
    user = phone_user(request)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    try:
        return web.json_response(notification_overview.answer_rule_suggestion(
            user, str((data or {}).get("package") or ""), bool((data or {}).get("accept"))))
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)


async def notifications_seen_api(request: web.Request) -> web.Response:
    """POST /api/notifications/seen {ids: [...]} — the user saw them (alert screen): stop re-nudging."""
    user = phone_user(request)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    ids = body.get("ids") if isinstance(body, dict) else None
    if not isinstance(ids, list):
        return web.json_response({"error": "ids must be a list"}, status=400)
    return web.json_response({"updated": notification_shade.mark_seen(user, ids)})


async def notifications_snooze_api(request: web.Request) -> web.Response:
    """POST /api/notifications/snooze {ids: [...], until?: ISO datetime | minutes?: int | clear?: true}

    → {updated, until, device_keys: [{key, until_ms}]} (the phone snoozes them
    in its shade; Android brings them back there at ``until``)."""
    user = phone_user(request)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if not isinstance(data, dict):
        return web.json_response({"error": "Expected an object"}, status=400)
    ids = [i for i in (data.get("ids") or []) if isinstance(i, str)]
    until = None
    if not data.get("clear"):
        try:
            if data.get("until"):
                until = datetime.datetime.fromisoformat(str(data["until"]).replace("Z", "+00:00"))
            elif data.get("minutes"):
                until = clock.now() + datetime.timedelta(minutes=int(data["minutes"]))
            else:
                return web.json_response({"error": "until, minutes or clear is required"}, status=400)
        except (TypeError, ValueError):
            return web.json_response({"error": "Invalid until/minutes"}, status=400)
    try:
        result = await asyncio.to_thread(notification_shade.snooze, user, ids, until)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response(result)


async def notifications_retriage_api(request: web.Request) -> web.Response:
    """POST /api/notifications/{id}/triage — run triage again (e.g. after a rule change)."""
    user = phone_user(request)
    nid = request.match_info["id"]
    if not notification_store.get_notification(user, nid):
        return web.json_response({"error": "Notification not found"}, status=404)
    decision = await notification_processing.process_notification(user, nid)
    return web.json_response({"decision": decision, "notification": notification_store.get_notification(user, nid)})


async def notifications_apps_api(request: web.Request) -> web.Response:
    """GET /api/notifications/apps — per-app counts + rules (last 30 days)."""
    user = phone_user(request)
    return web.json_response({"apps": notification_rules.apps_summary(user), "rules": notification_rules.list_rules(user)})


async def notification_act_api(request: web.Request) -> web.Response:
    """POST /api/notifications/{id}/act {action: "open" | <button title>, text?} → 202 {request}

    The phone carries it out (HA broadcast → it fetches the request); poll
    ``GET /api/notifications/requests/{rid}`` for ``done`` / ``failed`` / ``expired``."""
    user = phone_user(request)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    try:
        req = await notification_actions.request_action(user, request.match_info["id"], data.get("action") or "",
                                                        data.get("text") or "")
    except LookupError as e:
        return web.json_response({"error": str(e)}, status=404)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response(req, status=202)


async def notification_request_api(request: web.Request) -> web.Response:
    """GET /api/notifications/requests/{rid} — the phone fetches what to do; the web polls the status."""
    req = notification_actions.get_request(phone_user(request), request.match_info["rid"])
    return web.json_response(req) if req else web.json_response({"error": "not found"}, status=404)


async def notification_request_result_api(request: web.Request) -> web.Response:
    """POST /api/notifications/requests/{rid}/result {ok, error?} — the phone's answer."""
    user = phone_user(request)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    req = notification_actions.finish_request(user, request.match_info["rid"], bool(data.get("ok")),
                                              str(data.get("error") or "")[:300])
    return web.json_response(req) if req else web.json_response({"error": "not found"}, status=404)


async def notifications_summaries_api(request: web.Request) -> web.Response:
    """GET /api/notifications/summaries[?refresh=1] — AI summary per app + the Inbox briefing.

    Returns what is cached right away (``stale`` = being rewritten, ``refreshing``
    = a rewrite is running); ``refresh=1`` rewrites all of them."""
    user = phone_user(request)
    force = request.query.get("refresh", "") in ("1", "true", "yes")
    return web.json_response(notification_summary.get_summaries(user, force=force))


async def notifications_rule_api(request: web.Request) -> web.Response:
    """POST /api/notifications/rules {package, sender?, action: mute|important|alert|none, apply?: true}
    → {package, sender, action, reapplied?, moved, message, undo_id}

    With ``apply`` the app's active notifications are re-bucketed under the new
    rule (removing a rule puts them back where the triage put them). The undo
    restores the previous rule and the buckets it moved."""
    user = phone_user(request)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    try:
        rule = notification_buckets.change_rule(user, data.get("package") or "", data.get("action") or "",
                                                data.get("sender") or "", apply=bool(data.get("apply", True)))
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response(rule)


async def notifications_delete_app_api(request: web.Request) -> web.Response:
    """DELETE /api/notifications?package= — forget an app's notifications (kept: ones a task came from)."""
    user = phone_user(request)
    package = request.query.get("package", "").strip()
    if not package:
        return web.json_response({"error": "package is required"}, status=400)
    return web.json_response({"deleted": notification_store.delete_package(user, package)})


async def notifications_digest_api(request: web.Request) -> web.Response:
    """GET /api/notifications/digest?hours=12 — the digest text (preview; nothing is sent)."""
    user = phone_user(request)
    hours = int(request.query.get("hours", "12") or 12)
    since = (clock.now() - datetime.timedelta(hours=max(1, hours))).isoformat(timespec="seconds")
    return web.json_response(notification_digest.build_digest(user, since) or {"text": "Nothing new.", "total": 0})


def setup(app: web.Application) -> None:
    """Register the notification routes (fixed paths before ``{id}``)."""
    app.router.add_post("/api/notifications/batch", notifications_batch_api)
    app.router.add_get("/api/notifications", notifications_list_api)
    app.router.add_delete("/api/notifications", notifications_delete_app_api)
    app.router.add_post("/api/notifications/status", notifications_status_api)
    app.router.add_post("/api/notifications/snooze", notifications_snooze_api)
    app.router.add_post("/api/notifications/seen", notifications_seen_api)
    app.router.add_get("/api/notifications/apps", notifications_apps_api)
    app.router.add_get("/api/notifications/overview", notifications_overview_api)
    app.router.add_post("/api/notifications/rule-suggestions", notifications_rule_suggestion_api)
    app.router.add_get("/api/notifications/summaries", notifications_summaries_api)
    app.router.add_get("/api/notifications/requests/{rid}", notification_request_api)
    app.router.add_post("/api/notifications/requests/{rid}/result", notification_request_result_api)
    app.router.add_post("/api/notifications/{id}/act", notification_act_api)
    app.router.add_post("/api/notifications/rules", notifications_rule_api)
    app.router.add_get("/api/notifications/digest", notifications_digest_api)
    app.router.add_get("/api/notifications/{id}", notification_detail_api)
    app.router.add_post("/api/notifications/{id}/triage", notifications_retriage_api)
