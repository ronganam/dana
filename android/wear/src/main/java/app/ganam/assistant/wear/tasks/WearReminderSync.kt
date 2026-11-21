package app.ganam.assistant.wear.tasks

import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.util.Log
import com.google.android.gms.wearable.Wearable
import org.json.JSONObject

/**
 * Watch half of the phone/watch reminder sync (phone side:
 * `WearReminderBridge`). Handling an alarm here dismisses it on the phone,
 * and the phone dismisses ours when it is handled there.
 */
object WearReminderSync {
    const val PATH_ALARM = "/wear/reminder_alarm"
    const val PATH_DISMISS = "/wear/reminder_dismiss"
    private const val PATH_HANDLED_ON_WATCH = "/reminder_dismiss"

    /** Local broadcast that closes [WearReminderActivity]. */
    const val ACTION_CLOSE = "app.ganam.assistant.wear.CLOSE_REMINDER"
    const val EXTRA_TASK_ID = "task_id"

    /** Zones forwarded with the last alarm (JSON array of names). */
    const val PREFS = "wear_reminders"
    const val KEY_ZONES = "zones"

    fun zones(context: Context): List<String> {
        val raw = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_ZONES, null)
        return raw?.let { runCatching { org.json.JSONArray(it).let { a -> List(a.length()) { i -> a.getString(i) } } }.getOrNull() }
            ?.takeIf { it.isNotEmpty() }
            ?: listOf("Home", "Work")
    }

    fun notificationId(taskId: String) = taskId.hashCode()

    /** The alarm was handled on the watch: clear it here and on the phone. */
    fun handledOnWatch(context: Context, taskId: String) {
        if (taskId.isEmpty()) return
        cancelNotification(context, taskId)
        sendToPhone(context, PATH_HANDLED_ON_WATCH, JSONObject().put("task_id", taskId))
    }

    /** The phone handled the alarm: stop ringing here. */
    fun closeLocally(context: Context, taskId: String) {
        cancelNotification(context, taskId)
        context.sendBroadcast(Intent(ACTION_CLOSE).setPackage(context.packageName).putExtra(EXTRA_TASK_ID, taskId))
    }

    private fun cancelNotification(context: Context, taskId: String) {
        (context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).cancel(notificationId(taskId))
    }

    fun sendToPhone(context: Context, path: String, payload: JSONObject) {
        val app = context.applicationContext
        Wearable.getNodeClient(app).connectedNodes
            .addOnSuccessListener { nodes ->
                val phoneId = nodes.firstOrNull()?.id ?: return@addOnSuccessListener
                Wearable.getMessageClient(app).sendMessage(phoneId, path, payload.toString().toByteArray())
                    .addOnFailureListener { e -> Log.w("WearReminderSync", "Couldn't send $path", e) }
            }
    }
}
