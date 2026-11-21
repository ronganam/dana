package io.github.ronganam.dana.data.network

import android.content.Context
import io.github.ronganam.dana.data.money.MoneyFormat
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Who the app's token belongs to (`GET /api/me`): id, name, the add-ons
 * they have connected (`addons`), their languages and their currency (ISO
 * 4217, which [MoneyFormat] writes amounts in). Kept in `DanaPrefs` so places
 * that depend on an add-on (Shopping) show the right thing at start and
 * offline. [addons] follows every save, so a place can appear or go as soon
 * as `/api/me` (or a 403 `not_connected`) says so.
 */
object Me {
    data class Info(
        val id: String,
        val name: String,
        val addons: Set<String>,
        val languages: List<String> = emptyList(),
        val currency: String? = null
    ) {
        fun has(addon: String): Boolean = addon in addons
    }

    private const val KEY_ID = "me_id"
    private const val KEY_NAME = "me_name"
    private const val KEY_ADDONS = "me_addons"
    private const val KEY_LANGUAGES = "me_languages"
    private const val KEY_CURRENCY = "me_currency"

    private var live: MutableStateFlow<Set<String>>? = null

    /** The connected add-ons as they change (starts from the cached answer). */
    @Synchronized
    fun addons(context: Context): StateFlow<Set<String>> =
        (live ?: MutableStateFlow(cached(context)?.addons ?: emptySet()).also { live = it }).asStateFlow()

    fun cached(context: Context): Info? {
        val p = ServerConfig.prefs(context)
        val id = p.getString(KEY_ID, null) ?: return null
        fun list(key: String) = p.getString(key, "").orEmpty().split(',').filter { it.isNotBlank() }
        return Info(id, p.getString(KEY_NAME, "").orEmpty(), list(KEY_ADDONS).toSet(), list(KEY_LANGUAGES), p.getString(KEY_CURRENCY, null))
            .also { MoneyFormat.use(it.currency) }
    }

    fun parse(answer: MeAnswer): Info? {
        val id = answer.id ?: return null
        return Info(id, answer.name, answer.addons.toSet(), answer.languages, answer.currency)
    }

    /** Ask the server and remember the answer; null (keep the cached one) when it can't be read. */
    suspend fun fetch(context: Context, api: ApiService): Info? = try {
        parse(api.devices.me())?.also { save(context, it) }
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        android.util.Log.w("Me", "Couldn't read /api/me: ${e.message}")
        null
    }

    fun save(context: Context, info: Info) {
        ServerConfig.prefs(context).edit()
            .putString(KEY_ID, info.id)
            .putString(KEY_NAME, info.name)
            .putString(KEY_ADDONS, info.addons.sorted().joinToString(","))
            .putString(KEY_LANGUAGES, info.languages.joinToString(","))
            .putString(KEY_CURRENCY, info.currency)
            .apply()
        MoneyFormat.use(info.currency)
        live?.value = info.addons
    }

    /** The server said the user lacks [addon] (403 `not_connected`): forget it until the next `/api/me`. */
    fun drop(context: Context, addon: String) {
        val info = cached(context) ?: return
        if (addon in info.addons) save(context, info.copy(addons = info.addons - addon))
    }

    /**
     * A 403 answer: when it is `not_connected`, forget the add-on it names
     * (`addon`, else [fallback]) and say so.
     */
    fun dropIfNotConnected(context: Context, result: HttpResult, fallback: String): Boolean {
        if (result.code != 403) return false
        val json = result.json()
        if (json.text("error") != "not_connected") return false
        drop(context, json.text("addon") ?: fallback)
        return true
    }
}
