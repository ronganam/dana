package io.github.ronganam.dana.ui.viewmodels

import io.github.ronganam.dana.data.money.MoneyApi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import org.json.JSONObject

/**
 * State of the dashboard views (Overview, Spending, Cash flow, Investments,
 * Meal card): their own payloads, loaded on demand and again when
 * [MoneyController.revision] moves.
 *
 * Each payload is kept with the request it answered ([data]: name →
 * [Loaded]); a view reads one with [got], which returns it only while that
 * request is still the one wanted, so a month switch (or a new horizon)
 * shows skeletons, never the previous month's numbers, while a refresh of
 * the same request keeps the old answer on screen until the new one lands
 * (like the web's `got(name)`).
 */
class MoneyDashState(val money: MoneyController) {

    data class Loaded(val request: String, val body: JSONObject)

    private val _data = MutableStateFlow<Map<String, Loaded>>(emptyMap())
    val data: StateFlow<Map<String, Loaded>> = _data.asStateFlow()

    private val _horizon = MutableStateFlow(90)
    /** Days of the bank balance chart's forecast (90 / 180 / 365); the past shown is as long. */
    val horizon: StateFlow<Int> = _horizon.asStateFlow()

    private val _invDays = MutableStateFlow(365)
    /** The portfolio value chart's range (30 / 90 / 365 days); the payload holds a year. */
    val invDays: StateFlow<Int> = _invDays.asStateFlow()

    private val _everything = MutableStateFlow(false)
    /** Spending's 12 months: false = Everyday (one-offs left out), true = Everything. */
    val everything: StateFlow<Boolean> = _everything.asStateFlow()

    private val _openBills = MutableStateFlow<Set<String>>(emptySet())
    /** Coming up's card bills opened into their parts (keys from [billKey]). */
    val openBills: StateFlow<Set<String>> = _openBills.asStateFlow()

    private val _expanded = MutableStateFlow<Set<String>>(emptySet())
    /** Spending's groups opened into their categories. */
    val expanded: StateFlow<Set<String>> = _expanded.asStateFlow()

    fun setHorizon(days: Int) { _horizon.value = days }
    fun setInvDays(days: Int) { _invDays.value = days }
    fun setEverything(on: Boolean) { _everything.value = on }
    fun toggleBill(key: String) = _openBills.update { if (key in it) it - key else it + key }
    fun toggleGroup(key: String) = _expanded.update { if (key in it) it - key else it + key }

    /** What each view loads, by name. */
    private fun needs(view: MoneyView, month: String, current: Boolean): List<String> = when (view) {
        MoneyView.OVERVIEW -> listOf("insights", "pace", "cashflow") + if (current) listOf("balance") else emptyList()
        MoneyView.SPENDING -> listOf("trends", "months", "merchants")
        MoneyView.CASHFLOW -> listOf("cashflow", "balance", "recurring")
        MoneyView.INVESTMENTS -> listOf("portfolio")
        MoneyView.MEALS -> listOf("meals")
        else -> emptyList()
    }

    /** The request of a payload for the month (and horizon): path + query, as [MoneyApi.get] takes them. */
    fun request(name: String, month: String): Pair<String, Map<String, Any?>> = when (name) {
        "insights" -> "/insights" to mapOf("month" to month)
        "pace" -> "/pace" to mapOf("month" to month)
        "balance" -> "/balance" to mapOf("days" to _horizon.value)
        "cashflow" -> "/cashflow" to mapOf("n" to 12, "end" to month)
        "trends" -> "/trends" to mapOf("month" to month)
        "months" -> "/months" to mapOf("n" to 12, "end" to month)
        "merchants" -> "/merchants" to mapOf("period" to month, "limit" to 12)
        "recurring" -> "/recurring" to emptyMap()
        "portfolio" -> "/portfolio" to mapOf("days" to 365, "trades" to 50)
        "meals" -> "/meals" to mapOf("months" to 12)
        else -> error("unknown money payload $name")
    }

    private fun key(name: String, month: String): String = request(name, month).let { (p, q) -> p + MoneyApi.qs(q) }

    /** A payload for [month] when it answers the request wanted now, else null (show a skeleton). */
    fun got(data: Map<String, Loaded>, name: String, month: String): JSONObject? =
        data[name]?.takeIf { it.request == key(name, month) }?.body

    /**
     * Load what [view] shows for [month] (a LaunchedEffect keyed on view,
     * month, revision and horizon). Failures are logged, not shown: the
     * shared payloads' error covers an unreachable server.
     */
    suspend fun load(view: MoneyView, month: String) {
        val current = month == io.github.ronganam.dana.data.money.MoneyText.thisMonth()
        val names = needs(view, month, current)
        coroutineScope {
            names.map { name ->
                async {
                    val (path, query) = request(name, month)
                    val want = path + MoneyApi.qs(query)
                    try {
                        val body = money.api.get(path, query)
                        if (key(name, money.month.value) == want) _data.update { it + (name to Loaded(want, body)) }
                    } catch (e: Exception) {
                        if (e is CancellationException) throw e
                        android.util.Log.w("Money", "Couldn't load $name: ${e.message}")
                    }
                }
            }.awaitAll()
        }
    }

    /** The account changed: drop everything loaded. */
    fun forget() {
        _data.value = emptyMap()
        _openBills.value = emptySet()
        _expanded.value = emptySet()
    }

    companion object {
        /** A card bill's key in [openBills]. */
        fun billKey(date: String, accountId: String?, label: String) = "$date|${accountId.orEmpty()}|$label"
    }
}
