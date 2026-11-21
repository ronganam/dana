package io.github.ronganam.dana.data.wear

import io.github.ronganam.dana.protocol.WearPaths
import android.content.Context
import android.util.Log
import com.google.android.gms.wearable.Wearable
import io.github.ronganam.dana.protocol.DanaSnapshot
import io.github.ronganam.dana.protocol.WearCard

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
        sendToWatches(context, WearPaths.CARD, WearCard(kind, title, subtitle, lines.take(6), ref).encode())
    }

    /** The parts of the phone's widget snapshot the watch shows ([DanaSnapshot.forWatch]). */
    fun sendSnapshot(context: Context, snapshot: DanaSnapshot) = sendToWatches(context, WearPaths.SNAPSHOT, snapshot.forWatch().encode())

    fun clear(context: Context, kind: String) = sendToWatches(context, WearPaths.CARD_CLEAR, WearCard(kind = kind).encode())

    private fun sendToWatches(context: Context, path: String, payload: String) {
        if (io.github.ronganam.dana.BuildConfig.KIOSK || io.github.ronganam.dana.BuildConfig.TV) return
        val app = context.applicationContext
        val bytes = payload.toByteArray()
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
