package io.github.ronganam.dana.ui.viewmodels

import io.github.ronganam.dana.data.chat.ThreadModel
import io.github.ronganam.dana.data.models.ChatMessage
import io.github.ronganam.dana.data.network.SessionState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * How the shared thread (`session_id: "main"`) is shown: pages newest first
 * (the cursor of the next older page), what Dana still has in mind, the
 * conversation gap, and the view floor of a "fresh" view (the power-button
 * sheet, the kiosk after a pause), which shows only what is said from then on
 * while the server keeps one thread.
 */
internal class ThreadView(
    private val transcript: ChatTranscript,
    /** Writes the newest thread messages to the device cache. */
    private val cache: (List<ChatMessage>) -> Unit
) {
    private val _gapMinutes = MutableStateFlow(DEFAULT_GAP_MINUTES)
    val gapMinutes: StateFlow<Double> = _gapMinutes.asStateFlow()

    private val _hasOlder = MutableStateFlow(false)
    val hasOlder: StateFlow<Boolean> = _hasOlder.asStateFlow()

    private val _loadingOlder = MutableStateFlow(false)
    val loadingOlder: StateFlow<Boolean> = _loadingOlder.asStateFlow()

    private val _memory = MutableStateFlow<ThreadModel.Memory>(ThreadModel.Memory.Unknown)
    val memory: StateFlow<ThreadModel.Memory> = _memory.asStateFlow()

    private var olderCursor: String? = null

    /** Thread messages older than this (epoch ms) stay hidden; 0 = all shown. */
    private var viewFloorMs = 0L

    /** Show everything again (back from another conversation). */
    fun showAll() {
        viewFloorMs = 0L
    }

    /** A fresh view: only what is said from now on (with a margin for clock skew). */
    fun startFresh() {
        _hasOlder.value = false
        viewFloorMs = System.currentTimeMillis() - VIEW_FLOOR_MARGIN_MS
    }

    fun setMemory(memory: ThreadModel.Memory) {
        _memory.value = memory
    }

    /**
     * The newest thread page (from a GET or a run's closing `state`) over
     * what is shown: stored messages replace live bubbles, older pages stay.
     */
    fun applyPage(state: SessionState, page: List<ChatMessage>) {
        _memory.value = state.memory
        state.gapMinutes?.let { _gapMinutes.value = it }
        val floor = viewFloorMs
        val visible = if (floor > 0) page.filter { (it.timestamp ?: Long.MAX_VALUE) >= floor } else page
        val current = transcript.value.filter { !it.isThinking }
        var merged = ThreadModel.mergeNewest(current, visible)
        if (state.shortcut) {
            val i = merged.indexOfLast { !it.isUser && it.toolCall == null && it.message.isNotBlank() }
            if (i >= 0 && !merged[i].shortcut) merged = merged.toMutableList().also { it[i] = it[i].copy(shortcut = true) }
        }
        // The cursor belongs to this page; keep ours when older pages are already shown.
        if (floor > 0) {
            _hasOlder.value = false
        } else if (visible.isNotEmpty() && merged.firstOrNull()?.id == visible.first().id) {
            _hasOlder.value = state.hasMore
            olderCursor = state.before
        }
        val thinking = transcript.value.filter { it.isThinking }
        transcript.value = merged + thinking
        if (merged.isNotEmpty()) cache(merged)
    }

    /** The cursor of the page before the oldest one shown, when one can be loaded now. */
    fun olderToLoad(): String? = if (_hasOlder.value && !_loadingOlder.value) olderCursor else null

    fun setLoadingOlder(loading: Boolean) {
        _loadingOlder.value = loading
    }

    /** An older page arrived: it goes above what is shown. */
    fun prependOlder(page: SessionState) {
        val older = page.messages
        transcript.value = ThreadModel.prependOlder(transcript.value, older)
        _hasOlder.value = page.hasMore && older.isNotEmpty()
        olderCursor = page.before
    }

    companion object {
        private const val DEFAULT_GAP_MINUTES = 20.0
        /** A fresh view still shows a message stamped this much before it (clock skew). */
        private const val VIEW_FLOOR_MARGIN_MS = 5_000L
        /** Thread messages per page (the server's own default for the state frame). */
        const val PAGE = 60
    }
}
