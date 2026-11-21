package io.github.ronganam.dana.ui.viewmodels

import android.util.Log
import io.github.ronganam.dana.data.network.ChatApi
import io.github.ronganam.dana.data.network.RunEvent
import io.github.ronganam.dana.data.network.ServerStatusMonitor
import io.github.ronganam.dana.data.network.SessionBusyException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import java.io.IOException

/** A request that streams a run's events into its callback. */
internal typealias RunRequest = suspend (onEvent: (RunEvent) -> Unit) -> Unit

/**
 * Bookkeeping for the run being rendered. Events carry `run_id` + `seq`,
 * so a reattached stream that replays frames we already have is ignored.
 */
internal class RunTracker {
    var runId: String? = null
    var lastSeq = 0
    var modelLabel: String? = null
    var gotState = false
    /** LLM step (iteration) -> id of the live reply bubble for that step. */
    val stepBubbles = HashMap<Int, String>()
}

/**
 * Follows a run over Server-Sent Events (server `server/core/runs.py`). Every
 * stream closes with a `state` frame; one that drops before it reattaches to
 * `GET /api/sessions/{id}/stream` after the last seen `seq` (backing off, for
 * up to [REATTACH_GIVE_UP_MS]); a 409 (the session is busy) follows the run
 * that is going instead, unless the [Listener] gives the message back.
 */
internal class RunStreamClient(private val chat: () -> ChatApi) {

    @Volatile var run = RunTracker()

    fun reset(keepModelLabel: Boolean = false) {
        val label = run.modelLabel
        run = RunTracker().also { if (keepModelLabel) it.modelLabel = label }
    }

    /** Track [event]'s run and seq; false when it was already rendered (replayed on reattach). */
    fun accept(event: RunEvent): Boolean {
        event.runId?.let { runId ->
            // Following a different run than before (attached after a newer one started): fresh bookkeeping.
            if (run.runId != null && run.runId != runId) reset(keepModelLabel = true)
            run.runId = runId
        }
        event.seq?.let { seq ->
            if (seq <= run.lastSeq) return false
            run.lastSeq = seq
        }
        return true
    }

    interface Listener {
        fun onEvent(event: RunEvent)
        /** The session is busy (409) with another run; true when the message was given back (stop here). */
        fun onBusy(): Boolean
        /** About to follow the run that refused ours. */
        fun onFollowingBusy()
        fun onReconnecting()
        fun onFailed(e: Exception)
        fun onCancelled()
        /** The session is still the one shown (else a drop isn't worth a reattach). */
        fun stillShowing(): Boolean
    }

    /** Run [first] and follow its run to the closing `state`. */
    suspend fun follow(sessionId: String, first: RunRequest, listener: Listener) {
        var request = first
        val startedAt = System.currentTimeMillis()
        var reconnects = 0
        var busyRedirects = 0
        while (true) {
            try {
                request(listener::onEvent)
                if (run.gotState) return
                throw IOException("Stream ended before the run finished")
            } catch (e: CancellationException) {
                Log.d(TAG, "Assistant stream cancelled")
                listener.onCancelled()
                throw e
            } catch (e: SessionBusyException) {
                if (listener.onBusy()) return
                if (busyRedirects++ > 0) {
                    listener.onFailed(e)
                    return
                }
                // An earlier run in this chat is still going: follow it.
                listener.onFollowingBusy()
                reset()
                val busyRun = e.runId
                request = { onEvent -> chat().attach(sessionId, busyRun, 0, onEvent) }
            } catch (e: Exception) {
                Log.w(TAG, "Assistant stream failed: $e")
                // A stream that dies mid-read never reaches the interceptor.
                if (e is IOException) ServerStatusMonitor.checkNow()
                val runId = run.runId
                val canReattach = e is IOException && runId != null && listener.stillShowing() &&
                    System.currentTimeMillis() - startedAt < REATTACH_GIVE_UP_MS
                if (!canReattach) {
                    listener.onFailed(e)
                    return
                }
                // The server keeps the run going without us; reconnect and
                // resume right after the last event we rendered.
                reconnects++
                listener.onReconnecting()
                delay(minOf(REATTACH_BASE_DELAY_MS * reconnects, REATTACH_MAX_DELAY_MS))
                val after = run.lastSeq
                request = { onEvent -> chat().attach(sessionId, runId, after, onEvent) }
            }
        }
    }

    companion object {
        private const val TAG = "RunStream"
        private const val REATTACH_BASE_DELAY_MS = 1_000L
        private const val REATTACH_MAX_DELAY_MS = 10_000L
        /** Stop trying to reattach to a dropped run after this long. */
        private const val REATTACH_GIVE_UP_MS = 10 * 60_000L
    }
}
