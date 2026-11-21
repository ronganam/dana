"""Gmail tools: each runs on the user's own account (``conn``: their refresh
token; addons/gmail/client.py). Reading returns email content framed as
material (``untrusted_result``: the rest of the turn asks before acting
outside Dana); sending asks first; archive / read state / trash / drafts run
at once and register an undo (docs/gmail.md)."""

import functools
import logging
import re
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from core.undoable import UndoConflict, safe_record
from core.users import User
from core.addons import Approval, Connection, NotConnected, store
from addons.gmail import client, compose, content, seen
from addons.gmail.client import ME
from addons.gmail.config import (BODY_MAX_CHARS, ADDON, MAX_BATCH, MAX_RESULTS, RESULT_MAX_CHARS,
                                       THREAD_BODY_CHARS, THREAD_MAX_MESSAGES, THREAD_MESSAGE_MAX_CHARS,
                                       THREAD_MESSAGE_MIN_CHARS)
from addons.gmail.addon import addon

logger = logging.getLogger(__name__)

_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

ToolFunction = Callable[..., Awaitable[str]]


def _guarded(doing: str, missing: str = "There is no such email in this Gmail account.") -> Callable[[ToolFunction], ToolFunction]:
    """A tool whose failures become its result: a refusal as it is, an
    unknown id as ``missing``, anything else as a short reason (never a token)."""
    def wrap(fn: ToolFunction) -> ToolFunction:
        @functools.wraps(fn)
        async def run(conn: Connection, *args: Any, **kwargs: Any) -> str:
            try:
                return await fn(conn, *args, **kwargs)
            except compose.Refused as e:
                return f"Error: {e}"
            except NotConnected:
                raise
            except Exception as e:
                if client.missing(e):
                    return f"Error: {missing}"
                logger.error(f"Gmail tool failed while {doing}: {client.failure(e)}", exc_info=True)
                return f"Error {doing}: {client.failure(e)}"
        return run
    return wrap


def _one_id(value: Any, what: str = "message") -> str:
    text = str(value or "").strip()
    if not _ID.fullmatch(text):
        raise compose.Refused(f"“{content.one_line(text, 40)}” is not a {what} ID; use one a search or a read returned.")
    return text


def _many_ids(value: Any) -> List[str]:
    items = value if isinstance(value, (list, tuple)) else re.split(r"[,\s]+", str(value or ""))
    ids: List[str] = []
    for item in items:
        if str(item or "").strip() and _one_id(item) not in ids:
            ids.append(_one_id(item))
    if not ids:
        raise compose.Refused("No message IDs were given.")
    if len(ids) > MAX_BATCH:
        raise compose.Refused(f"Too many messages at once ({len(ids)}; at most {MAX_BATCH}).")
    return ids


def _limit(value: Any, default: int = 10) -> int:
    try:
        return max(1, min(int(value), MAX_RESULTS))
    except (TypeError, ValueError):
        return default


def _count(n: int) -> str:
    return f"{n} email" + ("" if n == 1 else "s")


def _raise_unless_missing(failed: Mapping[str, Exception]) -> None:
    """Of the requests of a batch Gmail refused, only "no such message" is an answer."""
    for error in failed.values():
        if not client.missing(error):
            raise error


# --- Reading --------------------------------------------------------------------------------------

def _find(conn: Connection, query: str, labels: Sequence[str], limit: int) -> List[Dict[str, Any]]:
    svc = client.service(conn)
    asked: Dict[str, Any] = {"userId": ME, "maxResults": limit}
    if query:
        asked["q"] = query
    if labels:
        asked["labelIds"] = list(labels)
    listed = svc.users().messages().list(**asked).execute()
    ids = [str(m["id"]) for m in listed.get("messages") or [] if m.get("id")][:limit]
    found, failed = client.batch(svc, {i: svc.users().messages().get(
        userId=ME, id=i, format="full", fields=content.SUMMARY_FIELDS) for i in ids})
    _raise_unless_missing(failed)
    return [found[i] for i in ids if i in found]


def _listing(conn: Connection, messages: List[Dict[str, Any]], what: str, nothing: str, limit: int) -> str:
    if not messages:
        return nothing
    for message in messages:
        seen.remember(conn.user, message)
    shown = [content.summary_lines(m) for m in messages]

    def build() -> str:
        more = ""
        if len(shown) < len(messages):
            more = f" Only the first {len(shown)} fit here: narrow the search to see the others."
        elif len(messages) >= limit:
            more = f" There may be more: narrow the search or raise limit (at most {MAX_RESULTS})."
        return content.framed(conn.user, f"{what.format(n=_count(len(messages)))}, newest first.{more}",
                              [line for block in shown for line in block])

    out = build()
    while len(out) > RESULT_MAX_CHARS and len(shown) > 1:
        shown.pop()
        out = build()
    return out


@addon.tool(untrusted_result=True)
@_guarded("searching Gmail")
async def search_emails(conn: Connection, query: str, limit: int = 10) -> str:
    """Search the user's Gmail. Returns, newest first, each message's ID, thread ID, sender, recipients,
    subject, date, snippet, whether it is unread and whether it has attachments. Read a result with read_email.

    Args:
        query: A Gmail search, as typed in Gmail's search box: words, and operators such as from:dan,
            to:me, subject:invoice, is:unread, has:attachment, newer_than:7d, older_than:1m, in:sent,
            label:work, "an exact phrase". Empty: the newest mail.
        limit: The maximum number of messages to return (default 10, at most 25).
    """
    limit = _limit(limit)
    messages = await client.run(_find, conn, str(query or "").strip(), (), limit)
    return _listing(conn, messages, "Found {n}", "No emails match that search.", limit)


@addon.tool(untrusted_result=True)
@_guarded("reading the Gmail inbox")
async def list_unread_emails(conn: Connection, limit: int = 10) -> str:
    """List the unread messages in the user's Gmail inbox, newest first (the same details as search_emails).

    Args:
        limit: The maximum number of messages to return (default 10, at most 25).
    """
    limit = _limit(limit)
    messages = await client.run(_find, conn, "", ("INBOX", "UNREAD"), limit)
    return _listing(conn, messages, "{n} unread in the inbox", "No unread emails in the inbox.", limit)


def _get(conn: Connection, message_id: str) -> Dict[str, Any]:
    return client.service(conn).users().messages().get(userId=ME, id=message_id, format="full").execute()


def _within(build: Callable[[int], str], limit: int) -> str:
    """``build(limit)``, built again with shorter bodies when it is too long for a tool result."""
    out = build(limit)
    over = len(out) - RESULT_MAX_CHARS
    return build(max(200, limit - over - 200)) if over > 0 else out


@addon.tool(untrusted_result=True)
@_guarded("reading the email")
async def read_email(conn: Connection, message_id: str) -> str:
    """Read one email: who it is from and to, its subject and date, its text and the names of its
    attachments (attachments are listed, not opened). Earlier messages quoted at the end of a reply
    are left out (use read_email_thread for the conversation) and a very long text is cut, both marked.

    Args:
        message_id: The message ID, as search_emails / list_unread_emails / read_email_thread returned it.
    """
    message = await client.run(_get, conn, _one_id(message_id))
    seen.remember(conn.user, message)
    return _within(lambda limit: content.framed(conn.user, "One email.", content.message_lines(message, limit)),
                   BODY_MAX_CHARS)


def _get_thread(conn: Connection, thread_id: str) -> List[Dict[str, Any]]:
    thread = client.service(conn).users().threads().get(userId=ME, id=thread_id, format="full").execute()

    def arrived(message: Mapping[str, Any]) -> int:
        try:
            return int(message.get("internalDate") or 0)
        except (TypeError, ValueError):
            return 0
    return sorted(thread.get("messages") or [], key=arrived)


@addon.tool(untrusted_result=True)
@_guarded("reading the conversation", missing="There is no such conversation in this Gmail account.")
async def read_email_thread(conn: Connection, thread_id: str) -> str:
    """Read an email conversation: its messages oldest first, each with its sender, date and a shortened
    text (read_email gives one message in full). A long conversation shows its newest messages.

    Args:
        thread_id: The thread ID, as search_emails / list_unread_emails / read_email returned it.
    """
    messages = await client.run(_get_thread, conn, _one_id(thread_id, "thread"))
    if not messages:
        return "That conversation has no messages."
    for message in messages:
        seen.remember(conn.user, message)
    shown = messages[-THREAD_MAX_MESSAGES:]
    what = f"A conversation of {_count(len(messages))}, oldest first"
    if len(shown) < len(messages):
        what += f" (the newest {len(shown)} are shown)"

    def build(per_message: int) -> str:
        lines: List[str] = []
        for message in shown:
            lines.append(f"--- Message {messages.index(message) + 1} of {len(messages)} ---")
            lines += content.message_lines(message, per_message)
        return content.framed(conn.user, what + ".", lines)

    out = build(max(THREAD_MESSAGE_MIN_CHARS, min(THREAD_MESSAGE_MAX_CHARS, THREAD_BODY_CHARS // len(shown))))
    while len(out) > RESULT_MAX_CHARS and len(shown) > 1:
        shown = shown[1:]
        what = f"A conversation of {_count(len(messages))}, oldest first (the newest {len(shown)} are shown)"
        out = build(THREAD_MESSAGE_MIN_CHARS)
    return out


# --- Sending (asks first) -------------------------------------------------------------------------

def _send(conn: Connection, message: compose.Outgoing) -> Dict[str, Any]:
    return client.service(conn).users().messages().send(userId=ME, body=compose.body_for(message)).execute()


def _original(svc: Any, message_id: str) -> Dict[str, str]:
    """The reply headers of one of the account's messages."""
    return seen.reply_headers(svc.users().messages().get(
        userId=ME, id=message_id, format="metadata", metadataHeaders=list(content.REPLY_HEADERS)).execute())


def _addresses(recipients: Sequence[content.Recipient]) -> str:
    return ", ".join(address for _, address in recipients)


def _went_to(message: compose.Outgoing) -> str:
    out = _addresses(message.to)
    if message.cc:
        out += f" (cc {_addresses(message.cc)})"
    if message.bcc:
        out += f" (bcc {_addresses(message.bcc)})"
    return out


_RECIPIENTS_LIMIT = 4000


def _recipients(value: Any, field: str) -> str:
    if value in (None, "", []):
        return ""
    return compose.shown(value, field) or f"{content.one_line(value, 300)} (not a valid address)"


def _describe_send_email(user: Optional[User], a: Dict[str, Any]) -> Approval:
    to = _recipients(a.get("to"), "to")
    return Approval(f"Send an email to {to or 'nobody'}", "Email", "✉️", "Send this email?",
                    body_label="Message", body=a.get("body"),
                    facts=[("Subject", content.one_line(a.get("subject")) or "(no subject)"),
                           ("Cc", _recipients(a.get("cc"), "cc")), ("Bcc", _recipients(a.get("bcc"), "bcc"))],
                    fact_limit=_RECIPIENTS_LIMIT)


def _describe_reply_to_email(user: Optional[User], a: Dict[str, Any]) -> Approval:
    everyone = compose.flag(a.get("reply_all"))
    try:
        original = seen.get(user, a.get("message_id"))
        conn = store.get(user, ADDON) if user is not None and original else None
        reply = compose.reply(original, (conn.account if conn else "") or "", a.get("body"), everyone) \
            if original else None
    except Exception:
        reply = None
    if reply is None:
        return Approval("Reply to an email" + (" (to everyone on it)" if everyone else ""), "Email reply", "✉️",
                        "Send this reply?", body_label="Reply", body=a.get("body"),
                        facts=[("Message", a.get("message_id"))])
    return Approval(f"Reply to {content.show_all(reply.to, limit=len(reply.to))}", "Email reply", "✉️",
                    "Send this reply?", body_label="Reply", body=a.get("body"),
                    facts=[("Subject", reply.subject), ("Cc", content.show_all(reply.cc, limit=len(reply.cc)))],
                    fact_limit=_RECIPIENTS_LIMIT)


@addon.tool(is_write=True, confirm=True, describe=_describe_send_email)
@_guarded("sending the email")
async def send_email(conn: Connection, to: str, subject: str, body: str, cc: Optional[str] = None,
                     bcc: Optional[str] = None) -> str:
    """Send a new email from the user's Gmail. It waits for the user's Approve, which the system shows
    with the recipients, the subject and the text: call it with the complete message.

    Write the text the user asked for, in their words and language; add nothing they did not ask for.
    To answer a message the user received, use reply_to_email instead (it stays in the conversation).

    Args:
        to: The recipient's email address; several separated by commas.
        subject: The subject line.
        body: The text of the message (plain text).
        cc: Optional addresses to copy, separated by commas.
        bcc: Optional addresses to copy without the others seeing them, separated by commas.
    """
    message = compose.new_message(to, subject, body, cc, bcc)
    sent = await client.run(_send, conn, message)
    logger.info(f"Email sent for user {conn.user.id} to {len(message.everyone)} recipient(s)")
    return f"Sent the email to {_went_to(message)}. Message ID: {sent.get('id')}"


def _reply(conn: Connection, message_id: str, body: str, reply_all: bool) -> Tuple[compose.Outgoing, Dict[str, Any]]:
    svc = client.service(conn)
    message = compose.reply(_original(svc, message_id), client.own_address(conn, svc), body, reply_all)
    return message, svc.users().messages().send(userId=ME, body=compose.body_for(message)).execute()


@addon.tool(is_write=True, confirm=True, describe=_describe_reply_to_email)
@_guarded("sending the reply")
async def reply_to_email(conn: Connection, message_id: str, body: str, reply_all: bool = False) -> str:
    """Reply to an email the user received, in the same conversation (the subject and the threading
    are set for you). It waits for the user's Approve, which the system shows with who it goes to and
    the text: call it with the complete reply.

    Write the text the user asked for, in their words and language; do not quote the original message.

    Args:
        message_id: The ID of the message to answer, as a search or a read returned it.
        body: The text of the reply (plain text).
        reply_all: True to also send it to everyone else the message was sent to (as Cc). Default: only
            the sender.
    """
    message, sent = await client.run(_reply, conn, _one_id(message_id), body, compose.flag(reply_all))
    logger.info(f"Reply sent for user {conn.user.id} to {len(message.everyone)} recipient(s)")
    return f"Sent the reply to {_went_to(message)}, in the same conversation. Message ID: {sent.get('id')}"


# --- Changes that run at once, with Undo ----------------------------------------------------------

def _undo_service(user: User) -> Any:
    """The service of ``user``'s Gmail at undo time (their current
    connection; NotConnected once they disconnected it)."""
    return client.service(store.require(user, ADDON))


def _labels(svc: Any, ids: Sequence[str]) -> Dict[str, Set[str]]:
    """The labels of each of ``ids`` that the account has (the others are absent)."""
    found, failed = client.batch(svc, {i: svc.users().messages().get(userId=ME, id=i, format="minimal")
                                       for i in ids})
    _raise_unless_missing(failed)
    return {i: set(found[i].get("labelIds") or []) for i in ids if i in found}


def _modify(svc: Any, ids: Sequence[str], add: Sequence[str] = (), remove: Sequence[str] = ()) -> None:
    if ids:
        svc.users().messages().batchModify(userId=ME, body={"ids": list(ids), "addLabelIds": list(add),
                                                            "removeLabelIds": list(remove)}).execute()


def _outcome(done: str, changed: Sequence[str], already: Sequence[str], state: str, missing: Sequence[str]) -> str:
    """What a change did, in words: ``done`` with ``{n}`` for the emails it
    changed, those that already were in ``state``, the ids the account lacks."""
    parts = [done.format(n=_count(len(changed))) if changed else "Nothing was changed."]
    if already:
        parts.append(f"{_count(len(already))} {'was' if len(already) == 1 else 'were'} already {state}.")
    if missing:
        parts.append(f"Not found in this Gmail account: {', '.join(missing)}.")
    return " ".join(parts)


def _draft(conn: Connection, to: Any, subject: Any, body: Any,
           reply_to_message_id: Optional[str]) -> Tuple[compose.Outgoing, Dict[str, Any]]:
    svc = client.service(conn)
    if reply_to_message_id:
        message = compose.reply(_original(svc, reply_to_message_id), client.own_address(conn, svc), body,
                                to=to, subject=subject, draft=True)
    else:
        message = compose.new_message(to, subject, body, draft=True)
    return message, svc.users().drafts().create(userId=ME, body={"message": compose.body_for(message)}).execute()


def _delete_draft(user: User, draft_id: str, message_id: str) -> None:
    svc = _undo_service(user)
    try:
        current = svc.users().drafts().get(userId=ME, id=draft_id, format="minimal").execute()
    except Exception as e:
        if client.missing(e):
            raise UndoConflict("The draft is no longer there (sent or deleted in Gmail), so nothing was undone.")
        raise
    # Editing a draft in Gmail gives it a new message; the one the tool wrote is then gone.
    if message_id and (current.get("message") or {}).get("id") != message_id:
        raise UndoConflict("The draft was edited since it was created, so it was kept.")
    svc.users().drafts().delete(userId=ME, id=draft_id).execute()


@addon.tool(is_write=True)
@_guarded("saving the draft")
async def create_email_draft(conn: Connection, to: str, subject: str, body: str,
                             reply_to_message_id: Optional[str] = None) -> str:
    """Save an email as a draft in the user's Gmail, without sending it: for a message the user wants to
    look at, finish or send from Gmail. It is saved at once and can be undone (the draft is deleted).

    Args:
        to: The recipient's email address; several separated by commas. May be empty; with
            reply_to_message_id empty means the sender of that message.
        subject: The subject line. With reply_to_message_id empty means the usual "Re: …".
        body: The text of the message (plain text).
        reply_to_message_id: Optional ID of a message this draft answers: it is saved in that conversation.
    """
    answered = _one_id(reply_to_message_id) if str(reply_to_message_id or "").strip() else None
    message, created = await client.run(_draft, conn, to, subject, body, answered)
    draft_id = str(created.get("id") or "")
    if draft_id:
        user, written = conn.user, str((created.get("message") or {}).get("id") or "")

        async def undo() -> None:
            await client.run(_delete_draft, user, draft_id, written)
        safe_record("Saved an email draft", undo, "create_email_draft")
    where = f" to {_went_to(message)}" if message.to else ""
    return (f"Saved the draft{where}{' in the same conversation' if message.thread_id else ''}. "
            f"Draft ID: {draft_id}. It was not sent.")


def _archive(conn: Connection, ids: List[str]) -> Tuple[List[str], List[str], List[str]]:
    svc = client.service(conn)
    labels = _labels(svc, ids)
    changed = [i for i in ids if "INBOX" in labels.get(i, ())]
    _modify(svc, changed, remove=["INBOX"])
    return changed, [i for i in labels if i not in changed], [i for i in ids if i not in labels]


def _unarchive(user: User, ids: List[str]) -> None:
    svc = _undo_service(user)
    labels = _labels(svc, ids)
    back = [i for i in ids if i in labels and not labels[i] & {"INBOX", "TRASH"}]
    if not back and not all("INBOX" in labels.get(i, ()) for i in ids):
        raise UndoConflict("They were moved or deleted since, so they were left as they are.")
    _modify(svc, back, add=["INBOX"])


@addon.tool(is_write=True)
@_guarded("archiving")
async def archive_emails(conn: Connection, message_ids: List[str]) -> str:
    """Archive emails: take them out of the inbox (they stay in Gmail and in search). Runs at once and
    can be undone (they go back to the inbox).

    Args:
        message_ids: The IDs of the messages to archive, as a search or a read returned them.
    """
    changed, already, missing = await client.run(_archive, conn, _many_ids(message_ids))
    if changed:
        user = conn.user

        async def undo() -> None:
            await client.run(_unarchive, user, changed)
        safe_record(f"Archived {_count(len(changed))}", undo, "archive_emails")
    return _outcome("Archived {n}.", changed, already, "out of the inbox", missing)


def _mark(conn: Connection, ids: List[str], read: bool) -> Tuple[List[str], List[str], List[str]]:
    svc = client.service(conn)
    labels = _labels(svc, ids)
    changed = [i for i in ids if i in labels and ("UNREAD" in labels[i]) == read]
    _modify(svc, changed, **({"remove": ["UNREAD"]} if read else {"add": ["UNREAD"]}))
    return changed, [i for i in labels if i not in changed], [i for i in ids if i not in labels]


def _unmark(user: User, ids: List[str], read: bool) -> None:
    """Put ``ids`` back to how they were before :func:`_mark` made them
    ``read``: only those that still are as it left them."""
    svc = _undo_service(user)
    labels = _labels(svc, ids)
    back = [i for i in ids if i in labels and ("UNREAD" not in labels[i]) == read]
    _modify(svc, back, **({"add": ["UNREAD"]} if read else {"remove": ["UNREAD"]}))


@addon.tool(is_write=True)
@_guarded("marking the emails")
async def mark_emails_read(conn: Connection, message_ids: List[str], read: bool = True) -> str:
    """Mark emails as read, or as unread. Runs at once and can be undone (each goes back to how it was).

    Args:
        message_ids: The IDs of the messages, as a search or a read returned them.
        read: True (default) to mark them read, False to mark them unread.
    """
    read = compose.flag(read)
    changed, already, missing = await client.run(_mark, conn, _many_ids(message_ids), read)
    state = "read" if read else "unread"
    if changed:
        user = conn.user

        async def undo() -> None:
            await client.run(_unmark, user, changed, read)
        safe_record(f"Marked {_count(len(changed))} as {state}", undo, "mark_emails_read")
    return _outcome(f"Marked {{n}} as {state}.", changed, already, state, missing)


def _trash(conn: Connection, ids: List[str]) -> Tuple[List[str], List[str], List[str]]:
    svc = client.service(conn)
    labels = _labels(svc, ids)
    wanted = [i for i in ids if i in labels and "TRASH" not in labels[i]]
    done, failed = client.batch(svc, {i: svc.users().messages().trash(userId=ME, id=i) for i in wanted})
    _raise_unless_missing(failed)
    changed = [i for i in wanted if i in done]
    return changed, [i for i in labels if i not in wanted], [i for i in ids if i not in labels or i in failed]


def _untrash(user: User, ids: List[str]) -> None:
    svc = _undo_service(user)
    labels = _labels(svc, ids)
    back = [i for i in ids if "TRASH" in labels.get(i, ())]
    if not back and not all(i in labels for i in ids):
        raise UndoConflict("They are no longer in the trash (deleted for good), so they could not be restored.")
    _, failed = client.batch(svc, {i: svc.users().messages().untrash(userId=ME, id=i) for i in back})
    _raise_unless_missing(failed)


@addon.tool(is_write=True)
@_guarded("moving the emails to the trash")
async def trash_emails(conn: Connection, message_ids: List[str]) -> str:
    """Move emails to Gmail's trash (Gmail empties it after 30 days; nothing is deleted for good here).
    Runs at once and can be undone (they come back out of the trash).

    Args:
        message_ids: The IDs of the messages to move to the trash, as a search or a read returned them.
    """
    changed, already, missing = await client.run(_trash, conn, _many_ids(message_ids))
    if changed:
        user = conn.user

        async def undo() -> None:
            await client.run(_untrash, user, changed)
        safe_record(f"Moved {_count(len(changed))} to the trash", undo, "trash_emails")
    return _outcome("Moved {n} to the trash.", changed, already, "in the trash", missing)
