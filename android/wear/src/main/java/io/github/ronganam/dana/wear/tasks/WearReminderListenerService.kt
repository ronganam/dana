package io.github.ronganam.dana.wear.tasks

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import io.github.ronganam.dana.wear.R
import com.google.android.gms.wearable.Wearable
import org.json.JSONObject

class WearReminderListenerService : com.google.android.gms.wearable.WearableListenerService() {

    override fun onMessageReceived(messageEvent: com.google.android.gms.wearable.MessageEvent) {
        super.onMessageReceived(messageEvent)
        if (messageEvent.path == WearReminderSync.PATH_DISMISS) {
            val taskId = JSONObject(String(messageEvent.data)).optString("task_id")
            WearReminderSync.closeLocally(this, taskId)
            return
        }
        if (messageEvent.path == WearReminderSync.PATH_ALARM) {
            val json = JSONObject(String(messageEvent.data))
            val taskId = json.optString("task_id", "")
            val taskTitle = json.optString("task_title", "Reminder")
            val priority = json.optString("priority", "medium")
            val triggerType = json.optString("trigger_type", "time")
            // The phone's Home Assistant zones, for the alarm's place snoozes.
            json.optJSONArray("zones")?.let { zones ->
                getSharedPreferences(WearReminderSync.PREFS, MODE_PRIVATE).edit()
                    .putString(WearReminderSync.KEY_ZONES, zones.toString()).apply()
            }

            // Launch full-screen alarm
            val intent = Intent(this, WearReminderActivity::class.java).apply {
                putExtra("task_id", taskId)
                putExtra("task_title", taskTitle)
                putExtra("priority", priority)
                putExtra("trigger_type", triggerType)
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            }
            // Android 10+ may block starting an activity from the
            // background; the full-screen notification below covers that.
            try {
                startActivity(intent)
            } catch (e: Exception) {
                android.util.Log.w("WearReminderListener", "Couldn't open the alarm directly", e)
            }

            // Also post a notification with action buttons
            showReminderNotification(taskId, taskTitle, priority)
        }
    }

    private fun showReminderNotification(taskId: String, taskTitle: String, priority: String) {
        val channelId = "wear_reminders"
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                channelId,
                "Task Reminders",
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "Urgent task reminders forwarded from phone"
                enableVibration(true)
            }
            nm.createNotificationChannel(channel)
        }

        val alarmIntent = Intent(this, WearReminderActivity::class.java).apply {
            putExtra("task_id", taskId)
            putExtra("task_title", taskTitle)
            putExtra("priority", priority)
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val alarmPending = PendingIntent.getActivity(
            this, taskId.hashCode(), alarmIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        // Complete action
        val completeIntent = Intent(this, WearTaskActionReceiver::class.java).apply {
            putExtra("action", "complete")
            putExtra("task_id", taskId)
        }
        val completePending = PendingIntent.getBroadcast(
            this, taskId.hashCode() + 1, completeIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        // Snooze 1hr action
        val snoozeIntent = Intent(this, WearTaskActionReceiver::class.java).apply {
            putExtra("action", "snooze")
            putExtra("task_id", taskId)
            putExtra("snooze_minutes", 60)
        }
        val snoozePending = PendingIntent.getBroadcast(
            this, taskId.hashCode() + 2, snoozeIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val notification = NotificationCompat.Builder(this, channelId)
            .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
            .setContentTitle(taskTitle)
            .setContentText("Task Reminder")
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setFullScreenIntent(alarmPending, true)
            .setContentIntent(alarmPending)
            .addAction(0, "Done", completePending)
            .addAction(0, "Snooze 1hr", snoozePending)
            .setAutoCancel(false)
            .build()

        try {
            nm.notify(WearReminderSync.notificationId(taskId), notification)
        } catch (e: SecurityException) {
            android.util.Log.w("WearReminderListener", "Notification permission missing", e)
        }
    }
}
