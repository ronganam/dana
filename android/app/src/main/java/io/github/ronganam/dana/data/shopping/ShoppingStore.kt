package io.github.ronganam.dana.data.shopping

import android.util.Log
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull
import io.github.ronganam.dana.data.network.HttpResult
import kotlinx.serialization.json.JsonElement
import java.io.File
import java.io.IOException
import java.time.Instant
import java.util.concurrent.ConcurrentHashMap

/**
 * The shopping list as this phone knows it, for the Shopping screen, the
 * widget, the watch and pushes alike (one per process: [ShoppingSync]).
 *
 * - **The server's last answer** for the user's active list, kept on disk
 *   ([CACHE]) so the screen and the widget show it at once, also offline.
 * - **Changes on their way** ([State.inflight]): applied on screen the moment
 *   they are made, sent one at a time in the order they were made.
 * - **Changes waiting** ([State.queued], on disk in [QUEUE]): a change the
 *   server didn't take because it couldn't be reached (or answered 5xx) waits
 *   here and is sent again, in order, by [flush] ([io.github.ronganam.dana.data.sync.PendingSyncWorker],
 *   the server coming back, the app coming to the front). Once anything
 *   waits, new changes queue behind it so the order never changes. Adds carry
 *   client ids, so a change that did reach the server before the connection
 *   dropped adds nothing twice. A waiting change the server refuses is
 *   dropped: silently when its item is gone (404), with a [notices] line
 *   otherwise.
 *
 * What is shown is [shown]: the last answer with the waiting and in-flight
 * changes on top ([ShoppingOps.applyOps]).
 */
class ShoppingStore(
    private val dir: File,
    private val transport: Transport,
    private val me: () -> Who? = { null },
    /** Ask the background worker to try the queue again later. */
    private val scheduleRetry: () -> Unit = {},
    /** Something the widget / watch show changed. */
    private val onChanged: () -> Unit = {},
    private val immediateTimeoutMs: Long = 6_000,
    private val clock: () -> String = { Instant.now().toString() }
) {
    /** The server, seen as requests. An unreachable server throws [IOException]. */
    fun interface Transport {
        suspend fun call(method: String, path: String, body: JsonElement?): HttpResult
    }

    data class State(
        val server: ShoppingScreen? = null,
        val inflight: List<ShoppingOp> = emptyList(),
        val queued: List<ShoppingOp> = emptyList(),
        val loaded: Boolean = false
    ) {
        val pending: List<ShoppingOp> get() = queued + inflight
    }

    sealed class Outcome {
        /** The server took it; [answer] is its screen (null: an answer that couldn't be read). */
        data class Sent(val answer: WriteAnswer?) : Outcome()
        /** Saved; it will be sent when the server answers again. */
        object Queued : Outcome()
        /** The server refused it ([status] and its error body). */
        data class Rejected(val status: Int, val body: ShoppingRefusal?) : Outcome()
    }

    /** What Undo of a change did. */
    sealed class Cancel {
        /** It was still waiting: taken out, it never reaches the server. */
        object Dropped : Cancel()
        /** It reached the server meanwhile: undo it there with [undoId]. */
        data class Sent(val undoId: String) : Cancel()
        /** Neither (sent without an undo, or long gone). */
        object Gone : Cancel()
    }

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    private val _notices = MutableSharedFlow<String>(extraBufferCapacity = 8)
    /** A waiting change the server refused when it was finally sent (its item still there). */
    val notices: SharedFlow<String> = _notices.asSharedFlow()

    private val sendLock = Mutex()
    private val fileLock = Any()
    /** Undo ids of waiting changes that were sent by [flush] (for an Undo pressed afterwards). */
    private val sentUndo = ConcurrentHashMap<String, String>()

    private val cacheFile get() = File(dir, CACHE)
    private val queueFile get() = File(dir, QUEUE)

    /** Read the disk cache and the queue (once; later calls do nothing). */
    fun load() {
        if (_state.value.loaded) return
        synchronized(fileLock) {
            if (_state.value.loaded) return
            val cached = runCatching { if (cacheFile.exists()) ShoppingJson.screen(cacheFile.readText()) else null }
                .onFailure { Log.w(TAG, "Couldn't read the shopping cache", it) }.getOrNull()
            val queued = runCatching { if (queueFile.exists()) ShoppingOps.decodeAll(queueFile.readText()) else emptyList() }
                .onFailure { Log.w(TAG, "Couldn't read the shopping queue", it) }.getOrDefault(emptyList())
            _state.update { it.copy(server = it.server ?: cached, queued = queued, loaded = true) }
        }
    }

    /** The list as the screen shows it: the last answer with every change still on its way on top. */
    fun shown(): ShoppingScreen? {
        val s = _state.value
        return ShoppingOps.applyOps(s.server, s.pending, me(), clock())
    }

    /** Whether a write is being sent right now (a stream event is then handled after it lands). */
    val busy: Boolean get() = sendLock.isLocked || _state.value.inflight.isNotEmpty()

    // ---------- reading ----------

    /**
     * `GET /api/shopping` for the active list; keeps it (and the disk cache)
     * when it is not older than what is held. Returns the HTTP status (-1: unreachable)
     * and the error body for a refusal.
     */
    suspend fun refresh(): Pair<Int, ShoppingRefusal?> {
        load()
        val (code, text) = try {
            transport.call("GET", "/api/shopping", null).let { it.code to it.body }
        } catch (e: CancellationException) {
            throw e
        } catch (e: IOException) {
            return -1 to null
        }
        if (code !in 200..299) return code to ShoppingJson.refusal(text)
        val screen = ShoppingJson.screen(text) ?: return code to ShoppingJson.refusal(text)
        val held = _state.value.server
        // A GET is the truth unless a write's answer newer than it already landed.
        if (ShoppingOps.accepts(held, screen) || !busy) keep(screen, text)
        return code to null
    }

    private fun keep(screen: ShoppingScreen, raw: String?) {
        _state.update { it.copy(server = screen) }
        if (screen.isActive && raw != null) writeAtomically(cacheFile, raw)
        onChanged()
    }

    /** Keep a screen read elsewhere (a write's answer the caller got itself, e.g. switching lists). */
    fun accept(screen: ShoppingScreen, raw: String) {
        if (screen.isActive && ShoppingOps.accepts(_state.value.server, screen)) keep(screen, raw)
    }

    // ---------- writing ----------

    /**
     * Make [op]: shown at once, then sent (one at a time, in order). Waits at
     * most [immediateTimeoutMs] for the server before queueing it.
     */
    suspend fun submit(op: ShoppingOp): Outcome {
        load()
        _state.update { it.copy(inflight = it.inflight + op) }
        onChanged()
        return sendLock.withLock {
            if (_state.value.queued.isNotEmpty()) {
                enqueue(op)
                return@withLock Outcome.Queued
            }
            val (code, text) = withTimeoutOrNull(immediateTimeoutMs) { attempt(op) } ?: (-1 to "")
            when {
                code in 200..299 -> {
                    val answer = ShoppingJson.answer(text)
                    landed(op, answer, text)
                    Outcome.Sent(answer)
                }
                ShoppingOps.isRetryable(code) -> {
                    enqueue(op)
                    Outcome.Queued
                }
                else -> {
                    _state.update { it.copy(inflight = it.inflight - op) }
                    onChanged()
                    Outcome.Rejected(code, ShoppingJson.refusal(text))
                }
            }
        }
    }

    /** [op] reached the server: it leaves the list of changes and its answer becomes the server state. */
    private fun landed(op: ShoppingOp, answer: WriteAnswer?, raw: String) {
        val screen = answer?.screen
        val keepIt = screen != null && screen.isActive && ShoppingOps.accepts(_state.value.server, screen)
        _state.update { s ->
            s.copy(
                inflight = s.inflight - op,
                queued = s.queued.filter { it.id != op.id },
                server = if (keepIt) screen else s.server
            )
        }
        if (keepIt) writeAtomically(cacheFile, raw)
        onChanged()
    }

    private suspend fun attempt(op: ShoppingOp): Pair<Int, String> = try {
        val r = ShoppingOps.request(op)
        transport.call(r.method, r.path, r.body).let { it.code to it.body }
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        Log.w(TAG, "Shopping change not delivered: ${e.message}")
        -1 to ""
    }

    private fun enqueue(op: ShoppingOp) {
        synchronized(fileLock) {
            val queued = _state.value.queued + op
            writeQueue(queued)
            _state.update { it.copy(inflight = it.inflight - op, queued = queued) }
        }
        scheduleRetry()
        onChanged()
    }

    /**
     * Send every waiting change, oldest first, until one can't be delivered.
     * Returns how many still wait.
     */
    suspend fun flush(): Int {
        load()
        if (_state.value.queued.isEmpty()) return 0
        return sendLock.withLock {
            while (true) {
                val op = _state.value.queued.firstOrNull() ?: break
                val (code, text) = attempt(op)
                if (ShoppingOps.isRetryable(code)) break
                if (code in 200..299) {
                    val answer = ShoppingJson.answer(text)
                    answer?.undo?.let { sentUndo[op.id] = it.id }
                    synchronized(fileLock) { writeQueue(_state.value.queued.filter { it.id != op.id }) }
                    landed(op, answer, text)
                } else {
                    if (code != 404) {
                        val body = ShoppingJson.refusal(text)
                        _notices.tryEmit("Couldn't sync “${label(op)}”: ${ShoppingView.errorText(code, body)}")
                    }
                    Log.i(TAG, "Dropping a waiting shopping change (HTTP $code)")
                    drop(op.id)
                }
            }
            _state.value.queued.size
        }
    }

    /** Undo of a change that waited to sync (it has no server undo id of its own). */
    suspend fun cancel(opId: String): Cancel = sendLock.withLock {
        if (_state.value.queued.any { it.id == opId }) {
            drop(opId)
            return@withLock Cancel.Dropped
        }
        sentUndo.remove(opId)?.let { Cancel.Sent(it) } ?: Cancel.Gone
    }

    private fun drop(opId: String) {
        synchronized(fileLock) {
            val queued = _state.value.queued.filter { it.id != opId }
            writeQueue(queued)
            _state.update { it.copy(queued = queued) }
        }
        onChanged()
    }

    /**
     * Another user now (the token or the server changed): drop the kept list
     * and every change waiting to sync; they were the previous user's.
     */
    suspend fun forget() = sendLock.withLock {
        synchronized(fileLock) {
            cacheFile.delete()
            queueFile.delete()
            sentUndo.clear()
            _state.value = State(loaded = true)
        }
        onChanged()
    }

    private fun label(op: ShoppingOp): String = ShoppingView.queuedLabel(op).removeSuffix(" · will sync")

    private fun writeQueue(ops: List<ShoppingOp>) {
        if (ops.isEmpty()) queueFile.delete() else writeAtomically(queueFile, ShoppingOps.encodeAll(ops))
    }

    private fun writeAtomically(f: File, text: String) {
        runCatching {
            dir.mkdirs()
            val tmp = File(dir, f.name + ".tmp")
            tmp.writeText(text)
            if (!tmp.renameTo(f)) { f.writeText(text); tmp.delete() }
        }.onFailure { Log.w(TAG, "Couldn't write ${f.name}", it) }
    }

    companion object {
        private const val TAG = "ShoppingStore"
        const val CACHE = "shopping_screen.json"
        const val QUEUE = "pending_shopping.json"
    }
}
