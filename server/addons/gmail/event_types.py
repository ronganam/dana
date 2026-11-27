"""The event the ``gmail`` addon emits for the event catalog (../docs/addons.md,
"Events"): ``gmail.email.received``, once per new message in a user's Gmail
inbox, read from Gmail itself (docs/gmail.md, "New mail for watches").

:func:`poll` runs about every minute for each connected user (the ``inbox``
job in :mod:`.hooks`; the server is internal-only, so no Pub/Sub push): one
``users.history.list`` (``historyTypes=messageAdded``, ``labelId=INBOX``) from
the stored ``historyId`` (``gmail_inbox``). When nothing changed that is the
only request. New messages are read in one batch (``format=metadata``: From,
To, Cc, Subject, the snippet and labels); drafts, spam, trash, chats and sent
mail that is not in the inbox are skipped. Each emit is claimed first in
``gmail_received`` (user, message id), so it happens once across restarts; the
rows keep the emitted data: they are the event's history (dry runs), pruned
after :data:`KEEP_DAYS` days.

The first poll (no ``historyId`` yet), a ``historyId`` Gmail no longer knows
(404) and another account on the connection all start over the same way: the
current ``historyId`` from ``users.getProfile``, nothing emitted. A failed
poll is logged once (again only when the reason changes), never raises, and
leaves the ``historyId`` as it was, so the next poll catches up.

The data is other people's text: the watches engine frames it as untrusted;
nothing here puts it in a prompt.
"""
from __future__ import annotations

import datetime
import html
import json
import logging
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core import clock, events
from core.addons import Connection, EventType, store
from core.addons.hooks import PastEvent
from core.db import get_db_connection
from core.users import User
from addons.gmail import client, content
from addons.gmail.config import ADDON
from addons.phone.event_types import ils_amount

logger = logging.getLogger(__name__)

EVENT = "gmail.email.received"
KEEP_DAYS = 30
# History pages one poll reads at most (a page holds up to 100 records).
MAX_PAGES = 5
# Messages one poll announces at most (the newest; after a long outage the rest are skipped).
MAX_PER_POLL = 20
HEADERS = ("From", "To", "Cc", "Subject")
# A message with one of these labels is not new mail.
SKIPPED_LABELS = frozenset({"DRAFT", "SPAM", "TRASH", "CHAT"})

# user id -> the reason of the last failed poll (logged once; cleared by a poll that works).
_failing: Dict[str, str] = {}


# --- State ----------------------------------------------------------------------------------------

def _state(user: User) -> Tuple[str, str]:
    """(historyId, address) of ``user``'s inbox as last read ("" before the first poll)."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT history_id, address FROM gmail_inbox WHERE user_id = ?", (user.id,)).fetchone()
    return (row["history_id"], row["address"]) if row else ("", "")


def _save_state(user: User, history_id: str, address: str) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with get_db_connection() as conn:
        conn.execute("INSERT INTO gmail_inbox (user_id, history_id, address, updated_at) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(user_id) DO UPDATE SET history_id = excluded.history_id, "
                     "address = excluded.address, updated_at = excluded.updated_at",
                     (user.id, history_id, address, now))
        conn.commit()


# --- The event's data -----------------------------------------------------------------------------

def _same_address(a: str, b: str) -> bool:
    """One mailbox: case aside, and a ``+tag`` of the local part aside."""
    def norm(address: str) -> str:
        local, _, host = address.strip().lower().rpartition("@")
        return f"{local.split('+', 1)[0]}@{host}" if local else address.strip().lower()
    return bool(a and b) and norm(a) == norm(b)


def email_data(message: Mapping[str, Any]) -> Dict[str, Any]:
    """``gmail.email.received``'s data of one message (``format=metadata``)."""
    payload = message.get("payload")
    sender = content.addresses(content.raw_header(payload, "From"))
    from_name, from_email = (sender[0][0], sender[0][1].lower()) if sender else ("", "")
    to = content.addresses(content.raw_header(payload, "To"))
    cc = content.addresses(content.raw_header(payload, "Cc"))
    subject = content.header(payload, "Subject")
    snippet = content.one_line(html.unescape(str(message.get("snippet") or "")), content.SNIPPET_MAX_CHARS)
    return {"message_id": str(message.get("id") or ""), "thread_id": str(message.get("threadId") or ""),
            "from": content.show(sender[0]) if sender else content.header(payload, "From"),
            "from_name": from_name, "from_email": from_email,
            "to": content.show_all(to) if to else content.header(payload, "To"),
            "cc": content.show_all(cc) if cc else content.header(payload, "Cc"),
            "subject": subject, "snippet": snippet,
            "labels": ", ".join(str(label) for label in message.get("labelIds") or []),
            "amount": ils_amount(f"{subject}\n{snippet}")}


def is_new_mail(message: Mapping[str, Any]) -> bool:
    """In the inbox, and not a draft, spam, trash or a chat."""
    labels = set(message.get("labelIds") or [])
    return "INBOX" in labels and not labels & SKIPPED_LABELS


# --- Reading Gmail (blocking: in a worker thread) -------------------------------------------------

class _Expired(Exception):
    """Gmail no longer knows the stored historyId."""


def _baseline(svc: Any, conn: Connection) -> Tuple[str, str]:
    profile = svc.users().getProfile(userId=client.ME).execute()
    address = conn.account or str(profile.get("emailAddress") or "").strip()
    return str(profile.get("historyId") or ""), address


def _added_ids(svc: Any, start: str) -> Tuple[List[str], str]:
    """The ids of the messages added to the inbox since ``start`` (oldest first)
    and the historyId to read from next time."""
    ids: List[str] = []
    latest, page = start, None
    for _ in range(MAX_PAGES):
        params: Dict[str, Any] = {"userId": client.ME, "startHistoryId": start, "historyTypes": ["messageAdded"],
                                  "labelId": "INBOX", "maxResults": 100}
        if page:
            params["pageToken"] = page
        try:
            resp = svc.users().history().list(**params).execute()
        except Exception as e:
            if client.status_of(e) == 404:
                raise _Expired() from e
            raise
        for record in resp.get("history") or []:
            for added in record.get("messagesAdded") or []:
                mid = str((added.get("message") or {}).get("id") or "")
                if mid and mid not in ids:
                    ids.append(mid)
        latest = str(resp.get("historyId") or latest)
        page = resp.get("nextPageToken")
        if not page:
            break
    return ids, latest


def _fetch(conn: Connection, history_id: str, address: str) -> Tuple[str, str, List[Dict[str, Any]], bool]:
    """One poll's Gmail work: (historyId to store, address, the new messages, whether it started over)."""
    svc = client.service(conn)
    if not history_id or (conn.account and not _same_address(conn.account, address)):
        return (*_baseline(svc, conn), [], True)
    try:
        ids, latest = _added_ids(svc, history_id)
    except _Expired:
        logger.info(f"Gmail history of user {conn.user.id} expired: starting over from now")
        return (*_baseline(svc, conn), [], True)
    if not ids:
        return latest, address, [], False
    if len(ids) > MAX_PER_POLL:
        logger.info(f"Gmail of user {conn.user.id}: {len(ids)} new messages, announcing the newest {MAX_PER_POLL}")
        ids = ids[-MAX_PER_POLL:]
    users_api = svc.users().messages()
    done, failed = client.batch(svc, {mid: users_api.get(userId=client.ME, id=mid, format="metadata",
                                                         metadataHeaders=list(HEADERS)) for mid in ids})
    for mid, error in failed.items():
        if not client.missing(error):  # a message deleted since is simply gone
            logger.info(f"Gmail message of user {conn.user.id} not read: {client.failure(conn, error)}")
    return latest, address, [done[mid] for mid in ids if mid in done], False


# --- The poll -------------------------------------------------------------------------------------

def _claim(user: User, data: Dict[str, Any]) -> bool:
    """Record that this message is announced; False when it already was."""
    at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with get_db_connection() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO gmail_received (user_id, message_id, emitted_at, data) "
                           "VALUES (?, ?, ?, ?)", (user.id, data["message_id"], at,
                                                   json.dumps(data, ensure_ascii=False)))
        conn.commit()
        return cur.rowcount == 1


async def poll(user: User) -> int:
    """Emit ``gmail.email.received`` for each message added to ``user``'s inbox
    since the last poll; returns how many (0 without the addon, on the first
    poll and when it failed)."""
    conn = store.get(user, ADDON)
    if conn is None or store.reconnects(user, ADDON):
        return 0  # not connected, or the sign-in was refused: nothing to ask until they reconnect
    history_id, stored_address = _state(user)
    try:
        latest, address, messages, fresh = await client.run(_fetch, conn, history_id, stored_address)
    except Exception as e:
        reason = client.failure(conn, e)
        if _failing.get(user.id) != reason:
            logger.info(f"Gmail inbox of user {user.id} not read: {reason}")
        _failing[user.id] = reason
        return 0
    if _failing.pop(user.id, None) is not None:
        logger.info(f"Gmail inbox of user {user.id} read again")
    if fresh:
        logger.info(f"Gmail inbox of user {user.id}: watching for new mail from now on")
    n = 0
    for message in messages:
        if not is_new_mail(message):
            continue
        data = email_data(message)
        if data["message_id"] and _claim(user, data):
            events.emit(EVENT, user, **data)
            n += 1
    if latest and (fresh or (latest, address) != (history_id, stored_address)):
        _save_state(user, latest, address)
    return n


def prune(user: User, now: Optional[datetime.datetime] = None) -> int:
    """Forget ``user``'s announced messages older than :data:`KEEP_DAYS` days; returns how many."""
    cutoff = ((now or clock.now()) - datetime.timedelta(days=KEEP_DAYS)).astimezone(datetime.timezone.utc)
    with get_db_connection() as conn:
        cur = conn.execute("DELETE FROM gmail_received WHERE user_id = ? AND emitted_at < ?",
                           (user.id, cutoff.isoformat(timespec="seconds")))
        conn.commit()
        return cur.rowcount


def received_history(user: User, since: datetime.datetime) -> List[PastEvent]:
    """``user``'s announced emails since ``since`` (as emitted), oldest first."""
    utc = since.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")
    with get_db_connection() as conn:
        rows = conn.execute("SELECT emitted_at, data FROM gmail_received WHERE user_id = ? AND emitted_at >= ? "
                            "ORDER BY emitted_at", (user.id, utc)).fetchall()
    out: List[PastEvent] = []
    for row in rows:
        try:
            out.append((datetime.datetime.fromisoformat(row["emitted_at"]), json.loads(row["data"])))
        except (TypeError, ValueError):
            continue
    return out


GMAIL_EVENTS = (
    EventType(EVENT, "A new email arrived in the user's Gmail inbox (read from Gmail about every minute; "
                     "an email the user sent to their own address arrives too).",
              {"message_id": "the Gmail message id (read_email takes it)",
               "thread_id": "the Gmail thread id (read_email_thread takes it)",
               "from": "the sender as \"Name <address>\" (the address alone when there is no name)",
               "from_name": "the sender's display name (may be empty)",
               "from_email": "the sender's address, lowercase",
               "to": "the To recipients, comma-joined, each \"Name <address>\" or the address",
               "cc": "the Cc recipients, the same way (may be empty)",
               "subject": "the subject (may be empty)",
               "snippet": "the start of the body as Gmail shows it in the list (plain text, short)",
               "labels": "Gmail label ids, comma-joined (INBOX, UNREAD, IMPORTANT, CATEGORY_PERSONAL, "
                         "CATEGORY_PROMOTIONS, CATEGORY_UPDATES, user label ids ...)",
               "amount": "the first amount in shekels in the subject or snippet (ILS, positive), else null"},
              history=received_history),
)
