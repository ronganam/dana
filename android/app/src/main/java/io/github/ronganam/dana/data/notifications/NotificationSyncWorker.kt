package io.github.ronganam.dana.data.notifications

import io.github.ronganam.dana.data.network.ServerConfig
import android.content.Context
import android.util.Log
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.CancellationException
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Drains [NotificationOutbox] to `POST /api/notifications/batch` (batched a few
 * seconds after the first new notification; short, so urgent ones reach the alert screen fast), then cancels in the shade
 * whatever the user dismissed in Dana (`cancel_keys`). Items leave the outbox
 * only after the server accepted them; failures retry with backoff.
 */
class NotificationSyncWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val ctx = applicationContext
        io.github.ronganam.dana.CallRecordingObserverService.ensureRunning(ctx)
        // Also a safety net for direct push: anything a lost ping didn't bring.
        io.github.ronganam.dana.push.PushSetup.ensureRegistered(ctx)
        io.github.ronganam.dana.push.PhoneEvents.catchUp(ctx)
        val api = ServerConfig.load(ctx).api()
        // Bank / card app notifications for the money add-on: their own queue and endpoint.
        val moneyDone = io.github.ronganam.dana.data.money.MoneySignals.drain(ctx, api)
        return try {
            var rounds = 0
            var changed = false
            do {
                val batch = NotificationOutbox.peek(ctx)
                val active = DanaNotificationListener.activeKeys()
                if (batch.isEmpty && active.isEmpty()) break
                val res = api.notifications.postBatch(batch.posted, batch.removed, active.toList(),
                    DanaNotificationListener.snoozedKeys().toList())
                NotificationOutbox.remove(ctx, batch.ids)
                if (!batch.isEmpty) changed = true
                Log.i(TAG, "Synced ${batch.posted.size} posted / ${batch.removed.size} removed: " +
                    "${res.accepted} new, ${res.duplicates} duplicates")
                if (res.cancelKeys.isNotEmpty()) DanaNotificationListener.cancel(res.cancelKeys)
                // Snoozed in Dana elsewhere (web, agent): snooze them in the shade too.
                val snooze = res.snoozeKeys
                if (snooze.isNotEmpty()) DanaNotificationListener.snooze(snooze)
                rounds++
            } while (!batch.isEmpty && rounds < 20)
            // New or removed notifications change Now and the Notifications screen.
            if (changed) io.github.ronganam.dana.data.sync.LiveRefresh.ping("sync")
            if (moneyDone) Result.success() else Result.retry()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Log.w(TAG, "Notification sync failed (${NotificationOutbox.size(ctx)} queued): ${e.message}")
            Result.retry()
        }
    }

    companion object {
        private const val TAG = "NotificationSync"
        private const val WORK_NAME = "notification-sync"
        private const val PERIODIC_NAME = "notification-sync-hourly"

        private val scheduler = Executors.newSingleThreadExecutor()

        /**
         * Debounced: notifications arriving within [delaySeconds] go in one
         * batch (a pending run is kept). A run waiting out a retry backoff
         * (server was down, can grow to hours) is replaced, so new
         * notifications — urgent ones especially — don't wait for it.
         */
        fun schedule(context: Context, delaySeconds: Long = 4) {
            val wm = WorkManager.getInstance(context.applicationContext)
            val request = OneTimeWorkRequestBuilder<NotificationSyncWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.LINEAR, 1, TimeUnit.MINUTES)
                .setInitialDelay(delaySeconds, TimeUnit.SECONDS)
                .build()
            val infos = wm.getWorkInfosForUniqueWork(WORK_NAME)
            infos.addListener({
                val retrying = try {
                    infos.get().any { it.state == WorkInfo.State.ENQUEUED && it.runAttemptCount > 0 }
                } catch (e: Exception) {
                    false
                }
                wm.enqueueUniqueWork(WORK_NAME, if (retrying) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.KEEP, request)
            }, scheduler)
        }

        /**
         * Hourly sync, always scheduled. It reconciles the shade (dismissals and
         * snoozes made in Dana, removals) when nothing new arrives, and — just as
         * important — keeps WorkManager from disabling its RescheduleReceiver
         * whenever the queue empties. Each enable/disable is a PACKAGE_CHANGED,
         * and Android unbinds and re-binds this app's notification listener on
         * every one: a swipe in the shade during that gap never reached Dana.
         */
        fun ensurePeriodic(context: Context) {
            val request = PeriodicWorkRequestBuilder<NotificationSyncWorker>(1, TimeUnit.HOURS)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            WorkManager.getInstance(context.applicationContext)
                .enqueueUniquePeriodicWork(PERIODIC_NAME, ExistingPeriodicWorkPolicy.KEEP, request)
        }

        /** Right away (the app came to the foreground / the user dismissed something). */
        fun syncNow(context: Context) = schedule(context, 0)
    }
}
