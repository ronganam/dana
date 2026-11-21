package io.github.ronganam.dana.ui.viewmodels

import android.content.Context
import io.github.ronganam.dana.data.money.MoneyApi
import io.github.ronganam.dana.data.money.MoneyApiError
import io.github.ronganam.dana.data.money.MoneyText
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.json.JSONObject

/** The Money views (server docs/money.md → Money tab). [monthly]: tied to the month switcher. */
enum class MoneyView(val label: String, val monthly: Boolean) {
    OVERVIEW("Overview", true),
    SPENDING("Spending", true),
    CASHFLOW("Cash flow", true),
    SAVE("Save", false),
    INVESTMENTS("Investments", false),
    MEALS("Meal card", false),
    TRANSACTIONS("Transactions", true),
    ACCOUNTS("Accounts", false)
}

/** What is open on top of a view: a transaction or a saving item (detail pane / full screen on the cover). */
sealed interface MoneyDetail {
    val title: String
    data class Transaction(val id: String, override val title: String = "") : MoneyDetail
    data class Saving(val key: String, override val title: String = "") : MoneyDetail
}

/**
 * The Transactions filters (web: the chips and the search box). [allTime]
 * false = the month of the switcher. Any view may set them (a category
 * focused on Spending, an insight's "N to sort") and open Transactions.
 */
data class TxnFilter(
    val q: String = "",
    val category: String? = null,
    val account: String? = null,
    val min: String = "",
    val max: String = "",
    val allTime: Boolean = false
) {
    val isEmpty: Boolean get() = q.isBlank() && category == null && account == null && min.isBlank() && max.isBlank() && !allTime
}

/** A login (`GET /api/money/sources`). */
data class MoneySource(
    val id: String,
    val company: String,
    val companyName: String,
    val label: String?,
    val state: String,
    val stateReason: String?,
    val errorType: String?,
    val lastSyncAt: String?,
    val lastOkAt: String?,
    val nextSyncAt: String?,
    val credentialsSet: Boolean,
    val canSync: Boolean,
    val accounts: List<MoneyAccount>,
    /** A pending SMS code request: id + expiry (ISO). */
    val codeRequest: Pair<String, String>?
) {
    val name: String get() = label?.takeIf { it.isNotBlank() } ?: companyName
    val busy: Boolean get() = state == "syncing" || state == "needs_code"

    companion object {
        fun parse(o: JSONObject): MoneySource {
            val accounts = o.optJSONArray("accounts")
            val code = o.optJSONObject("code_request")
            return MoneySource(
                id = o.optString("id"),
                company = o.optString("company"),
                companyName = o.optString("company_name"),
                label = o.optString("label").takeIf { !o.isNull("label") && it.isNotBlank() },
                state = o.optString("state"),
                stateReason = o.optString("state_reason").takeIf { !o.isNull("state_reason") && it.isNotBlank() },
                errorType = o.optString("error_type").takeIf { !o.isNull("error_type") && it.isNotBlank() },
                lastSyncAt = o.optString("last_sync_at").takeIf { !o.isNull("last_sync_at") && it.isNotBlank() },
                lastOkAt = o.optString("last_ok_at").takeIf { !o.isNull("last_ok_at") && it.isNotBlank() },
                nextSyncAt = o.optString("next_sync_at").takeIf { !o.isNull("next_sync_at") && it.isNotBlank() },
                credentialsSet = o.optBoolean("credentials_set"),
                canSync = o.optBoolean("can_sync", true),
                accounts = (0 until (accounts?.length() ?: 0)).map { MoneyAccount.parse(accounts!!.getJSONObject(it)) },
                codeRequest = code?.let { it.optString("id") to it.optString("expires_at") }
            )
        }
    }
}

/** An account of a login: `kind` bank | card | investment | meal; amounts in agorot. */
data class MoneyAccount(
    val id: String,
    val kind: String,
    val number: String,
    val label: String?,
    val balance: Long?,
    val balanceAt: String?,
    val nextCharge: Long?,
    val nextChargeDate: String?
) {
    companion object {
        fun parse(o: JSONObject) = MoneyAccount(
            id = o.optString("id"),
            kind = o.optString("kind"),
            number = o.optString("number"),
            label = o.optString("label").takeIf { !o.isNull("label") && it.isNotBlank() },
            balance = if (o.isNull("balance") || !o.has("balance")) null else o.optLong("balance"),
            balanceAt = o.optString("balance_at").takeIf { !o.isNull("balance_at") && it.isNotBlank() },
            nextCharge = if (o.isNull("next_charge") || !o.has("next_charge")) null else o.optLong("next_charge"),
            nextChargeDate = o.optString("next_charge_date").takeIf { !o.isNull("next_charge_date") && it.isNotBlank() }
        )
    }
}

/**
 * The Money place (More → Money; server docs/money.md). This core holds what
 * every view shares: the open view, the month, the open detail, the
 * Transactions filters, the overview / sources / categories payloads, and
 * the live loop (`GET changes` every 30 s while shown, every 5 s while a
 * sync runs or a code is pending) that bumps [revision] so each view
 * refetches its own data. The views' own state lives in [dash]
 * ([MoneyDashState]: Overview, Spending, Cash flow, Investments, Meal card)
 * and [ledger] ([MoneyLedgerState]: Transactions, detail, Accounts, Save).
 */
class MoneyController(val ctx: ControllerContext, appContext: Context) {
    val api = MoneyApi(appContext) { ctx.api }

    private val _view = MutableStateFlow(MoneyView.OVERVIEW)
    val view: StateFlow<MoneyView> = _view.asStateFlow()

    private val _month = MutableStateFlow(MoneyText.thisMonth())
    /** `YYYY-MM` of the month switcher. */
    val month: StateFlow<String> = _month.asStateFlow()

    private val _detail = MutableStateFlow<MoneyDetail?>(null)
    val detail: StateFlow<MoneyDetail?> = _detail.asStateFlow()

    private val _filter = MutableStateFlow(TxnFilter())
    val filter: StateFlow<TxnFilter> = _filter.asStateFlow()

    private val _overview = MutableStateFlow<JSONObject?>(null)
    /** `GET overview?month=` of [month] (null while it loads: show skeletons, never another month's numbers). */
    val overview: StateFlow<JSONObject?> = _overview.asStateFlow()

    private val _sources = MutableStateFlow<List<MoneySource>?>(null)
    val sources: StateFlow<List<MoneySource>?> = _sources.asStateFlow()

    private val _categories = MutableStateFlow<JSONObject?>(null)
    /** `GET categories?period=<month>`: the month's groups and the `taxonomy` (category keys and labels). */
    val categories: StateFlow<JSONObject?> = _categories.asStateFlow()

    private val _revision = MutableStateFlow(0)
    /** Bumped when the server's money data changed (or Refresh): views reload what they show. */
    val revision: StateFlow<Int> = _revision.asStateFlow()

    private val _loadError = MutableStateFlow<String?>(null)
    /** Why the shared payloads couldn't be read (shown instead of the view), null when fine. */
    val loadError: StateFlow<String?> = _loadError.asStateFlow()

    /** Until this time (ms) the live loop polls every 5 s (after Sync now, a sent code, a new login). */
    @Volatile private var fastUntil = 0L
    private var changesCursor: String? = null

    /** Open Dana's thread about something on screen ("Ask Dana": kinds `transaction`, `saving`); set by the destination. */
    var ask: (ThreadContext) -> Unit = {}

    val dash = MoneyDashState(this)
    val ledger = MoneyLedgerState(this)

    // ---------- navigation ----------

    fun select(view: MoneyView) {
        _view.value = view
        _detail.value = null
    }

    fun setMonth(month: String) {
        if (month == _month.value) return
        _month.value = month
        _overview.value = null
        _categories.value = null
        loadShared()
        _revision.value++
    }

    fun shiftMonth(by: Int) = setMonth(MoneyText.shiftMonth(_month.value, by))

    fun openTransaction(id: String, title: String = "") { _detail.value = MoneyDetail.Transaction(id, title) }
    fun openSaving(key: String, title: String = "") { _detail.value = MoneyDetail.Saving(key, title) }
    fun closeDetail() { _detail.value = null }

    fun setFilter(filter: TxnFilter) { _filter.value = filter }

    /** Show Transactions with [filter] (a category, a search, "only uncategorized": category `uncategorized`). */
    fun openTransactions(filter: TxnFilter) {
        _filter.value = filter
        select(MoneyView.TRANSACTIONS)
    }

    // ---------- loading ----------

    /** Overview, sources and categories for the month; then every view reloads ([revision]). */
    fun refresh() {
        loadShared()
        _revision.value++
    }

    private fun loadShared() {
        val month = _month.value
        ctx.scope.launch {
            try {
                val o = api.get("/overview", mapOf("month" to month))
                if (month == _month.value) _overview.value = o
                _loadError.value = null
            } catch (e: Exception) {
                if (e is kotlinx.coroutines.CancellationException) throw e
                _loadError.value = (e as? MoneyApiError)?.message ?: describeError(e)
            }
        }
        ctx.scope.launch { loadSources() }
        ctx.scope.launch {
            ctx.load("Money") { api.get("/categories", mapOf("period" to month)) }?.let { if (month == _month.value) _categories.value = it }
        }
    }

    suspend fun loadSources() {
        ctx.load("Money") { api.get("/sources") }?.let { o ->
            val a = o.optJSONArray("sources")
            _sources.value = (0 until (a?.length() ?: 0)).map { MoneySource.parse(a!!.getJSONObject(it)) }
        }
    }

    /** Poll faster for a while (a sync started, a code was sent, a login was added). */
    fun watchClosely(minutes: Int = 4) {
        fastUntil = System.currentTimeMillis() + minutes * 60_000L
    }

    /**
     * Run while Money is on screen (a LaunchedEffect of the destination):
     * loads everything once, then follows `GET changes?since=` and refreshes
     * when the server says something changed.
     */
    suspend fun live() {
        refresh()
        while (true) {
            val busy = _sources.value?.any { it.busy } == true || System.currentTimeMillis() < fastUntil
            delay(if (busy) 5_000 else 30_000)
            val o = ctx.load("Money") { api.get("/changes", mapOf("since" to changesCursor)) } ?: continue
            val first = changesCursor == null
            changesCursor = o.optString("cursor").ifBlank { null }
            if (!first && o.optBoolean("changed")) refresh()
        }
    }

    /** Has the user an account of [kind] (investment, meal)? Decides whether the view shows. */
    fun hasAccount(kind: String): Boolean = _sources.value?.any { s -> s.accounts.any { it.kind == kind } } == true

    /** The views to offer: Investments / Meal card only with such an account. */
    fun availableViews(sources: List<MoneySource>?): List<MoneyView> = MoneyView.values().filter { v ->
        when (v) {
            MoneyView.INVESTMENTS -> sources?.any { s -> s.accounts.any { it.kind == "investment" } } == true
            MoneyView.MEALS -> sources?.any { s -> s.accounts.any { it.kind == "meal" } } == true
            else -> true
        }
    }

    // ---------- writes ----------

    /**
     * Run a write, show its failure as a message, and offer Undo when it
     * answers an `undo_id` ([undoText] is the snackbar's line). Refreshes after.
     */
    fun write(failure: String, undoText: String? = null, block: suspend () -> JSONObject?): kotlinx.coroutines.Job = ctx.scope.launch {
        try {
            val answer = block()
            val undoId = answer?.optString("undo_id")?.ifBlank { null }
            if (undoText != null && undoId != null) {
                ctx.messages.postUndoable(undoText, onUndo = { undo(undoId) })
            }
            refresh()
        } catch (e: Exception) {
            if (e is kotlinx.coroutines.CancellationException) throw e
            ctx.messages.post("$failure: ${(e as? MoneyApiError)?.message ?: describeError(e)}")
        }
    }

    fun undo(undoId: String): kotlinx.coroutines.Job = write("Couldn't undo") { api.undo(undoId); null }

    /** The account changed (Settings): forget the previous user's money. */
    fun forget() {
        _overview.value = null
        _sources.value = null
        _categories.value = null
        _detail.value = null
        _filter.value = TxnFilter()
        changesCursor = null
        dash.forget()
        ledger.forget()
    }
}
