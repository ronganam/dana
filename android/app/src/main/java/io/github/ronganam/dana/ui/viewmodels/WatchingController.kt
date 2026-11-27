package io.github.ronganam.dana.ui.viewmodels

import android.util.Log
import io.github.ronganam.dana.data.network.ApiException
import io.github.ronganam.dana.data.watches.DryRun
import io.github.ronganam.dana.data.watches.PipelineEvent
import io.github.ronganam.dana.data.watches.RunStep
import io.github.ronganam.dana.data.watches.TraceRow
import io.github.ronganam.dana.data.watches.Watch
import io.github.ronganam.dana.data.watches.WatchDetail
import io.github.ronganam.dana.data.watches.WatchInvalid
import io.github.ronganam.dana.data.watches.WatchRun
import io.github.ronganam.dana.data.watches.draftOf
import io.github.ronganam.dana.data.watches.runSteps
import io.github.ronganam.dana.data.watches.todays
import io.github.ronganam.dana.data.watches.watchRuns
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.ZoneId

/** Where making a new watch is. */
sealed interface NewWatchPhase {
    /** Typing the words. */
    data object Words : NewWatchPhase
    data object Compiling : NewWatchPhase
    /** The server could not make a watch of the words: its reasons. */
    data class Refused(val errors: List<String>) : NewWatchPhase
    /** Made, off: its circuit, then the dry run, then Turn on. */
    data class Made(val watch: Watch) : NewWatchPhase
}

/** The New watch page. */
data class NewWatchState(
    val text: String = "",
    val phase: NewWatchPhase = NewWatchPhase.Words,
    val dryRun: DryRun? = null,
    val dryRunning: Boolean = false,
    val dryFailed: Boolean = false,
)

/**
 * The Watching place (server docs/watches.md): the user's watches, today's
 * checks of each (`/trace`), their agent runs from live activities (`/api/live`,
 * Approve / Deny through `POST /api/live/{id}/act`, the same path the Live
 * Update's buttons take), the engine's live steps (`/pipeline/stream`), one
 * watch open, and a new watch being made. Visible ([start]) it follows the
 * stream and polls the live activities; [stop] when it leaves.
 */
class WatchingController(private val ctx: ControllerContext) {
    private val _watches = MutableStateFlow<List<Watch>>(emptyList())
    val watches: StateFlow<List<Watch>> = _watches.asStateFlow()

    private val _loaded = MutableStateFlow(false)
    /** An answer came (false: still loading the first time). */
    val loaded: StateFlow<Boolean> = _loaded.asStateFlow()

    private val _failed = MutableStateFlow(false)
    val failed: StateFlow<Boolean> = _failed.asStateFlow()

    private val _runs = MutableStateFlow<Map<String, WatchRun>>(emptyMap())
    /** Each watch's agent run that is going on, by watch id. */
    val runs: StateFlow<Map<String, WatchRun>> = _runs.asStateFlow()

    private val _steps = MutableStateFlow<Map<String, List<RunStep>>>(emptyMap())
    /** A run's steps so far, by watch id (from its session). */
    val steps: StateFlow<Map<String, List<RunStep>>> = _steps.asStateFlow()

    private val _drafts = MutableStateFlow<Map<String, String>>(emptyMap())
    /** What a waiting run would send, by watch id (its approval's text argument). */
    val drafts: StateFlow<Map<String, String>> = _drafts.asStateFlow()

    private val _today = MutableStateFlow<Map<String, List<TraceRow>>>(emptyMap())
    /** Today's checks, by watch id. */
    val today: StateFlow<Map<String, List<TraceRow>>> = _today.asStateFlow()

    private val _pulses = MutableStateFlow<Map<String, Long>>(emptyMap())
    /** When the engine last did something for a watch (epoch ms), from the live stream. */
    val pulses: StateFlow<Map<String, Long>> = _pulses.asStateFlow()

    private val _streaming = MutableStateFlow(false)
    /** The live stream is connected. */
    val streaming: StateFlow<Boolean> = _streaming.asStateFlow()

    private val _selected = MutableStateFlow<String?>(null)
    /** The open watch's id. */
    val selected: StateFlow<String?> = _selected.asStateFlow()

    private val _detail = MutableStateFlow<WatchDetail?>(null)
    val detail: StateFlow<WatchDetail?> = _detail.asStateFlow()

    private val _trace = MutableStateFlow<List<TraceRow>>(emptyList())
    /** The open watch's recent checks (30 days kept, newest first). */
    val trace: StateFlow<List<TraceRow>> = _trace.asStateFlow()

    private val _changing = MutableStateFlow(false)
    /** A change in words is being compiled. */
    val changing: StateFlow<Boolean> = _changing.asStateFlow()

    private val _newWatch = MutableStateFlow<NewWatchState?>(null)
    /** The New watch page while it is open. */
    val newWatch: StateFlow<NewWatchState?> = _newWatch.asStateFlow()

    private val _acting = MutableStateFlow<Set<String>>(emptySet())
    /** Live activities with an Approve / Deny on its way. */
    val acting: StateFlow<Set<String>> = _acting.asStateFlow()

    private var visible: Job? = null
    private var refreshJob: Job? = null

    private val zone: ZoneId get() = ZoneId.systemDefault()

    /** The place is showing: load, then follow the stream and the live activities until [stop]. */
    fun start() {
        if (visible?.isActive == true) return
        visible = ctx.scope.launch {
            launch { refresh() }
            launch { followStream() }
            launch {
                while (isActive) {
                    delay(LIVE_POLL_MS)
                    refreshLive()
                }
            }
        }
    }

    fun stop() {
        visible?.cancel()
        visible = null
        _streaming.value = false
    }

    /** Everything again: the watches, today's checks, the runs (and the open watch). */
    fun refresh(): Job = ctx.scope.launch {
        val list = ctx.load(TAG) { ctx.api.watches.list() }
        _failed.value = list == null
        if (list != null) {
            _watches.value = list
            _loaded.value = true
            loadToday(list.filter { !it.isClosed })
        }
        refreshLive()
        _selected.value?.let { loadDetail(it) }
    }

    private suspend fun loadToday(list: List<Watch>) = coroutineScope {
        val day = LocalDate.now(zone)
        val pairs = list.map { w ->
            async { w.id to ctx.load(TAG) { todays(ctx.api.watches.trace(w.id, 500).trace, day, zone) } }
        }.awaitAll()
        _today.update { old -> pairs.associate { (id, rows) -> id to (rows ?: old[id].orEmpty()) } }
    }

    private suspend fun refreshLive() {
        val acts = ctx.load(TAG) { ctx.api.live.list() } ?: return
        val runs = watchRuns(acts)
        _runs.value = runs
        val states = runs.mapNotNull { (id, run) -> ctx.load(TAG) { ctx.api.chat.session(run.sessionId) }?.let { Triple(id, run, it) } }
        _steps.value = states.associate { (id, run, st) -> id to runSteps(st.messages, run) }
        _drafts.value = states.mapNotNull { (id, run, st) -> if (run.waiting) draftOf(st.pendingApproval?.toolArgs)?.let { id to it } else null }.toMap()
    }

    /** The live stream, reconnecting after a drop (2 s, growing to 30 s) while the place shows. */
    private suspend fun followStream() {
        var wait = 2_000L
        while (kotlinx.coroutines.currentCoroutineContext().isActive) {
            try {
                ctx.api.watches.stream { ev ->
                    _streaming.value = true
                    wait = 2_000L
                    onStep(ev)
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiException) {
                if (e.code == 403) return  // not connected: nothing to follow
                Log.w(TAG, "Stream: $e")
            } catch (e: Exception) {
                Log.w(TAG, "Stream: $e")
            }
            _streaming.value = false
            delay(wait)
            wait = (wait * 2).coerceAtMost(30_000L)
        }
    }

    /** One engine step: pulse its watch; a fire or an action changes counts and runs, so fetch again soon. */
    internal fun onStep(ev: PipelineEvent) {
        val id = ev.watchId
        if (id != null) _pulses.update { it + (id to System.currentTimeMillis()) }
        if (ev.stage in REFETCH_STAGES || (id != null && ev.stage == "semantic")) {
            refreshJob?.cancel()
            refreshJob = ctx.scope.launch {
                delay(800)
                refresh()
            }
        }
    }

    // --- One watch ---------------------------------------------------------------------

    fun open(id: String?) {
        _selected.value = id
        _detail.value = null
        _trace.value = emptyList()
        if (id != null) {
            _newWatch.value = null
            ctx.scope.launch { loadDetail(id) }
        }
    }

    private suspend fun loadDetail(id: String) {
        val d = ctx.load(TAG) { ctx.api.watches.get(id) }
        val t = ctx.load(TAG) { ctx.api.watches.trace(id, 300).trace }
        if (_selected.value != id) return
        if (d != null) _detail.value = d
        if (t != null) _trace.value = t
    }

    /** Pause or turn on the open watch. */
    fun setPaused(id: String, paused: Boolean) = ctx.action(if (paused) "Couldn't pause" else "Couldn't turn it on") {
        val w = ctx.api.watches.setStatus(id, paused)
        replace(w)
        true
    }

    /** Change a watch in words; true when it changed. */
    fun change(id: String, words: String, done: (Boolean) -> Unit) {
        if (words.isBlank()) return
        _changing.value = true
        ctx.scope.launch {
            val ok = try {
                replace(ctx.api.watches.change(id, words.trim()))
                ctx.messages.post("Changed.")
                loadDetail(id)
                true
            } catch (e: CancellationException) {
                throw e
            } catch (e: WatchInvalid) {
                ctx.messages.post("Dana couldn't change it: ${e.errors.firstOrNull().orEmpty()}")
                false
            } catch (e: Exception) {
                ctx.messages.post("Couldn't change it: ${ctx.texts.reason(e)}")
                false
            } finally {
                _changing.value = false
            }
            done(ok)
        }
    }

    /** Delete a watch, with Undo. */
    fun delete(id: String) = ctx.action("Couldn't delete") {
        val title = _watches.value.firstOrNull { it.id == id }?.title.orEmpty()
        val undo = ctx.api.watches.delete(id)
        _watches.update { l -> l.filter { it.id != id } }
        if (_selected.value == id) open(null)
        ctx.messages.postUndoable("Deleted “$title”", onUndo = {
            if (undo != null) ctx.action("Couldn't undo") { ctx.api.watches.undo(undo); refresh(); true }
        })
        true
    }

    private fun replace(w: Watch) {
        _watches.update { l -> if (l.any { it.id == w.id }) l.map { if (it.id == w.id) w else it } else listOf(w) + l }
        _detail.update { d -> if (d?.watch?.id == w.id) d.copy(watch = w) else d }
        _newWatch.update { n -> if (n != null && (n.phase as? NewWatchPhase.Made)?.watch?.id == w.id) n.copy(phase = NewWatchPhase.Made(w)) else n }
    }

    // --- Runs ----------------------------------------------------------------------------

    /** Approve / Deny a waiting run (the live activity's own action). */
    fun answer(run: WatchRun, approve: Boolean) {
        if (run.activityId in _acting.value) return
        _acting.update { it + run.activityId }
        ctx.scope.launch {
            try {
                ctx.api.live.act(run.activityId, if (approve) "approve" else "deny")
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                ctx.messages.post("Couldn't ${if (approve) "approve" else "deny"}: ${ctx.texts.reason(e)}")
            } finally {
                _acting.update { it - run.activityId }
            }
            refreshLive()
        }
    }

    // --- A new watch ----------------------------------------------------------------------

    fun startNew() {
        _selected.value = null
        _newWatch.value = NewWatchState()
    }

    fun closeNew() {
        _newWatch.value = null
    }

    fun setNewText(text: String) = _newWatch.update { it?.copy(text = text, phase = if (it.phase is NewWatchPhase.Refused) NewWatchPhase.Words else it.phase) }

    /** Compile the words into a watch that is off, then try it on the last 14 days. */
    fun compileNew() {
        val s = _newWatch.value ?: return
        if (s.text.isBlank() || s.phase == NewWatchPhase.Compiling) return
        _newWatch.value = s.copy(phase = NewWatchPhase.Compiling, dryRun = null, dryFailed = false)
        ctx.scope.launch {
            val phase = try {
                NewWatchPhase.Made(ctx.api.watches.createOff(s.text.trim()))
            } catch (e: CancellationException) {
                throw e
            } catch (e: WatchInvalid) {
                NewWatchPhase.Refused(e.errors)
            } catch (e: Exception) {
                NewWatchPhase.Refused(listOf(ctx.texts.reason(e)))
            }
            _newWatch.update { it?.copy(phase = phase) }
            if (phase is NewWatchPhase.Made) {
                _watches.update { l -> listOf(phase.watch) + l.filter { it.id != phase.watch.id } }
                dryRun(phase.watch)
            }
        }
    }

    private suspend fun dryRun(w: Watch) {
        _newWatch.update { it?.copy(dryRunning = true, dryFailed = false) }
        val r = ctx.load(TAG) { ctx.api.watches.dryRun(w.spec, DRY_RUN_DAYS) }
        _newWatch.update { it?.copy(dryRun = r, dryRunning = false, dryFailed = r == null) }
    }

    /** Turn the new watch on and show it. */
    fun turnOnNew() {
        val w = (_newWatch.value?.phase as? NewWatchPhase.Made)?.watch ?: return
        ctx.action("Couldn't turn it on") {
            replace(ctx.api.watches.setStatus(w.id, paused = false))
            _newWatch.value = null
            ctx.messages.post("“${w.title}” is on.")
            true
        }
    }

    /** Leave the new watch off (it stays in the list, paused). */
    fun leaveNewOff() {
        val made = (_newWatch.value?.phase as? NewWatchPhase.Made)?.watch
        _newWatch.value = null
        if (made != null) ctx.messages.post("“${made.title}” stays off. Turn it on from its page.")
    }

    companion object {
        private const val TAG = "Watching"
        private const val LIVE_POLL_MS = 10_000L
        const val DRY_RUN_DAYS = 14
        private val REFETCH_STAGES = setOf("fire", "action", "deadline")
    }
}
