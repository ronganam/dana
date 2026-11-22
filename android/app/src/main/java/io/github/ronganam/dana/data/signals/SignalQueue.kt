package io.github.ronganam.dana.data.signals

import android.util.Log
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.io.File

/** Sends one batch of events to the server (`POST /api/signals`). */
fun interface SignalSender {
    suspend fun send(batch: List<SignalEvent>): SignalsPosted
}

/** What one [SignalQueue.flush] did: events [sent], still [remaining], and whether a retry later is worth it. */
data class FlushResult(val sent: Int, val remaining: Int, val retry: Boolean)

/**
 * The phone's signal events waiting for the server, in the order they
 * happened, kept in [file] (JSON) so a reboot or a long offline stretch loses
 * nothing. [flush] sends them oldest first in batches of at most
 * [BATCH_SIZE]; a batch that fails (unreachable, 5xx, 408, 429) stays whole and
 * is sent again later (the server takes a repeated event without effect). The
 * events the server rejects are dropped and logged; events older than
 * [MAX_AGE_MS] are dropped before sending (the server would call them stale).
 */
class SignalQueue(
    private val file: File,
    private val sender: SignalSender,
    private val now: () -> Long = System::currentTimeMillis
) {
    private val fileLock = Any()
    private val flushMutex = Mutex()

    /** Add [events] at the end (oldest first among themselves). */
    fun add(events: List<SignalEvent>) {
        if (events.isEmpty()) return
        synchronized(fileLock) { write(prune(read() + events)) }
    }

    /** The events waiting, oldest first. */
    fun pending(): List<SignalEvent> = synchronized(fileLock) { read() }

    /**
     * Send everything waiting, a batch at a time. Stops at the first batch the
     * server didn't take; events added meanwhile go out in the same run.
     */
    suspend fun flush(): FlushResult = flushMutex.withLock {
        var sent = 0
        var result: FlushResult? = null
        while (result == null) {
            val batch = synchronized(fileLock) {
                val all = prune(read())
                write(all)
                all.take(BATCH_SIZE)
            }
            if (batch.isEmpty()) {
                result = FlushResult(sent, 0, retry = false)
                continue
            }
            val answer = sender.send(batch)
            when {
                answer.ok -> {
                    answer.rejected.forEach { r ->
                        val event = batch.getOrNull(r.index)
                        Log.w(TAG, "Server rejected ${event?.javaClass?.simpleName ?: "event #${r.index}"}: ${r.error}")
                    }
                    sent += batch.size - answer.rejected.size
                    drop(batch)
                }
                isRetryable(answer.code) -> result = FlushResult(sent, pending().size, retry = true)
                answer.code in DROP_BATCH -> {
                    // The server can't read this batch at all: sending it again won't help.
                    Log.w(TAG, "Server refused a batch of ${batch.size} (HTTP ${answer.code}); dropping it")
                    drop(batch)
                }
                else -> {
                    // Not paired any more, an older server without /api/signals…: keep them
                    // (they age out) and try again on the next run, without backing off.
                    Log.w(TAG, "Signals not taken (HTTP ${answer.code}); keeping them")
                    result = FlushResult(sent, pending().size, retry = false)
                }
            }
        }
        result
    }

    /** Remove [batch] from the head of the queue (only [flush] removes, under its mutex). */
    private fun drop(batch: List<SignalEvent>) = synchronized(fileLock) {
        val all = read()
        // The batch is still the head: [add] only appends, and pruning removes from the head too.
        val head = all.take(batch.size)
        write(if (head == batch) all.drop(batch.size) else all.filterNot { it in batch.toSet() })
    }

    /** Drop events too old for the server and keep at most [MAX_QUEUED] (the newest). */
    private fun prune(events: List<SignalEvent>): List<SignalEvent> {
        val cutoff = now() - MAX_AGE_MS
        return events.filter { it.latest >= cutoff }.takeLast(MAX_QUEUED)
    }

    private fun read(): List<SignalEvent> = try {
        if (!file.exists()) emptyList() else SignalsJson.decodeEvents(file.readText())
    } catch (e: Exception) {
        Log.e(TAG, "Couldn't read the signal queue", e)
        emptyList()
    }

    private fun write(events: List<SignalEvent>) {
        if (events.isEmpty()) {
            file.delete()
            return
        }
        file.parentFile?.mkdirs()
        val tmp = File(file.parentFile, "${file.name}.tmp")
        tmp.writeText(SignalsJson.encodeEvents(events))
        if (!tmp.renameTo(file)) {
            file.writeText(tmp.readText())
            tmp.delete()
        }
    }

    companion object {
        private const val TAG = "SignalQueue"
        /** The server takes at most 200 events per call. */
        const val BATCH_SIZE = 200
        /** The server rejects events older than 48 h as stale; a little margin. */
        const val MAX_AGE_MS = 47L * 3600 * 1000
        /** A bound for a phone that stays offline (or unpaired) for long. */
        const val MAX_QUEUED = 5000
        private val DROP_BATCH = setOf(400, 413, 422)

        fun isRetryable(code: Int): Boolean = code == -1 || code == 408 || code == 429 || code >= 500
    }
}
