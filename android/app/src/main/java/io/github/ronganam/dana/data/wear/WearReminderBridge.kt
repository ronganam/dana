package io.github.ronganam.dana.data.wear

import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.util.Log
import com.google.android.gms.wearable.Wearable
import io.github.ronganam.dana.data.network.ApiService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeout
import org.json.JSONArray
import org.json.JSONObject

/**
 * Keeps the phone and watch reminder alarms in step.
 *
 * Reminders reach the phone as a Home Assistant broadcast
 * ([io.github.ronganam.dana.ReminderReceiver]). The watch has its own full-screen
 * alarm, which listens on [PATH_ALARM], but before this nothing sent to it.
 * Handling the alarm on either device (complete, snooze or dismiss) now
 * dismisses it on the other one too.
 */
object WearReminderBridge {
    private const val TAG = "WearReminderBridge"

    /** Phone -> watch: ring the alarm. */
    const val PATH_ALARM = "/wear/reminder_alarm"
    /** Phone -> watch: the alarm was handled on the phone. */
    const val PATH_DISMISS_ON_WATCH = "/wear/reminder_dismiss"
    /** Watch -> phone: the alarm was handled on the watch. */
    const val PATH_DISMISSED_ON_WATCH = "/reminder_dismiss"

    /** Local broadcast that closes [io.github.ronganam.dana.ReminderActivity]. */
    const val ACTION_CLOSE_REMINDER = "io.github.ronganam.dana.CLOSE_REMINDER"
    const val EXTRA_TASK_ID = "task_id"

    private const val PREFS = "DanaPrefs"
    private const val KEY_ZONES = "zones_cache"

    fun forwardAlarm(context: Context, taskId: String, title: String, priority: String, triggerType: String) {
        val app = context.applicationContext
        // Send right away with the zones we know, so the watch never rings late;
        // refresh the cache for next time in the background.
        val payload = JSONObject().apply {
            put("task_id", taskId)
            put("task_title", title)
            put("priority", priority)
            put("trigger_type", triggerType)
            put("zones", JSONArray(cachedZones(app)))
        }
        sendToWatches(app, PATH_ALARM, payload)
        refreshZones(app)
    }

    /** Home Assistant zones for place snoozes (last known; before the first fetch only Home Assistant's own "Home"). */
    fun cachedZones(context: Context): List<String> {
        val raw = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_ZONES, null)
        return raw?.let { runCatching { JSONArray(it).let { a -> List(a.length()) { i -> a.getString(i) } } }.getOrNull() }
            ?.takeIf { it.isNotEmpty() }
            ?: listOf("Home")
    }

    fun refreshZones(context: Context) {
        val app = context.applicationContext
        CoroutineScope(Dispatchers.IO).launch {
            runCatching {
                val zones = withTimeout(8000) { ApiService.fromPrefs(app).getZones() }.map { it.name }
                if (zones.isNotEmpty()) {
                    app.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                        .putString(KEY_ZONES, JSONArray(zones).toString()).apply()
                }
            }.onFailure { Log.d(TAG, "Zone refresh failed: ${it.message}") }
        }
    }

    fun dismissOnWatch(context: Context, taskId: String) {
        if (taskId.isEmpty()) return
        sendToWatches(context, PATH_DISMISS_ON_WATCH, JSONObject().put("task_id", taskId))
    }

    /** The watch handled the alarm: stop ringing here and clear the notification. */
    fun dismissOnPhone(context: Context, taskId: String) {
        if (taskId.isEmpty()) return
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.cancel(taskId.hashCode())
        context.sendBroadcast(
            Intent(ACTION_CLOSE_REMINDER).setPackage(context.packageName).putExtra(EXTRA_TASK_ID, taskId)
        )
    }

    private fun sendToWatches(context: Context, path: String, payload: JSONObject) {
        val app = context.applicationContext
        val bytes = payload.toString().toByteArray()
        Wearable.getNodeClient(app).connectedNodes
            .addOnSuccessListener { nodes ->
                if (nodes.isEmpty()) Log.d(TAG, "No watch connected for $path")
                nodes.forEach { node ->
                    Wearable.getMessageClient(app).sendMessage(node.id, path, bytes)
                        .addOnFailureListener { e -> Log.w(TAG, "Couldn't send $path to ${node.displayName}", e) }
                }
            }
            .addOnFailureListener { e -> Log.w(TAG, "Couldn't list watches for $path", e) }
    }
}
