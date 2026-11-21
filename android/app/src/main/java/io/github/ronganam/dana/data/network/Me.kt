package io.github.ronganam.dana.data.network

import android.content.Context
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONObject

/**
 * Who the app's token belongs to (`GET /api/me`): id, name and the
 * integrations they have connected (`kinds`). Kept in `DanaPrefs` (three
 * small values) so places that depend on a kind (Shopping) show the right
 * thing at start and offline. [kinds] follows every save, so a place can
 * appear or go as soon as `/api/me` (or a 403 `not_connected`) says so.
 */
object Me {
    data class Info(val id: String, val name: String, val kinds: Set<String>) {
        fun has(kind: String): Boolean = kind in kinds
    }

    private const val PREFS = "DanaPrefs"
    private const val KEY_ID = "me_id"
    private const val KEY_NAME = "me_name"
    private const val KEY_KINDS = "me_kinds"

    private var live: MutableStateFlow<Set<String>>? = null

    /** The connected kinds as they change (starts from the cached answer). */
    @Synchronized
    fun kinds(context: Context): StateFlow<Set<String>> =
        (live ?: MutableStateFlow(cached(context)?.kinds ?: emptySet()).also { live = it }).asStateFlow()

    fun cached(context: Context): Info? {
        val p = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val id = p.getString(KEY_ID, null) ?: return null
        val kinds = p.getString(KEY_KINDS, "").orEmpty().split(',').filter { it.isNotBlank() }.toSet()
        return Info(id, p.getString(KEY_NAME, "").orEmpty(), kinds)
    }

    fun parse(o: JSONObject): Info? {
        val id = o.optString("id")
        if (id.isEmpty()) return null
        val a = o.optJSONArray("kinds")
        val kinds = if (a == null) emptySet() else (0 until a.length()).map { a.optString(it) }.filter { it.isNotBlank() }.toSet()
        return Info(id, o.optString("name"), kinds)
    }

    /** Ask the server and remember the answer; null (keep the cached one) when it can't be read. */
    suspend fun fetch(context: Context, api: ApiService): Info? = try {
        parse(api.getMe())?.also { save(context, it) }
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        android.util.Log.w("Me", "Couldn't read /api/me: ${e.message}")
        null
    }

    fun save(context: Context, info: Info) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString(KEY_ID, info.id)
            .putString(KEY_NAME, info.name)
            .putString(KEY_KINDS, info.kinds.sorted().joinToString(","))
            .apply()
        live?.value = info.kinds
    }

    /** The server said the user lacks [kind] (403 `not_connected`): forget it until the next `/api/me`. */
    fun drop(context: Context, kind: String) {
        val info = cached(context) ?: return
        if (kind in info.kinds) save(context, info.copy(kinds = info.kinds - kind))
    }
}
