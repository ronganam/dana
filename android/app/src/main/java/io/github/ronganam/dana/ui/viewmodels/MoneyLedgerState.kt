package io.github.ronganam.dana.ui.viewmodels

import io.github.ronganam.dana.data.money.CheckReply
import io.github.ronganam.dana.data.money.MoneyApi
import io.github.ronganam.dana.data.money.MoneyApiError
import io.github.ronganam.dana.data.money.MoneyCompany
import io.github.ronganam.dana.data.money.MoneyLedger
import io.github.ronganam.dana.data.money.MoneySave
import io.github.ronganam.dana.data.money.SavingItem
import io.github.ronganam.dana.data.money.SavingsOverview
import io.github.ronganam.dana.data.money.Txn
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.json.JSONObject

/** The loaded Transactions list: [key] = the filters and month it was loaded for. */
data class TxnList(
    val rows: List<Txn> = emptyList(),
    val next: String? = null,
    val loading: Boolean = false,
    val loaded: Boolean = false,
    val error: String? = null,
    val key: String = ""
)

/** The open transaction: the list's row at once, the full detail when it arrives. */
data class TxnDetailState(val id: String, val txn: Txn?, val full: Boolean, val error: String? = null)

/** Which Transactions filter panel is open under the chips. */
enum class FilterPanel { CATEGORY, ACCOUNT, AMOUNT }

/**
 * The Accounts view's form: picking a company ([Mode.COMPANIES]), a login
 * (add when [sourceId] is null, else update) or removing a login. [values]
 * holds what is typed (secrets too) only until it is saved or cancelled:
 * never written anywhere, never logged.
 */
data class LoginForm(
    val mode: Mode,
    val sourceId: String? = null,
    val company: String? = null,
    val values: Map<String, String> = emptyMap(),
    val label: String = "",
    val alsoTransactions: Boolean = false,
    val busy: Boolean = false,
    val error: String? = null,
    /** 400 `invalid {field, message}`: shown at that field. */
    val fieldError: Pair<String, String>? = null
) {
    enum class Mode { COMPANIES, LOGIN, REMOVE }
    override fun toString(): String = "LoginForm($mode, $sourceId, $company)"
}

/**
 * State of Transactions (list, paging, the open detail), Accounts (logins,
 * companies, sign-in codes) and Save (ways to save, spending to watch, price
 * checks): loaded on demand and again when [MoneyController.revision] moves.
 * Each write updates what is on screen from its answer, offers Undo
 * (`undo_id`) and refreshes the views.
 */
class MoneyLedgerState(val money: MoneyController) {
    private val api get() = money.api
    private val scope get() = money.ctx.scope

    // ---------- Transactions ----------

    private val _txns = MutableStateFlow(TxnList())
    val txns: StateFlow<TxnList> = _txns.asStateFlow()
    private var txJob: Job? = null

    private val _searchDraft = MutableStateFlow<String?>(null)
    /** What is typed in the search box (null = follow the filter). */
    val searchDraft: StateFlow<String?> = _searchDraft.asStateFlow()
    fun setSearchDraft(text: String?) { _searchDraft.value = text }

    private val _panel = MutableStateFlow<FilterPanel?>(null)
    val panel: StateFlow<FilterPanel?> = _panel.asStateFlow()
    fun openPanel(p: FilterPanel?) { _panel.value = if (_panel.value == p) null else p }

    private val _panelQuery = MutableStateFlow("")
    /** The category panel's (and the detail picker's) search text. */
    val panelQuery: StateFlow<String> = _panelQuery.asStateFlow()
    fun setPanelQuery(q: String) { _panelQuery.value = q }

    private fun keyOf(f: TxnFilter, month: String) = listOf(f.q.trim(), f.category, f.account, f.min, f.max, f.allTime, if (f.allTime) "" else month).joinToString("|")

    /**
     * Load the list for [filter] and [month]. The same filters again (the
     * server changed, a write) refetch the rows already loaded (up to 200) in
     * place; new filters start over.
     */
    fun loadTxns(filter: TxnFilter, month: String) {
        val key = keyOf(filter, month)
        val have = _txns.value
        val keep = have.key == key && have.loaded
        val limit = if (keep) minOf(200, maxOf(PAGE, have.rows.size)) else PAGE
        txJob?.cancel()
        _txns.value = if (keep) have.copy(loading = true) else TxnList(loading = true, key = key)
        txJob = scope.launch {
            try {
                val o = api.get("/transactions", MoneyLedger.query(filter.q, filter.category, filter.account, filter.min, filter.max, filter.allTime, month, limit))
                _txns.value = TxnList(Txn.parseList(o), o.optString("next_cursor").takeIf { !o.isNull("next_cursor") && it.isNotBlank() }, false, true, null, key)
            } catch (e: Exception) {
                if (e is CancellationException) throw e
                _txns.update { it.copy(loading = false, error = MoneyLedger.errorText(e)) }
            }
        }
    }

    /** The next page (scrolling near the end). */
    fun more(filter: TxnFilter, month: String) {
        val have = _txns.value
        val cursor = have.next ?: return
        if (have.loading || have.key != keyOf(filter, month)) return
        _txns.value = have.copy(loading = true)
        txJob = scope.launch {
            try {
                val o = api.get("/transactions", MoneyLedger.query(filter.q, filter.category, filter.account, filter.min, filter.max, filter.allTime, month, PAGE, cursor))
                _txns.update {
                    it.copy(
                        rows = MoneyLedger.merge(it.rows, Txn.parseList(o)),
                        next = o.optString("next_cursor").takeIf { !o.isNull("next_cursor") && it.isNotBlank() },
                        loading = false, error = null
                    )
                }
            } catch (e: Exception) {
                if (e is CancellationException) throw e
                _txns.update { it.copy(loading = false, error = MoneyLedger.errorText(e)) }
            }
        }
    }

    // ---------- the detail ----------

    private val _detail = MutableStateFlow<TxnDetailState?>(null)
    val detail: StateFlow<TxnDetailState?> = _detail.asStateFlow()

    private val _noteDrafts = MutableStateFlow<Map<String, String>>(emptyMap())
    /** Notes being typed, per transaction (saved on Done). */
    val noteDrafts: StateFlow<Map<String, String>> = _noteDrafts.asStateFlow()

    private val _picker = MutableStateFlow<String?>(null)
    /** The transaction whose category picker is open in the detail. */
    val picker: StateFlow<String?> = _picker.asStateFlow()
    private val _forMerchant = MutableStateFlow(true)
    /** The picker's scope: always for this merchant (default) or only this one. */
    val forMerchant: StateFlow<Boolean> = _forMerchant.asStateFlow()

    fun openPicker(id: String?) {
        _picker.value = id
        _forMerchant.value = true
        _panelQuery.value = ""
    }
    fun setForMerchant(on: Boolean) { _forMerchant.value = on }

    /** Load the detail of [id]; the list's row shows meanwhile. */
    fun loadDetail(id: String) {
        val cur = _detail.value
        val row = _txns.value.rows.firstOrNull { it.id == id }
        if (cur?.id != id) {
            _detail.value = TxnDetailState(id, row, full = false)
            if (_picker.value != id) _picker.value = null
        }
        scope.launch {
            try {
                val t = Txn.parse(api.get("/transactions/${MoneyApi.enc(id)}"))
                if (_detail.value?.id == id) _detail.value = TxnDetailState(id, t, full = true)
            } catch (e: Exception) {
                if (e is CancellationException) throw e
                if (_detail.value?.id != id) return@launch
                if (e is MoneyApiError && e.code == 404) {
                    money.closeDetail()
                    money.ctx.messages.post("That transaction isn't there any more.")
                } else _detail.update { it?.copy(error = MoneyLedger.errorText(e)) }
            }
        }
    }

    /** A PATCH answer's transaction replaces the row in the list and the open detail. */
    private fun put(answer: JSONObject) {
        val o = answer.optJSONObject("transaction") ?: return
        val t = Txn.parse(o)
        _txns.update { l -> l.copy(rows = l.rows.map { if (it.id == t.id) t.copy(extras = null) else it }) }
        _detail.update { d -> if (d?.id == t.id) d.copy(txn = t, full = true) else d }
    }

    private fun patchTxn(id: String, body: JSONObject, words: (JSONObject) -> String, after: () -> Unit = {}) =
        write { val a = api.patch("/transactions/${MoneyApi.enc(id)}", body); put(a); after(); a to words(a) }

    fun setCategory(t: Txn, category: String, label: String, forMerchant: Boolean) {
        _picker.value = null
        patchTxn(t.id, JSONObject().put("category", category).put("for_merchant", forMerchant), { MoneyLedger.undoText(it, label, forMerchant) })
    }

    fun setFlow(t: Txn, flow: String) = patchTxn(t.id, JSONObject().put("flow", flow), { MoneyLedger.flowText(it) })

    /** One-off / Yearly: [state] yes | no | auto. */
    fun setMark(t: Txn, mark: String, state: String) {
        val value: Boolean? = when (state) { "yes" -> true; "no" -> false; else -> null }
        patchTxn(t.id, JSONObject().put(mark, value ?: JSONObject.NULL), { MoneyLedger.markText(it, mark, value) })
    }

    fun setNoteDraft(id: String, text: String?) {
        _noteDrafts.update { if (text == null) it - id else it + (id to text) }
    }

    fun saveNote(id: String) {
        val note = _noteDrafts.value[id] ?: return
        patchTxn(id, JSONObject().put("note", note), { MoneyLedger.undoText(it, null) }, after = { setNoteDraft(id, null) })
    }

    // ---------- Accounts ----------

    private val _companies = MutableStateFlow<List<MoneyCompany>?>(null)
    val companies: StateFlow<List<MoneyCompany>?> = _companies.asStateFlow()

    private val _syncBusy = MutableStateFlow<Set<String>>(emptySet())
    val syncBusy: StateFlow<Set<String>> = _syncBusy.asStateFlow()

    private val _form = MutableStateFlow<LoginForm?>(null)
    val form: StateFlow<LoginForm?> = _form.asStateFlow()

    private suspend fun loadCompanies(): List<MoneyCompany>? {
        _companies.value?.let { return it }
        return try {
            MoneyCompany.parseAll(api.get("/companies")).also { _companies.value = it }
        } catch (e: Exception) {
            if (e is CancellationException) throw e
            null
        }
    }

    /** "Sync now": 202 → follow it closely; 429 / 409 in words. */
    fun syncNow(sourceId: String) {
        if (sourceId in _syncBusy.value) return
        _syncBusy.update { it + sourceId }
        scope.launch {
            try {
                api.post("/sources/${MoneyApi.enc(sourceId)}/sync")
                money.ctx.messages.post("Syncing — new transactions show up here by themselves.")
                money.watchClosely()
                delay(1200)
                money.loadSources()
            } catch (e: Exception) {
                if (e is CancellationException) throw e
                money.ctx.messages.post(MoneyLedger.errorText(e))
            } finally {
                _syncBusy.update { it - sourceId }
            }
        }
    }

    fun openAdd() {
        _form.value = LoginForm(LoginForm.Mode.COMPANIES)
        scope.launch {
            if (loadCompanies() == null) _form.update { f -> if (f?.mode == LoginForm.Mode.COMPANIES) f.copy(error = "Couldn't load the list of banks and cards.") else f }
        }
    }

    fun pickCompany(id: String) { _form.value = LoginForm(LoginForm.Mode.LOGIN, company = id) }

    /** Update a login (also from a banner: it shows Accounts first). */
    fun openEdit(sourceId: String) {
        val src = money.sources.value?.firstOrNull { it.id == sourceId } ?: return
        if (money.view.value != MoneyView.ACCOUNTS) money.select(MoneyView.ACCOUNTS)
        _form.value = LoginForm(LoginForm.Mode.LOGIN, sourceId = sourceId, company = src.company, label = src.label ?: "")
        scope.launch { loadCompanies() }
    }

    fun openRemove(sourceId: String) { _form.value = LoginForm(LoginForm.Mode.REMOVE, sourceId = sourceId) }

    /** Close the form; what was typed goes with it. */
    fun closeForm() { _form.value = null }

    fun editForm(change: (LoginForm) -> LoginForm) { _form.update { it?.let(change) } }

    fun submitLogin() {
        val f = _form.value ?: return
        if (f.mode != LoginForm.Mode.LOGIN || f.busy) return
        val company = _companies.value?.firstOrNull { it.id == f.company } ?: return
        val credentials = JSONObject()
        company.fields.forEach { credentials.put(it.name, f.values[it.name] ?: "") }
        val filled = company.fields.count { !f.values[it.name].isNullOrEmpty() }
        val label = f.label.trim()
        val body = JSONObject()
        if (f.sourceId != null) {
            if (filled in 1 until company.fields.size) { _form.value = f.copy(error = "Fill in every field to change the login.", fieldError = null); return }
            if (filled > 0) body.put("credentials", credentials)
            body.put("label", label)
        } else {
            if (filled < company.fields.size) { _form.value = f.copy(error = "Fill in every field.", fieldError = null); return }
            body.put("company", company.id).put("credentials", credentials)
            if (label.isNotEmpty()) body.put("label", label)
        }
        _form.value = f.copy(busy = true, error = null, fieldError = null)
        scope.launch {
            try {
                if (f.sourceId != null) api.patch("/sources/${MoneyApi.enc(f.sourceId)}", body) else api.post("/sources", body)
                // The typed login leaves memory with the form.
                if (_form.value?.mode == LoginForm.Mode.LOGIN) _form.value = null
                money.ctx.messages.post(
                    when {
                        f.sourceId == null -> "${company.name} added — the first sync has started"
                        filled > 0 -> "${company.name}: login updated, syncing now"
                        else -> "Saved"
                    }
                )
                money.watchClosely()
                money.refresh()
            } catch (e: Exception) {
                if (e is CancellationException) throw e
                _form.update { cur ->
                    if (cur?.mode != LoginForm.Mode.LOGIN) cur
                    else if (e is MoneyApiError && e.code == 400 && e.field != null) cur.copy(busy = false, fieldError = e.field to e.message)
                    else cur.copy(busy = false, error = MoneyLedger.errorText(e))
                }
            }
        }
    }

    fun submitRemove() {
        val f = _form.value ?: return
        if (f.mode != LoginForm.Mode.REMOVE || f.busy || f.sourceId == null) return
        _form.value = f.copy(busy = true, error = null)
        scope.launch {
            try {
                api.delete("/sources/${MoneyApi.enc(f.sourceId)}", mapOf("transactions" to if (f.alsoTransactions) 1 else null))
                _form.value = null
                money.ctx.messages.post("Removed")
                money.refresh()
            } catch (e: Exception) {
                if (e is CancellationException) throw e
                _form.update { it?.copy(busy = false, error = MoneyLedger.errorText(e)) }
            }
        }
    }

    // ---------- sign-in codes ----------

    private val _codeDrafts = MutableStateFlow<Map<String, String>>(emptyMap())
    val codeDrafts: StateFlow<Map<String, String>> = _codeDrafts.asStateFlow()
    private val _codeBusy = MutableStateFlow<Set<String>>(emptySet())
    val codeBusy: StateFlow<Set<String>> = _codeBusy.asStateFlow()
    private val _codeErrors = MutableStateFlow<Map<String, String>>(emptyMap())
    val codeErrors: StateFlow<Map<String, String>> = _codeErrors.asStateFlow()
    private val _codeSent = MutableStateFlow<Set<String>>(emptySet())
    val codeSent: StateFlow<Set<String>> = _codeSent.asStateFlow()

    fun setCodeDraft(requestId: String, text: String) { _codeDrafts.update { it + (requestId to text.filter { c -> c.isDigit() }.take(8)) } }

    /** `POST code-requests/{id} {code}`; bad_code / not_pending in words. */
    fun sendCode(requestId: String) {
        val code = (_codeDrafts.value[requestId] ?: "").trim()
        if (!Regex("^\\d{4,8}$").matches(code)) { _codeErrors.update { it + (requestId to "The code is 4 to 8 digits.") }; return }
        if (requestId in _codeBusy.value) return
        _codeBusy.update { it + requestId }
        _codeErrors.update { it - requestId }
        scope.launch {
            var reload = false
            try {
                api.post("/code-requests/${MoneyApi.enc(requestId)}", JSONObject().put("code", code))
                _codeSent.update { it + requestId }
                _codeDrafts.update { it - requestId }
                money.watchClosely()
                reload = true
            } catch (e: Exception) {
                if (e is CancellationException) throw e
                _codeErrors.update { it + (requestId to MoneyLedger.errorText(e)) }
                reload = e is MoneyApiError && e.code == 409
            } finally {
                _codeBusy.update { it - requestId }
            }
            if (reload) { delay(1500); money.loadSources() }
        }
    }

    // ---------- Save ----------

    private val _savings = MutableStateFlow<SavingsOverview?>(null)
    val savings: StateFlow<SavingsOverview?> = _savings.asStateFlow()
    private val _savingsError = MutableStateFlow<String?>(null)
    val savingsError: StateFlow<String?> = _savingsError.asStateFlow()

    private val _saveBusy = MutableStateFlow<Set<String>>(emptySet())
    /** Items with a write or a check request in flight. */
    val saveBusy: StateFlow<Set<String>> = _saveBusy.asStateFlow()
    private val _checkNotes = MutableStateFlow<Map<String, CheckReply>>(emptyMap())
    /** A price check's last answer in words (a wait, an error, "still checking"). */
    val checkNotes: StateFlow<Map<String, CheckReply>> = _checkNotes.asStateFlow()
    private val _collapsed = MutableStateFlow<Set<String>>(emptySet())
    /** Collapsed sections ("save:choices"). */
    val collapsed: StateFlow<Set<String>> = _collapsed.asStateFlow()
    private val _targetFor = MutableStateFlow<String?>(null)
    /** The item whose monthly target field is open. */
    val targetFor: StateFlow<String?> = _targetFor.asStateFlow()
    private val _targetDraft = MutableStateFlow("")
    val targetDraft: StateFlow<String> = _targetDraft.asStateFlow()
    private val _targetError = MutableStateFlow<String?>(null)
    val targetError: StateFlow<String?> = _targetError.asStateFlow()

    private val polls = mutableMapOf<String, Job>()
    private val gaveUp = mutableSetOf<String>()

    private var savingsLoad: Job? = null

    /** Loads the ways to save; a call while one is loading joins it (the list and the pane both ask after a write). */
    fun loadSavings() {
        if (savingsLoad?.isActive == true) return
        savingsLoad = scope.launch {
            try {
                val sv = SavingsOverview.parse(api.get("/savings"))
                _savings.value = sv
                _savingsError.value = null
                sv.items.filter { it.market?.state == "checking" && it.key !in polls && it.key !in gaveUp }.forEach { poll(it.key) }
            } catch (e: Exception) {
                if (e is CancellationException) throw e
                _savingsError.value = MoneyLedger.errorText(e)
            }
        }
    }

    private fun putSaving(item: SavingItem) { _savings.update { it?.with(item) } }

    fun toggleSection(id: String) { _collapsed.update { if (id in it) it - id else it + id } }

    fun openTarget(item: SavingItem) {
        _targetFor.value = item.key
        _targetDraft.value = MoneySave.targetDraft(item)
        _targetError.value = null
    }
    fun setTargetDraft(text: String) { _targetDraft.value = text }
    fun closeTarget() { _targetFor.value = null; _targetError.value = null }

    fun submitTarget(key: String) {
        val target = MoneySave.targetAgorot(_targetDraft.value)
        if (target == null) { _targetError.value = "Type the most to spend a month in whole shekels, for example 650."; return }
        _targetError.value = null
        patchSaving(key, JSONObject().put("target", target))
    }

    /** A status, an answer, a target or a subscription line: the item takes the answer in place, with Undo. */
    fun patchSaving(key: String, body: JSONObject) {
        if (key in _saveBusy.value) return
        _saveBusy.update { it + key }
        if (body.has("answers")) _checkNotes.update { it - key }
        write(finally = { _saveBusy.update { it - key } }) {
            val a = api.patch("/savings/${MoneyApi.enc(key)}", body)
            val item = SavingItem.parse(a.optJSONObject("item") ?: JSONObject())
            putSaving(item)
            if (body.has("target") || body.has("status")) { _targetFor.value = null; _targetError.value = null }
            a to MoneySave.toast(item, body)
        }
    }

    /** A subscription line's Keep / Cancel; the same choice again clears it. */
    fun chooseSub(item: SavingItem, subKey: String, choice: String) {
        val cur = item.subitems.firstOrNull { it.key == subKey }?.choice
        patchSaving(item.key, JSONObject().put("subitem", JSONObject().put("key", subKey).put("choice", if (cur == choice) JSONObject.NULL else choice)))
    }

    fun answer(item: SavingItem, questionId: String, value: Any) {
        val q = item.questions.firstOrNull { it.id == questionId } ?: return
        if (q.answer?.toString() == value.toString()) return
        patchSaving(item.key, JSONObject().put("answers", JSONObject().put(questionId, value)))
    }

    /** "Check prices": only ever on this press. 202 (or 409: one runs) → poll the item. */
    fun startCheck(key: String) {
        if (key in _saveBusy.value) return
        _checkNotes.update { it - key }
        gaveUp.remove(key)
        _saveBusy.update { it + key }
        scope.launch {
            val reply = try {
                val a = api.post("/savings/${MoneyApi.enc(key)}/check")
                MoneySave.checkReply(202, a)
            } catch (e: Exception) {
                if (e is CancellationException) throw e
                if (e is MoneyApiError) MoneySave.checkReply(e.code, e.body) else CheckReply("error", MoneyLedger.errorText(e))
            } finally {
                _saveBusy.update { it - key }
            }
            val it = _savings.value?.item(key)
            when {
                reply.kind == "checking" -> {
                    if (it?.market != null) putSaving(it.copy(market = it.market.copy(state = "checking", error = null)))
                    poll(key)
                }
                reply.kind == "question" && reply.question != null && it?.market != null -> {
                    val has = it.questions.any { q -> q.id == reply.question.id }
                    putSaving(it.copy(market = it.market.copy(needsAnswer = reply.question.id), questions = if (has) it.questions else it.questions + reply.question))
                }
                else -> _checkNotes.update { n -> n + (key to reply) }
            }
        }
    }

    /** GET the item every 3 s while its check runs, for up to 90 s; then the views refresh. */
    private fun poll(key: String) {
        if (polls[key]?.isActive == true) return
        polls[key] = scope.launch {
            val started = System.currentTimeMillis()
            try {
                while (true) {
                    delay(CHECK_POLL_MS)
                    try {
                        val item = SavingItem.parse(api.get("/savings/${MoneyApi.enc(key)}"))
                        putSaving(item)
                        if (item.market?.state != "checking") { money.refresh(); return@launch }
                    } catch (e: Exception) {
                        if (e is CancellationException) throw e
                        if (e is MoneyApiError && (e.code == 404 || e.code == 403)) { money.refresh(); return@launch }
                    }
                    if (System.currentTimeMillis() - started >= CHECK_POLL_FOR_MS) {
                        gaveUp += key
                        _checkNotes.update { it + (key to CheckReply("wait", "Still checking. The offers show up here when it's done: come back in a few minutes.")) }
                        return@launch
                    }
                }
            } finally {
                polls.remove(key)
            }
        }
    }

    // ---------- writes ----------

    /**
     * Run a write whose [block] answers (the server's answer, the line to
     * show): an `undo_id` makes it an Undo snackbar, then every view
     * refreshes. A failure is said in words.
     */
    private fun write(finally: () -> Unit = {}, block: suspend () -> Pair<JSONObject, String>): Job = scope.launch {
        try {
            val (answer, text) = block()
            val undoId = answer.optString("undo_id").ifBlank { null }
            if (undoId != null) money.ctx.messages.postUndoable(text, onUndo = { money.undo(undoId) })
            else money.ctx.messages.post(text)
            money.refresh()
        } catch (e: Exception) {
            if (e is CancellationException) throw e
            money.ctx.messages.post(MoneyLedger.errorText(e))
        } finally {
            finally()
        }
    }

    /** The account changed: drop everything loaded. */
    fun forget() {
        txJob?.cancel()
        savingsLoad?.cancel()
        polls.values.forEach { it.cancel() }
        polls.clear()
        gaveUp.clear()
        _txns.value = TxnList()
        _searchDraft.value = null
        _panel.value = null
        _detail.value = null
        _noteDrafts.value = emptyMap()
        _picker.value = null
        _companies.value = null
        _syncBusy.value = emptySet()
        _form.value = null
        _codeDrafts.value = emptyMap()
        _codeErrors.value = emptyMap()
        _codeSent.value = emptySet()
        _savings.value = null
        _savingsError.value = null
        _checkNotes.value = emptyMap()
        _targetFor.value = null
    }

    companion object {
        const val PAGE = 50
        const val CHECK_POLL_MS = 3_000L
        const val CHECK_POLL_FOR_MS = 90_000L
    }
}
