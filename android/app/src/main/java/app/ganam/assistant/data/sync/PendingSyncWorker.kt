package app.ganam.assistant.data.sync

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import app.ganam.assistant.data.shopping.ShoppingSync
import java.util.concurrent.TimeUnit

/**
 * Sends queued reminder actions ([TaskActionQueue]) and shopping changes made
 * offline ([app.ganam.assistant.data.shopping.ShoppingStore]) once there is a
 * network, retrying with a linear backoff (1, 2, 3… minutes) while the server
 * is unreachable, e.g. the VPN is off.
 */
class PendingSyncWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val tasks = TaskActionQueue.flush(applicationContext)
        val shopping = ShoppingSync.get(applicationContext).flush()
        return if (tasks == 0 && shopping == 0) Result.success() else Result.retry()
    }

    companion object {
        private const val WORK_NAME = "pending-task-actions"

        fun schedule(context: Context) {
            val request = OneTimeWorkRequestBuilder<PendingSyncWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.LINEAR, 1, TimeUnit.MINUTES)
                .setInitialDelay(30, TimeUnit.SECONDS)
                .build()
            // KEEP: a worker already backing off picks up the new action too.
            WorkManager.getInstance(context.applicationContext)
                .enqueueUniqueWork(WORK_NAME, ExistingWorkPolicy.KEEP, request)
        }
    }
}
