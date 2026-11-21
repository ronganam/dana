"""Money configuration: the addon's name, the sync slots and the limits
(docs/money.md, docs/configuration.md).

The env values are read when used, so a test can point them elsewhere."""

import datetime
import os
from typing import List

ADDON = "money"

DEFAULT_SYNC_TIMES = "07:00,19:00"

# A sign-in code request lives this long.
CODE_MINUTES = 4
# "Sync now" (the page, the agent) at most this often per source.
MANUAL_MINUTES = 10
# Phone payment signals may pull a sync earlier at most this often per local day.
SIGNAL_SYNCS_PER_DAY = 4
# No signal sync in these local hours: [start, end).
QUIET_HOURS = (0, 6)
# Each source's slots move by a stable amount within ± this.
JITTER_MINUTES = 20
# After a failure that is not a login problem: the 1st retry, the 2nd; later the next slot.
RETRY_MINUTES = (30, 120)
# The first sync of a source reads this far back; later ones from the last good sync minus the overlap.
FIRST_SYNC_DAYS = 365
OVERLAP_DAYS = 10
# One sync gives up after this long (a provider's own wait included).
SYNC_TIMEOUT_MINUTES = 15
# The self-check fails for a source with no good sync for this long.
STALE_HOURS = 24


def sync_times() -> List[datetime.time]:
    """The local times of the regular syncs (``MONEY_SYNC_TIMES``, ``HH:MM`` comma-separated);
    a malformed value falls back to the default."""
    out: List[datetime.time] = []
    for part in (os.getenv("MONEY_SYNC_TIMES") or DEFAULT_SYNC_TIMES).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hour, minute = part.split(":")
            out.append(datetime.time(int(hour), int(minute)))
        except ValueError:
            return sync_times_default()
    return sorted(out) or sync_times_default()


def sync_times_default() -> List[datetime.time]:
    return [datetime.time(int(p[:2]), int(p[3:])) for p in DEFAULT_SYNC_TIMES.split(",")]
