package io.github.ronganam.dana.data.wear

import io.github.ronganam.dana.protocol.ReminderAlarm
import io.github.ronganam.dana.protocol.ReminderHandled
import io.github.ronganam.dana.protocol.WearPaths
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.util.Log
import com.google.android.gms.wearable.Wearable
import io.github.ronganam.dana.data.network.Zones
import org.json.JSONObject

/**
 * Keeps the phone and watch reminder alarms in step.
 *
 * Reminders reach the phone as a Home Assistant broadcast
 * ([io.github.ronganam.dana.ReminderReceiver]). The watch has its own full-screen
 * alarm, which listens on [WearPaths.REMINDER_ALARM], but before this nothing sent to it.
 * Handling the alarm on either device (complete, snooze or dismiss) now
 * dismisses it on the other one too.
 */
object WearReminderBridge {
    private const val TAG = "WearReminderBridge"

    /** Local broadcast that closes [io.github.ronganam.dana.ReminderActivity]. */
    const val ACTION_CLOSE_REMINDER = "io.github.ronganam.dana.CLOSE_REMINDER"
    const val EXTRA_TASK_ID = "task_id"

    fun forwardAlarm(context: Context, taskId: String, title: String, priority: String, triggerType: String) {
        val app = context.applicationContext
        // Send right away with the zones we know, so the watch never rings late;
        // refresh the cache for next time in the background.
        val alarm = ReminderAlarm(taskId, title, priority, triggerType, Zones.cached(app))
        sendToWatches(app, WearPaths.REMINDER_ALARM, alarm.toJson())
        Zones.refresh(app)
    }

    fun dismissOnWatch(context: Context, taskId: String) {
        if (taskId.isEmpty()) return
        sendToWatches(context, WearPaths.REMINDER_HANDLED_ON_PHONE, ReminderHandled(taskId).toJson())
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
