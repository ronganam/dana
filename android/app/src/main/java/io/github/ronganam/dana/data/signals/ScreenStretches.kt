package io.github.ronganam.dana.data.signals

/** The screen turned on ([on]) or off at [at] (epoch ms): UsageStatsManager's interactive / non-interactive events. */
data class ScreenChange(val on: Boolean, val at: Long)

/** The stretches one read found, and where an unfinished one now starts (null: the screen is off). */
data class ScreenRead(val stretches: List<SignalEvent.Screen>, val openSince: Long?)

/**
 * Screen on/off changes → the "screen" stretches the server sums up. A
 * stretch still going on at [until] is sent up to [until] and continues from
 * there next time ([ScreenRead.openSince]), so the server sees it while it
 * lasts (the wake flow checks ten minutes after an unlock) and the pieces
 * never overlap: a piece read twice is the same event, which the server
 * takes once.
 */
object ScreenStretches {
    /**
     * Read [changes] (any order) on top of [openSince], the start of the
     * stretch the last read left open. A repeated "on" keeps the first start; an
     * "off" with nothing open (the screen was turned on before the history
     * read) is skipped.
     */
    fun read(changes: List<ScreenChange>, openSince: Long?, until: Long): ScreenRead {
        val out = mutableListOf<SignalEvent.Screen>()
        var open = openSince
        for (c in changes.sortedBy { it.at }) {
            if (c.at > until) break
            if (c.on) {
                if (open == null) open = c.at
            } else {
                val start = open
                if (start != null && c.at > start) out += SignalEvent.Screen(start, c.at)
                open = null
            }
        }
        val start = open
        return if (start != null && until > start) {
            out += SignalEvent.Screen(start, until)
            ScreenRead(out, until)
        } else ScreenRead(out, start)
    }
}
