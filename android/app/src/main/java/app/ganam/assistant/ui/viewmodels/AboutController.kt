package app.ganam.assistant.ui.viewmodels

import app.ganam.assistant.data.models.AboutFact
import app.ganam.assistant.data.models.AboutPage
import app.ganam.assistant.data.models.LearnedPhrase
import app.ganam.assistant.data.models.SelfCheck
import app.ganam.assistant.data.network.ApiException
import app.ganam.assistant.data.network.ApiService
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * About you (server `docs/about.md`): what Dana keeps about the owner
 * (`/api/about`), the phrases she runs without the model (`/api/shortcuts`)
 * and the self-check (`/api/checks`). Edits and removals are applied at once
 * and offer Undo through the server's undo registry.
 */
class AboutController(private val ctx: ControllerContext) {
    private val _page = MutableStateFlow<AboutPage?>(null)
    val page: StateFlow<AboutPage?> = _page.asStateFlow()

    private val _phrases = MutableStateFlow<List<LearnedPhrase>>(emptyList())
    val phrases: StateFlow<List<LearnedPhrase>> = _phrases.asStateFlow()

    private val _checks = MutableStateFlow<List<SelfCheck>>(emptyList())
    val checks: StateFlow<List<SelfCheck>> = _checks.asStateFlow()

    private val _loading = MutableStateFlow(false)
    val loading: StateFlow<Boolean> = _loading.asStateFlow()

    private val _failed = MutableStateFlow(false)
    /** The facts couldn't be loaded (offline, old server). */
    val failed: StateFlow<Boolean> = _failed.asStateFlow()

    fun refresh() {
        ctx.scope.launch {
            _loading.value = true
            val about = async { ctx.load("About") { ctx.api.getAbout() } }
            val phrases = async { ctx.load("Shortcuts") { ctx.api.getShortcuts() } }
            val checks = async { ctx.load("Checks") { ctx.api.getChecks() } }
            val a = about.await()
            if (a != null) _page.value = a
            _failed.value = a == null && _page.value == null
            phrases.await()?.let { _phrases.value = it }
            checks.await()?.let { _checks.value = it }
            _loading.value = false
        }
    }

    /** "Add" at the end of the page; Dana files it under a section. */
    fun add(text: String, section: String? = null) {
        val t = text.trim()
        if (t.isEmpty()) return
        ctx.action("Couldn't add that") {
            val (fact, undoId) = ctx.api.addAboutFact(t, section)
            reload()
            if (fact != null) offerUndo("Added", undoId)
            true
        }
    }

    /** A line edited in place. */
    fun edit(fact: AboutFact, text: String) {
        val t = text.trim()
        if (t.isEmpty() || t == fact.text) return
        replaceFact(fact.id) { it.copy(text = t, sourceKind = "edited") }
        ctx.action("Couldn't save the change") {
            val undoId = ctx.api.updateAboutFact(fact.id, t, null)
            reload()
            offerUndo("Saved", undoId)
            true
        }
    }

    /** × on a line: forgotten at once, with Undo. */
    fun remove(fact: AboutFact) {
        val before = _page.value
        _page.value = before?.let { p -> p.copy(sections = p.sections.map { s -> s.copy(facts = s.facts.filterNot { it.id == fact.id }) }) }
        ctx.scope.launch {
            try {
                val undoId = ctx.api.deleteAboutFact(fact.id)
                offerUndo("Forgot it", undoId)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _page.value = before
                ctx.messages.post("Couldn't forget that: ${reason(e)}")
            }
        }
    }

    /** A learned phrase goes back to the model. The server has no undo for this. */
    fun removePhrase(p: LearnedPhrase) {
        val before = _phrases.value
        _phrases.value = before.filterNot { it.id == p.id }
        ctx.scope.launch {
            try {
                if (!ctx.api.deleteShortcut(p.id)) _phrases.value = before
                ctx.messages.post("“${p.phrase}” goes through Dana again")
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _phrases.value = before
                ctx.messages.post("Couldn't remove it: ${reason(e)}")
            }
        }
    }

    /** "Ignore for a week" on a failed check. */
    fun ignore(check: SelfCheck) {
        ctx.scope.launch {
            try {
                val r = ctx.api.ignoreCheck(check.key, 7)
                reloadChecks()
                offerUndo(r.message.ifBlank { "Ignored for a week" }, r.undoId) { reloadChecks() }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                ctx.messages.post("Couldn't ignore it: ${reason(e)}")
            }
        }
    }

    private fun replaceFact(id: String, change: (AboutFact) -> AboutFact) {
        _page.value = _page.value?.let { p -> p.copy(sections = p.sections.map { s -> s.copy(facts = s.facts.map { if (it.id == id) change(it) else it }) }) }
    }

    private suspend fun reload() {
        ctx.load("About") { ctx.api.getAbout() }?.let { _page.value = it }
    }

    private suspend fun reloadChecks() {
        ctx.load("Checks") { ctx.api.getChecks() }?.let { _checks.value = it }
    }

    private fun offerUndo(text: String, undoId: String?, after: suspend () -> Unit = { reload() }) {
        if (undoId.isNullOrBlank()) {
            ctx.messages.post(text)
            return
        }
        ctx.messages.postUndoable(text, onUndo = {
            ctx.scope.launch {
                try {
                    when (ctx.api.undo(undoId)) {
                        ApiService.UndoOutcome.DONE -> ctx.messages.post("Undone.")
                        ApiService.UndoOutcome.TOO_LATE -> ctx.messages.post("Too late to undo that")
                    }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    ctx.messages.post(reason(e))
                }
                after()
            }
        })
    }

    private fun reason(e: Exception): String =
        if (e is ApiException && e.code != 401 && e.code != 403) (e.message ?: describeError(e)).removePrefix("Undo failed: ") else describeError(e)
}
