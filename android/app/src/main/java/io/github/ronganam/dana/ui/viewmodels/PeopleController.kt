package io.github.ronganam.dana.ui.viewmodels

import io.github.ronganam.dana.data.models.Person
import io.github.ronganam.dana.data.models.PersonDetail
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

/** People: everyone Dana knows from calls and messages (server docs/people.md). */
class PeopleController(private val ctx: ControllerContext) {

    private val _people = MutableStateFlow<List<Person>>(emptyList())
    val people: StateFlow<List<Person>> = _people.asStateFlow()

    private val _loading = MutableStateFlow(false)
    val loading: StateFlow<Boolean> = _loading.asStateFlow()

    /** recent | pinned | drifting | name */
    private val _sort = MutableStateFlow("recent")
    val sort: StateFlow<String> = _sort.asStateFlow()

    private val _query = MutableStateFlow("")
    val query: StateFlow<String> = _query.asStateFlow()

    private val _selectedId = MutableStateFlow<String?>(null)
    val selectedId: StateFlow<String?> = _selectedId.asStateFlow()

    private val _detail = MutableStateFlow<PersonDetail?>(null)
    val detail: StateFlow<PersonDetail?> = _detail.asStateFlow()

    private val _detailLoading = MutableStateFlow(false)
    val detailLoading: StateFlow<Boolean> = _detailLoading.asStateFlow()

    private val _briefRefreshing = MutableStateFlow(false)
    val briefRefreshing: StateFlow<Boolean> = _briefRefreshing.asStateFlow()

    private var searchJob: Job? = null

    fun fetch() {
        ctx.scope.launch {
            _loading.value = true
            try {
                ctx.load("People") { ctx.api.getPeople(_sort.value, _query.value) }?.let { _people.value = it }
            } finally {
                _loading.value = false
            }
        }
    }

    fun setSort(sort: String) {
        if (_sort.value == sort) return
        _sort.value = sort
        fetch()
    }

    fun setQuery(q: String) {
        _query.value = q
        searchJob?.cancel()
        searchJob = ctx.scope.launch { delay(250); fetch() }
    }

    fun select(id: String?) {
        _selectedId.value = id
        if (id == null) { _detail.value = null; return }
        if (_detail.value?.person?.id != id) _detail.value = null
        loadDetail(id)
    }

    /** Re-read the open person after something changed elsewhere (a task was completed). */
    fun reload(id: String) {
        ctx.scope.launch { delay(600); if (_selectedId.value == id) loadDetail(id) }
    }

    private fun loadDetail(id: String) {
        ctx.scope.launch {
            _detailLoading.value = true
            try {
                ctx.load("Person") { ctx.api.getPerson(id) }?.let { if (_selectedId.value == id) _detail.value = it }
            } finally {
                _detailLoading.value = false
            }
        }
    }

    fun update(id: String, changes: JSONObject) {
        ctx.action("Couldn't save") {
            ctx.api.updatePerson(id, changes).also { if (it) { loadDetail(id); fetch() } }
        }
    }

    fun setPinned(p: Person, pinned: Boolean) {
        _people.value = _people.value.map { if (it.id == p.id) it.copy(pinned = pinned) else it }
        update(p.id, JSONObject().put("pinned", pinned))
    }

    fun save(id: String, name: String, relation: String, notes: String, birthday: String, aliases: List<String>) {
        update(id, JSONObject().put("name", name).put("relation", relation).put("notes", notes)
            .put("birthday", birthday).put("aliases", JSONArray(aliases)))
    }

    fun hide(id: String) {
        update(id, JSONObject().put("hidden", true))
        if (_selectedId.value == id) select(null)
    }

    fun refreshBrief(id: String) {
        ctx.scope.launch {
            _briefRefreshing.value = true
            try {
                if (ctx.load("PersonBrief") { ctx.api.refreshPersonBrief(id) } == true) loadDetail(id)
                else ctx.messages.post("Couldn't refresh the summary")
            } finally {
                _briefRefreshing.value = false
            }
        }
    }
}
