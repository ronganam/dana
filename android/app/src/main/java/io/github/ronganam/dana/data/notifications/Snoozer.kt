package io.github.ronganam.dana.data.notifications

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import io.github.ronganam.dana.EXTRA_OPEN_INBOX
import io.github.ronganam.dana.MainActivity
import io.github.ronganam.dana.data.network.ApiService
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * Snoozing phone notifications, shared by the Inbox and the alert screen.
 *
 * The server hides them until then; on the phone, ones still in the shade get
 * Android's own snooze (they leave the shade and come back by themselves).
 * Ones no longer in the shade come back as a Dana notification instead
 * ([SnoozeReturnWorker]).
 */
object Snoozer {

    /** What to snooze. */
    data class Target(val id: String, val key: String?, val appName: String, val title: String?)

    data class Option(val label: String, val untilMillis: Long)

    /** 15 min / 1 h / 3 h / this evening (before 19:00) / tomorrow morning. */
    fun options(now: ZonedDateTime = ZonedDateTime.now()): List<Option> = buildList {
        add(Option("15 min", now.plusMinutes(15).toEpochMillis()))
        add(Option("1 hour", now.plusHours(1).toEpochMillis()))
        add(Option("3 hours", now.plusHours(3).toEpochMillis()))
        val evening = now.truncatedTo(ChronoUnit.DAYS).withHour(20)
        if (now.isBefore(evening.minusHours(1))) add(Option("This evening", evening.toEpochMillis()))
        val morning = now.truncatedTo(ChronoUnit.DAYS).plusDays(1).withHour(8)
        add(Option("Tomorrow 08:00", morning.toEpochMillis()))
    }

    private fun ZonedDateTime.toEpochMillis() = toInstant().toEpochMilli()

    /** "until 20:00" / "until Wed 08:00". */
    fun untilLabel(untilMillis: Long, now: ZonedDateTime = ZonedDateTime.now()): String {
        val t = java.time.Instant.ofEpochMilli(untilMillis).atZone(now.zone)
        val time = t.format(DateTimeFormatter.ofPattern("HH:mm"))
        return when (ChronoUnit.DAYS.between(now.toLocalDate(), t.toLocalDate())) {
            0L -> "until $time"
            1L -> "until tomorrow $time"
            else -> "until ${t.format(DateTimeFormatter.ofPattern("EEE", Locale.getDefault()))} $time"
        }
    }

    /** Server first (throws when it fails), then the shade and return reminders. */
    suspend fun snooze(context: Context, api: ApiService, targets: List<Target>, untilMillis: Long) {
        if (targets.isEmpty()) return
        val keys = api.notifications.snooze(targets.map { it.id }, untilMillis)
        applyOnPhone(context, targets, keys, untilMillis)
    }

    /**
     * The phone's half of a snooze the server already did (also for Now's
     * `snooze` action): Android's own snooze for [keys] still in the shade, a
     * return reminder for the rest.
     */
    fun applyOnPhone(context: Context, targets: List<Target>, keys: Map<String, Long>, untilMillis: Long) {
        val inShade = DanaNotificationListener.snooze(keys.ifEmpty {
            targets.mapNotNull { t -> t.key?.let { it to untilMillis } }.toMap()
        })
        targets.filter { it.key == null || it.key !in inShade }.forEach { SnoozeReturnWorker.schedule(context, it, untilMillis) }
    }

    /** Undo of a server-side snooze (already reversed on the server): back in the shade, no return reminder. */
    fun undoOnPhone(context: Context, ids: List<String>, keys: Collection<String>) {
        ids.forEach { SnoozeReturnWorker.cancel(context, it) }
        DanaNotificationListener.unsnooze(keys)
    }

    /** Wake them now (undo / "Unsnooze"). Ones Android snoozed return to the shade at their time. */
    suspend fun wake(context: Context, api: ApiService, ids: List<String>, keys: List<String> = emptyList()) {
        if (ids.isEmpty()) return
        api.notifications.snooze(ids, null)
        ids.forEach { SnoozeReturnWorker.cancel(context, it) }
        DanaNotificationListener.unsnooze(keys)
    }
}

/**
 * When a snooze ends for a notification that wasn't in the shade any more,
 * post a Dana notification so it doesn't come back silently.
 */
class SnoozeReturnWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val ctx = applicationContext
        val key = inputData.getString(KEY_KEY)
        // Android already put it back in the shade.
        if (key != null && key in DanaNotificationListener.activeKeys()) return Result.success()
        val id = inputData.getString(KEY_ID) ?: return Result.success()
        val app = inputData.getString(KEY_APP) ?: "Notification"
        val title = inputData.getString(KEY_TITLE)

        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, "Snoozed notifications", NotificationManager.IMPORTANCE_DEFAULT).apply {
                    description = "A notification you snoozed in Dana is back"
                }
            )
        }
        val open = PendingIntent.getActivity(
            ctx, id.hashCode(),
            Intent(ctx, MainActivity::class.java).apply {
                putExtra(EXTRA_OPEN_INBOX, true)
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val n = NotificationCompat.Builder(ctx, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_popup_reminder)
            .setContentTitle("⏰ $app")
            .setContentText(title ?: "Snoozed notification is back")
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        try {
            nm.notify(id.hashCode(), n)
        } catch (e: SecurityException) {
            // Notification permission revoked: the Inbox still shows it.
        }
        return Result.success()
    }

    companion object {
        private const val CHANNEL = "snoozed_notifications"
        private const val KEY_ID = "id"
        private const val KEY_KEY = "key"
        private const val KEY_APP = "app"
        private const val KEY_TITLE = "title"

        private fun name(id: String) = "snooze-return-$id"

        fun schedule(context: Context, t: Snoozer.Target, untilMillis: Long) {
            val delay = (untilMillis - System.currentTimeMillis()).coerceAtLeast(0)
            val request = OneTimeWorkRequestBuilder<SnoozeReturnWorker>()
                .setInitialDelay(delay, TimeUnit.MILLISECONDS)
                .setInputData(workDataOf(KEY_ID to t.id, KEY_KEY to t.key, KEY_APP to t.appName, KEY_TITLE to t.title))
                .build()
            WorkManager.getInstance(context.applicationContext)
                .enqueueUniqueWork(name(t.id), ExistingWorkPolicy.REPLACE, request)
        }

        fun cancel(context: Context, id: String) {
            WorkManager.getInstance(context.applicationContext).cancelUniqueWork(name(id))
        }
    }
}
