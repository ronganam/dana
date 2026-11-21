package io.github.ronganam.dana

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.app.NotificationCompat
import io.github.ronganam.dana.R

/**
 * A task reminder from the server (`reminder` push event, see
 * [io.github.ronganam.dana.push.PhoneEvents]): rings the watch, posts the alarm
 * notification and opens [ReminderActivity] full screen.
 */
object ReminderReceiver {

    const val QUIET_CHANNEL = "reminders_quiet"

    /** The server says the task was completed elsewhere: drop its notification. */
    fun clear(context: Context, taskId: String) {
        (context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).cancel(taskId.hashCode())
    }

    fun show(context: Context, taskId: String, decodedTitle: String, priority: String, triggerType: String) {
        Log.i("ReminderReceiver", "Triggering reminder for Task ID: $taskId, Title: $decodedTitle")

        // Ring the watch too; handling it on either device dismisses both.
        io.github.ronganam.dana.data.wear.WearReminderBridge.forwardAlarm(
            context, taskId, decodedTitle, priority, triggerType
        )

        val notificationManager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val channelId = "reminders_channel"

        val channel = NotificationChannel(
            channelId,
            context.getString(R.string.channel_reminders),
            NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = context.getString(R.string.channel_reminders_description)
            enableVibration(true)
            setShowBadge(true)
        }
        notificationManager.createNotificationChannel(channel)

        val fullScreenIntent = Intent(context, ReminderActivity::class.java).apply {
            putExtra("task_id", taskId)
            putExtra("task_title", decodedTitle)
            putExtra("priority", priority)
            putExtra("trigger_type", triggerType)
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }

        val pendingFlags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE

        val fullScreenPendingIntent = PendingIntent.getActivity(
            context,
            taskId.hashCode(),
            fullScreenIntent,
            pendingFlags
        )

        notificationManager.createNotificationChannel(
            NotificationChannel(QUIET_CHANNEL, context.getString(R.string.channel_reminders_quiet), NotificationManager.IMPORTANCE_LOW).apply {
                description = context.getString(R.string.channel_reminders_quiet_description)
            }
        )
        // The reminder screen opens by itself and plays the alarm on its own,
        // so its notification only stays in the shade: no heads-up over it.
        val popping = android.provider.Settings.canDrawOverlays(context)
        fun build(quiet: Boolean) = NotificationCompat.Builder(context, if (quiet) QUIET_CHANNEL else channelId)
            .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
            .setContentTitle(decodedTitle)
            .setContentText("Task Reminder")
            .setPriority(if (quiet) NotificationCompat.PRIORITY_LOW else NotificationCompat.PRIORITY_MAX)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setContentIntent(fullScreenPendingIntent)
            .setAutoCancel(true)
            .apply { if (!quiet) setFullScreenIntent(fullScreenPendingIntent, true) }

        notificationManager.notify(taskId.hashCode(), build(quiet = popping).build())

        try {
            context.startActivity(fullScreenIntent)
        } catch (e: Exception) {
            Log.e("ReminderReceiver", "Failed to start ReminderActivity directly", e)
            if (popping) notificationManager.notify(taskId.hashCode(), build(quiet = false).build())
        }
    }
}
