package io.github.ronganam.dana.ui.viewmodels

import io.github.ronganam.dana.data.models.SearchResult
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * Search from the ask bar (`GET /api/search`): 2+ characters, 250 ms after
 * the last keystroke; a new text cancels the request in flight, so results
 * always belong to what is typed.
 */
class SearchController(private val ctx: ControllerContext) {

    /** What the results are for, and whether they are still coming. */
    data class State(val query: String = "", val results: List<SearchResult> = emptyList(), val searching: Boolean = false, val failed: Boolean = false)

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    private var job: Job? = null

    fun setQuery(text: String) {
        val q = text.trim()
        if (q == _state.value.query) return
        job?.cancel()
        if (!isSearchable(q)) {
            _state.value = State(query = q)
            return
        }
        // Keep the previous results on screen while the new ones come.
        _state.value = _state.value.copy(query = q, searching = true, failed = false)
        job = ctx.scope.launch {
            delay(250)
            try {
                val results = ctx.api.now.search(q, limit = 5)
                if (_state.value.query == q) _state.value = State(q, results, searching = false)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (_state.value.query == q) _state.value = State(q, emptyList(), searching = false, failed = true)
            }
        }
    }

    companion object {
        fun isSearchable(text: String) = text.trim().length >= 2

        /** Light grouping, in the order the kinds are shown. */
        val KIND_ORDER = listOf("reminder", "person", "call", "notification", "note", "shopping")

        fun kindLabel(kind: String) = when (kind) {
            "reminder" -> "Reminders"
            "person" -> "People"
            "call" -> "Calls"
            "notification" -> "Notifications"
            "note" -> "Projects" // project entries and projects (server core/search.py)
            "shopping" -> "Shopping"
            else -> kind.replaceFirstChar { it.uppercase() }
        }

        /** Results grouped by kind (known kinds first, in [KIND_ORDER]; the server's order inside a group). */
        fun grouped(results: List<SearchResult>): List<Pair<String, List<SearchResult>>> =
            results.groupBy { it.kind }.entries
                .sortedBy { KIND_ORDER.indexOf(it.key).let { i -> if (i < 0) KIND_ORDER.size else i } }
                .map { it.key to it.value }
    }
}
