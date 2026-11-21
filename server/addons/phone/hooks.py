"""The phone addon's background work and its push channel (../docs/addons.md, "The manifest").

- at start: finish call processing and notification triage a restart interrupted;
- every 6 hours: drop call audio older than ``CALL_AUDIO_RETENTION_DAYS``
  (transcripts are kept) and notifications older than 30 days;
- every minute: the notification digest at ``NOTIFICATION_DIGEST_TIMES``;
- every 30 seconds: re-nudge unseen alerts;
- the ``push`` channel of ``core.notify.send``: a message's ``kind`` and
  ``data`` as a push event to the user's phone (addons/phone/push.py).
"""

import asyncio
import datetime

from core.addons import hours, minutes
from core.notify import Message
from core.users import User
from addons.phone import push
from addons.phone.addon import addon
from addons.phone.calls import processing as calls_processing, retention as calls_retention
from addons.phone.notifications import digest, escalation, processing as notification_processing
from addons.phone.notifications import retention as notification_retention


@addon.job(at_start=True)
async def resume_unfinished(user: User) -> None:
    """Finish ``user``'s call processing and notification triage interrupted by a restart."""
    calls_processing.resume_unfinished(user)
    notification_processing.resume_unfinished(user)


@addon.job(every=hours(6))
async def purge_old_audio(user: User) -> int:
    return await asyncio.to_thread(calls_retention.purge_old_audio, user)


@addon.job(every=hours(6))
async def purge_old_notifications(user: User) -> int:
    return await asyncio.to_thread(notification_retention.purge_old, user)


@addon.job(every=minutes(1))
async def send_digest(user: User) -> int:
    return await digest.digest_due(user)


@addon.job(every=datetime.timedelta(seconds=30))
async def renudge(user: User) -> int:
    return await escalation.renudge_due(user)


@addon.channel("push")
async def push_channel(user: User, message: Message) -> bool:
    """A message with a ``kind`` goes to ``user``'s phone as that push event."""
    if not message.kind:
        return False
    return await push.send(user, message.kind, dict(message.data))
