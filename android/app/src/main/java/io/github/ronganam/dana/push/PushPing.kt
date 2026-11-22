package io.github.ronganam.dana.push

import android.content.Context
import kotlinx.coroutines.withTimeoutOrNull

/**
 * A wake-up ping from the server, on either transport (FCM's data message or
 * a UnifiedPush message): it carries only the event's number and type, so
 * whichever service got it notes when it came (Settings → Push) and runs the
 * same catch-up, [PhoneEvents.catchUp], which fetches the events themselves.
 */
class PushPing(
    private val now: () -> Long,
    private val noteLastPing: (Long) -> Unit,
    private val catchUp: suspend () -> Unit,
    private val timeoutMs: Long = TIMEOUT_MS,
) {
    /** Note the ping and catch up; false when the catch-up did not finish in time. */
    suspend fun run(): Boolean {
        noteLastPing(now())
        return withTimeoutOrNull(timeoutMs) { catchUp() } != null
    }

    companion object {
        /** A high-priority push gives the app about 10 s. */
        const val TIMEOUT_MS = 9_000L

        /** The ping handler of this app: the time goes to [PhoneEvents], the catch-up is [PhoneEvents.catchUp]. */
        fun of(context: Context): PushPing {
            val app = context.applicationContext
            return PushPing(System::currentTimeMillis, { PhoneEvents.noteLastPing(app, it) }, { PhoneEvents.catchUp(app) })
        }
    }
}
