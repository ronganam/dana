package io.github.ronganam.dana.data.wear

import io.github.ronganam.dana.protocol.WearPaths
import android.content.Context
import android.util.Log
import com.google.android.gms.wearable.Wearable
import org.json.JSONArray
import org.json.JSONObject

/**
 * Phone → watch cards: the caller card, a person before a meeting, the
 * morning briefing. The watch shows each as a notification (one per [kind],
 * replaced by the next) and keeps the briefing for its Today tile.
 *
 *   /wear/dana_card        {kind, title, subtitle, lines[], ref}
 *   /wear/dana_card_clear  {kind}
 *   /wear/dana_snapshot    the widget snapshot, trimmed (Today / Waiting tiles,
 *                          Inbox and Shopping complications, the shopping list:
 *                          `data/shopping/ShoppingSnapshot`)
 *
 * Watch → phone: /suggestion_action {id, action: accept|dismiss} and
 * /shopping_action {action: check|uncheck, id, list_id} | {action: add, text, list_id},
 * handled in [WearMessageListenerService].
 */
object WearCards {
    private const val TAG = "WearCards"

    fun send(context: Context, kind: String, title: String, subtitle: String, lines: List<String>, ref: String = "") {
        val payload = JSONObject().put("kind", kind).put("title", title).put("subtitle", subtitle)
            .put("lines", JSONArray(lines.take(6))).put("ref", ref)
        sendToWatches(context, WearPaths.CARD, payload)
    }


    /** The parts of the phone's widget snapshot the watch shows (kept well under the message size limit). */
    fun sendSnapshot(context: Context, snapshot: JSONObject) {
        val o = JSONObject()
        for (k in listOf("briefing_headline", "briefing_date", "suggestion_count", "inbox_needs", "inbox_active", "updated_at") +
            io.github.ronganam.dana.data.shopping.ShoppingSnapshot.KEYS.filter { it != "shopping" }) {
            if (snapshot.has(k)) o.put(k, snapshot.get(k))
        }
        fun firstN(key: String, n: Int) = snapshot.optJSONArray(key)?.let { a -> JSONArray((0 until minOf(n, a.length())).map { a.get(it) }) }
        firstN("briefing_points", 4)?.let { o.put("briefing_points", it) }
        firstN("events", 3)?.let { o.put("events", it) }
        firstN("tasks", 4)?.let { o.put("tasks", it) }
        firstN("suggestions", 3)?.let { o.put("suggestions", it) }
        firstN("shopping", 40)?.let { o.put("shopping", it) }
        sendToWatches(context, WearPaths.SNAPSHOT, o)
    }

    fun clear(context: Context, kind: String) = sendToWatches(context, WearPaths.CARD_CLEAR, JSONObject().put("kind", kind))

    private fun sendToWatches(context: Context, path: String, payload: JSONObject) {
        if (io.github.ronganam.dana.BuildConfig.KIOSK || io.github.ronganam.dana.BuildConfig.TV) return
        val app = context.applicationContext
        val bytes = payload.toString().toByteArray()
        Wearable.getNodeClient(app).connectedNodes
            .addOnSuccessListener { nodes ->
                nodes.forEach { node ->
                    Wearable.getMessageClient(app).sendMessage(node.id, path, bytes)
                        .addOnFailureListener { e -> Log.d(TAG, "Couldn't send $path to ${node.displayName}: ${e.message}") }
                }
            }
            .addOnFailureListener { e -> Log.d(TAG, "Couldn't list watches for $path: ${e.message}") }
    }
}
