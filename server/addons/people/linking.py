"""Linking a user's calls and notifications to their people.

Calls match by number, then by contact name (and the number is added to that
person); a call from someone new creates a person (``source=auto``;
number-only callers are created hidden). A caller the call does not name is
called what the user's Google contacts call that number, when the user has
``contacts`` (``addons.contacts.store.caller_name``). Notifications
only match existing people by sender (or a 1:1 conversation); no people are
made from notifications. Only ``user``'s activity (``activity.calls_sql`` /
``notifications_sql``) is read, and only ``user``'s people are matched.
"""
from __future__ import annotations

import logging
from typing import Dict

from core.db import get_db_connection
from core.users import User
from addons.contacts import store as contacts
from addons.people import activity, store
from addons.people.values import UNKNOWN_NAMES, looks_like_number, norm_name, norm_phone

logger = logging.getLogger(__name__)


def link_pending(user: User) -> Dict[str, int]:
    """Match ``user``'s calls and notifications not checked yet; create people for new callers.
    Nothing to do for a user without ``people``."""
    if not store.has_people(user):
        return {"created": 0, "linked": 0}
    made = linked = 0
    with get_db_connection() as conn:
        calls = activity.unchecked_calls(conn, user)
        notes = activity.unchecked_notifications(conn, user)
        if not calls and not notes:
            return {"created": 0, "linked": 0}
        phones, names = store.phone_map(conn, user), store.alias_map(conn, user)
        for c in calls:
            caller = (c["caller"] or "").strip()
            contact = (c["receiver"] or "").strip() if caller == "Me" else caller
            num = norm_phone(c["phone_number"]) or (norm_phone(contact) if looks_like_number(contact) else "")
            pid = phones.get(num) if num else None
            key = norm_name(contact)
            if not pid and num and (key in UNKNOWN_NAMES or looks_like_number(contact)):
                known = contacts.caller_name(user, num)  # None without the contacts addon
                if known:
                    contact, key = known, norm_name(known)
            if not pid and key and key not in UNKNOWN_NAMES and not looks_like_number(contact):
                pid = names.get(key) or None
                if pid and num:
                    store.add_phone(conn, user, pid, num)
                    phones[num] = pid
            if not pid and (num or (key and key not in UNKNOWN_NAMES)):
                display = contact if key and key not in UNKNOWN_NAMES else (c["phone_number"] or num)
                p = store.create_person(user, display, phones=[num] if num else [], source="auto",
                                        hidden=looks_like_number(display), conn=conn, recheck=False)
                pid = p["id"]
                made += 1
                if num:
                    phones[num] = pid
                if key and key not in UNKNOWN_NAMES and not looks_like_number(contact):
                    names[key] = pid
            activity.set_call_person(conn, c["id"], pid or "")
            linked += 1 if pid else 0
        for n in notes:
            pid = None
            for cand in (n["sender"], None if n["is_group"] else n["conversation"]):
                k = norm_name(cand)
                if k and names.get(k):
                    pid = names[k]
                    break
            activity.set_notification_person(conn, n["id"], pid or "")
            linked += 1 if pid else 0
        conn.commit()
    if made:
        logger.info(f"People: {made} new from calls, {linked} items linked")
    return {"created": made, "linked": linked}
