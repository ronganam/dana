package io.github.ronganam.dana.data.signals

/**
 * One sleep session as Health Connect keeps it: [start] to [end], and when a
 * health app wrote (or last changed) it ([writtenAt]), all epoch ms.
 */
data class SleepSession(val start: Long, val end: Long, val writtenAt: Long)

/** The sleep events one read found, and the cursor the next read starts from. */
data class SleepRead(val events: List<SignalEvent.Sleep>, val cursor: Long)

/**
 * Health Connect's sleep sessions → "sleep" events. The cursor is the latest
 * write time read so far: a session counts when it was written after the
 * cursor, so a night a watch syncs hours late still arrives once, and a
 * session read again (unchanged) is not sent again. A session the server
 * would refuse (an end before its start, a span over a day, an end in the
 * future, older than [SignalQueue.MAX_AGE_MS]) is skipped; two apps writing
 * the same night give one event.
 */
object SleepSessions {
    /** The server refuses a span over 24 h. */
    const val MAX_SPAN_MS = 24L * 3600 * 1000

    fun read(sessions: List<SleepSession>, cursor: Long, now: Long): SleepRead {
        val fresh = sessions.filter { it.writtenAt > cursor }
        val events = fresh
            .filter { it.end > it.start && it.end - it.start <= MAX_SPAN_MS && it.end <= now && it.end >= now - SignalQueue.MAX_AGE_MS }
            .map { SignalEvent.Sleep(it.start, it.end) }
            .distinct()
            .sortedBy { it.end }
        return SleepRead(events, maxOf(cursor, fresh.maxOfOrNull { it.writtenAt } ?: cursor))
    }
}
