"""Messages the Gmail tools send or save as drafts: who they go to, checked,
and the RFC 5322 text Gmail gets (docs/gmail.md, "Sending").

Recipients and the subject come from the model, and in a reply from the
headers of someone else's message, so both are checked before anything is
built: a line break or control character in an address or the subject is
refused (it could add a header), every address must be a plain valid one, and
a message goes to at most :data:`~addons.gmail.config.MAX_RECIPIENTS`
addresses. :class:`Refused` says why; nothing is sent then.
"""

import base64
import re
from dataclasses import dataclass
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import getaddresses
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from addons.gmail import content
from addons.gmail.config import MAX_RECIPIENTS, OUTGOING_BODY_MAX_CHARS, SUBJECT_MAX_CHARS
from addons.gmail.content import Recipient

_BREAK = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029]")
_SUBJECT_BREAK = re.compile(r"[\r\n\x00\x0b\x0c\x85\u2028\u2029]")
_BODY_DROPPED = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MESSAGE_ID = re.compile(r"<[^<>\s]{1,250}>")
REFERENCES_KEPT = 20
# Plain ASCII on the wire: a non-ASCII body goes out base64 / quoted-printable.
_POLICY = policy.SMTP.clone(cte_type="7bit")


class Refused(ValueError):
    """A recipient, subject or body the tools do not send (the message says why)."""


@dataclass(frozen=True)
class Outgoing:
    """A message ready to be sent or saved: checked recipients, one-line
    subject, body, and for a reply the thread and the ids it answers."""
    to: Tuple[Recipient, ...] = ()
    cc: Tuple[Recipient, ...] = ()
    bcc: Tuple[Recipient, ...] = ()
    subject: str = ""
    body: str = ""
    in_reply_to: str = ""
    references: Tuple[str, ...] = ()
    thread_id: str = ""

    @property
    def everyone(self) -> Tuple[Recipient, ...]:
        return (*self.to, *self.cc, *self.bcc)


def flag(value: Any) -> bool:
    """A yes / no argument as the tools and the Approve card both read it
    (the word "false" from a model is False)."""
    return value.strip().lower() not in ("false", "0", "no", "") if isinstance(value, str) else bool(value)


def recipients(value: Any, field: str) -> List[Recipient]:
    """The addresses in ``value`` (one string with commas, or a list), each once.

    Raises:
        Refused: a line break or control character, or something that is not
            a plain valid address.
    """
    if value is None:
        return []
    out: List[Recipient] = []
    seen = set()
    for item in value if isinstance(value, (list, tuple)) else [value]:
        if not isinstance(item, str):
            raise Refused(f"{field}: an address must be text.")
        if _BREAK.search(item):
            raise Refused(f"{field}: an address cannot contain a line break or a control character.")
        text = item.replace(";", ",").strip().strip(",").strip()
        if not text:
            continue
        for name, address in getaddresses([text]):
            address = address.strip()
            if not name and not address and "," in text:
                continue  # an empty place between two commas
            # ``address in text``: the parser joins loose words ("Dan Cohen dan@x.com"), never accept that.
            if not content.valid_address(address) or address not in text:
                raise Refused(f"{field}: “{content.one_line(item, 80)}” is not a valid email address.")
            if address.lower() in seen:
                continue
            seen.add(address.lower())
            out.append((content.display_name(name), address))
    return out


def subject_line(value: Any) -> str:
    """The subject as one line.

    Raises:
        Refused: it has a line break, or is too long.
    """
    text = "" if value is None else str(value)
    if _SUBJECT_BREAK.search(text):
        raise Refused("The subject cannot contain a line break.")
    text = " ".join(_BREAK.sub(" ", text).split())
    if len(text) > SUBJECT_MAX_CHARS:
        raise Refused(f"The subject is too long ({len(text):,} characters; at most {SUBJECT_MAX_CHARS}).")
    return text


def body_text(value: Any, *, required: bool) -> str:
    """The body with plain line ends and no control characters.

    Raises:
        Refused: empty when ``required``, or too long.
    """
    text = "" if value is None else str(value)
    text = _BODY_DROPPED.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    if required and not text.strip():
        raise Refused("The message has no text.")
    if len(text) > OUTGOING_BODY_MAX_CHARS:
        raise Refused(f"The message is too long ({len(text):,} characters; at most {OUTGOING_BODY_MAX_CHARS:,}).")
    return text


def _limited(message: Outgoing) -> Outgoing:
    if len(message.everyone) > MAX_RECIPIENTS:
        raise Refused(f"Too many recipients ({len(message.everyone)}; at most {MAX_RECIPIENTS} in one message).")
    return message


def _unique(groups: Sequence[Sequence[Recipient]], taken: Sequence[str] = ()) -> List[List[Recipient]]:
    """Each group without the addresses of ``taken`` or of a group before it."""
    seen = {a.lower() for a in taken if a}
    out: List[List[Recipient]] = []
    for group in groups:
        kept = []
        for name, address in group:
            if address.lower() not in seen:
                seen.add(address.lower())
                kept.append((name, address))
        out.append(kept)
    return out


def new_message(to: Any, subject: Any, body: Any, cc: Any = None, bcc: Any = None, *,
                draft: bool = False) -> Outgoing:
    """A new message. A draft may still lack a recipient or its text.

    Raises:
        Refused: a bad recipient or subject, no recipient or no text (unless
            ``draft``), too many recipients.
    """
    first, copied, hidden = _unique([recipients(to, "to"), recipients(cc, "cc"), recipients(bcc, "bcc")])
    if not first and not draft:
        raise Refused("The message has no recipient.")
    return _limited(Outgoing(to=tuple(first), cc=tuple(copied), bcc=tuple(hidden), subject=subject_line(subject),
                             body=body_text(body, required=not draft)))


def reply_subject(original_subject: str) -> str:
    """``Re: <subject>``, the subject as it is when it already is a reply's."""
    subject = content.one_line(content.decoded(original_subject))
    if content.REPLY.match(subject):
        return subject
    return f"Re: {subject}" if subject else "Re:"


def reply(original: Mapping[str, str], own_address: str, body: Any, reply_all: bool = False, *,
          to: Any = None, subject: Any = None, draft: bool = False) -> Outgoing:
    """A reply to the message whose headers are ``original``
    (:func:`addons.gmail.seen.reply_headers`), in its thread.

    It goes to the message's ``Reply-To``, else its sender; a reply to the
    user's own message goes to that message's recipients. ``reply_all`` adds
    the other recipients as Cc, never the user's own address. ``to`` /
    ``subject`` given (a draft) replace the defaults.

    Raises:
        Refused: no usable address to reply to, a bad ``to`` / ``subject``,
            no text (unless ``draft``), too many recipients.
    """
    me = (own_address or "").strip().lower()
    sender = content.addresses(original.get("from", ""))
    first_to = content.addresses(original.get("to", ""))
    mine = bool(me) and any(address.lower() == me for _, address in sender)
    chosen = recipients(to, "to")
    target = chosen or (first_to if mine else (content.addresses(original.get("reply-to", "")) or sender))
    others: List[Recipient] = []
    if reply_all:
        others = ([] if mine else first_to) + content.addresses(original.get("cc", ""))
    target, others = _unique([target, others], taken=() if chosen else (me,))
    if not target and others:
        target, others = others, []
    if not target and not draft:
        raise Refused("That message has no usable address to reply to.")
    answered = _MESSAGE_ID.findall(original.get("message-id", ""))[:1]
    chain: List[str] = []
    for ref in _MESSAGE_ID.findall(original.get("references", "")) + answered:
        if ref not in chain:
            chain.append(ref)
    given = subject_line(subject)
    return _limited(Outgoing(to=tuple(target), cc=tuple(others), subject=given or reply_subject(original.get("subject", "")),
                             body=body_text(body, required=not draft),
                             in_reply_to=answered[0] if answered else "",
                             references=tuple(chain[-REFERENCES_KEPT:]),
                             thread_id=str(original.get("thread_id") or "")))


def mime(message: Outgoing) -> EmailMessage:
    """``message`` as an email (UTF-8; non-ASCII names and subject encoded
    per RFC 2047). ``From`` is left to Gmail: the account itself."""
    out = EmailMessage(policy=_POLICY)
    for name, group in (("To", message.to), ("Cc", message.cc), ("Bcc", message.bcc)):
        if group:
            out[name] = [Address(display_name=display, addr_spec=address) for display, address in group]
    out["Subject"] = message.subject
    if message.in_reply_to:
        out["In-Reply-To"] = message.in_reply_to
    if message.references:
        out["References"] = " ".join(message.references)
    out.set_content(message.body, charset="utf-8")
    return out


def raw(message: Outgoing) -> str:
    """What Gmail's ``raw`` field takes: the email's bytes, base64url."""
    return base64.urlsafe_b64encode(mime(message).as_bytes()).decode("ascii")


def body_for(message: Outgoing) -> dict:
    """The request body of ``messages.send`` / the ``message`` of ``drafts.create``."""
    out = {"raw": raw(message)}
    if message.thread_id:
        out["threadId"] = message.thread_id
    return out


def shown(value: Any, field: str) -> Optional[str]:
    """Recipients as an Approve card shows them: the real address of each,
    with its name. None when ``value`` would be refused."""
    try:
        return content.show_all(recipients(value, field), limit=MAX_RECIPIENTS)
    except Refused:
        return None
