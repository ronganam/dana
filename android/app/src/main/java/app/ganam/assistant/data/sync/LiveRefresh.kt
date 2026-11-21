package app.ganam.assistant.data.sync

import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow

/**
 * "Something changed on the server": a push event arrived (alert, new
 * suggestions, a reminder fired or was cleared, briefing, weekly review) or a
 * notification batch went up. Screens that show the server's state (Now, the
 * Notifications screen) refresh when it fires. Process-wide; it carries
 * nothing, so a burst of pings is just a burst of refreshes the listeners
 * debounce.
 */
object LiveRefresh {
    private val _pings = MutableSharedFlow<String>(extraBufferCapacity = 8)
    /** The reason (push event type, "sync"), for logs. */
    val pings: SharedFlow<String> = _pings.asSharedFlow()

    fun ping(reason: String) {
        _pings.tryEmit(reason)
    }
}
