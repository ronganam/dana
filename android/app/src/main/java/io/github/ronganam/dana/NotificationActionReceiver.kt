package io.github.ronganam.dana

import io.github.ronganam.dana.data.network.ServerConfig
import android.content.Context
import android.util.Log
import io.github.ronganam.dana.data.notifications.DanaNotificationListener
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * The server's `notification_action` push event (see
 * [io.github.ronganam.dana.push.PhoneEvents]): a notification's button was
 * pressed in the web Inbox. The event carries only `request_id`; what to press
 * (and any reply text) is fetched from the server with this app's own token,
 * so the reply text never goes through Google's push service.
 */
object NotificationActionReceiver {
    private const val TAG = "NotifAction"
    private val seen = LinkedHashSet<String>()

    suspend fun handle(context: Context, rid: String) {
        if (!rid.matches(Regex("[A-Za-z0-9_-]{8,64}"))) return
        synchronized(seen) {
            if (!seen.add(rid)) return  // already handled
            if (seen.size > 100) seen.remove(seen.first())
        }
        val api = ServerConfig.load(context).api()
        val req = try {
            api.getNotificationRequest(rid)
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't fetch request $rid", e)
            return
        }
        if (req.optString("status") != "pending") return
        val key = req.optString("key").takeIf { it.isNotBlank() }
        val action = req.optString("action")
        val error = withContext(Dispatchers.Main) {
            if (action == "open") {
                if (DanaNotificationListener.open(context, key)) null else "It's no longer in the notification shade"
            } else {
                DanaNotificationListener.perform(context, key, action, req.optString("text"))
            }
        }
        Log.i(TAG, "Request $rid ($action): ${error ?: "done"}")
        try {
            api.finishNotificationRequest(rid, error == null, error)
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't report request $rid", e)
        }
    }
}
