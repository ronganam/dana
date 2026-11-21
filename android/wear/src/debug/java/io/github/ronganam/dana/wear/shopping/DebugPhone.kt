package io.github.ronganam.dana.wear.shopping

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.util.Base64
import android.util.Log
import io.github.ronganam.dana.wear.cards.DanaSnapshot
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

/**
 * Debug builds only: a stand-in phone for the watch's Shopping screen when no
 * phone is paired (emulators). Off unless switched on with [DebugShoppingReceiver]:
 *
 *   adb -s <watch> shell am broadcast -n io.github.ronganam.dana/.wear.shopping.DebugShoppingReceiver \
 *       --es phone ok|unreachable|refuse|off  --es snapshot_b64 <base64 JSON>  --es say_b64 <base64 text>
 *
 * `ok` answers like the phone (`{ok, queued: false, ref}` after 300 ms) and
 * sends a new snapshot 1.5 s later with the change applied; `unreachable`
 * refuses every send; `refuse` answers `{ok: false, error}`. `snapshot_b64`
 * stores a snapshot as if the phone had sent it; `say_b64` adds text as if it
 * had been spoken.
 */
object DebugPhone {
    private const val PREFS = "debug_phone"
    private val main = Handler(Looper.getMainLooper())
    /** Items ticked through the fake phone, to put back on an untick. */
    private val removed = HashMap<String, Pair<JSONObject, Int>>()

    fun mode(context: Context): String =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("mode", "off") ?: "off"

    fun setMode(context: Context, mode: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString("mode", mode).apply()
    }

    fun link(context: Context): ShoppingLink? = when (mode(context)) {
        "ok" -> ShoppingLink { c, payload -> answerLater(c, payload, ok = true); true }
        "refuse" -> ShoppingLink { c, payload -> answerLater(c, payload, ok = false); true }
        "unreachable" -> ShoppingLink { _, _ -> false }
        else -> null
    }

    private fun answerLater(context: Context, payload: JSONObject, ok: Boolean) {
        val app = context.applicationContext
        main.postDelayed({
            val answer = JSONObject().put("ok", ok).put("ref", payload.optString("ref"))
            if (ok) answer.put("queued", false) else answer.put("error", "This list is view only.")
            WatchShopping.onAnswer(answer)
            if (ok) main.postDelayed({ DanaSnapshot.save(app, applied(DanaSnapshot.load(app), payload)) }, 1_500)
        }, 300)
    }

    /** [snapshot] with the request applied, as the phone's next snapshot would have it. */
    private fun applied(snapshot: JSONObject, payload: JSONObject): JSONObject {
        val items = snapshot.optJSONArray("shopping") ?: JSONArray()
        val list = (0 until items.length()).mapNotNull { items.optJSONObject(it) }.toMutableList()
        when (payload.optString("action")) {
            "check" -> {
                val i = list.indexOfFirst { it.optString("id") == payload.optString("id") }
                if (i >= 0) removed[payload.optString("id")] = list.removeAt(i) to i
            }
            "uncheck" -> removed.remove(payload.optString("id"))?.let { (o, i) -> list.add(i.coerceIn(0, list.size), o) }
            "add" -> payload.optString("text").split(',', '\n').map { it.trim() }.filter { it.isNotEmpty() }.forEach {
                list.add(JSONObject().put("id", UUID.randomUUID().toString().take(12)).put("name", it).put("quantity", "")
                    .put("aisle", "Other").put("aisle_key", "other"))
            }
        }
        val count = snapshot.optInt("shopping_count", items.length()) + (list.size - items.length())
        return snapshot.put("shopping", JSONArray(list)).put("shopping_count", count.coerceAtLeast(0))
            .put("shopping_version", snapshot.optLong("shopping_version") + 1)
    }
}

/** Debug builds only: drives [DebugPhone] from adb (see there). */
class DebugShoppingReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val app = context.applicationContext
        intent.getStringExtra("phone")?.let { DebugPhone.setMode(app, it) }
        intent.getStringExtra("snapshot_b64")?.let { b64 ->
            val json = runCatching { JSONObject(String(Base64.decode(b64, Base64.DEFAULT))) }.getOrNull()
            if (json == null) Log.w("DebugPhone", "Bad snapshot") else DanaSnapshot.save(app, json)
        }
        intent.getStringExtra("say_b64")?.let { WatchShopping.add(app, String(Base64.decode(it, Base64.DEFAULT))) }
        Log.i("DebugPhone", "phone=${DebugPhone.mode(app)}")
    }
}
