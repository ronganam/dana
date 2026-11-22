package io.github.ronganam.dana.data.signals

import android.content.Context
import android.util.Log
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import io.github.ronganam.dana.data.network.ServerConfig
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * The app's one [SignalQueue] (`filesDir/pending_signals.json`) and how it
 * reaches the server: [SignalSendWorker] (WorkManager, network constraint,
 * linear backoff) when an event should go now, the periodic signals run for
 * the rest, and [flushAsync] when the server comes back online.
 */
object SignalSync {
    private const val TAG = "SignalSync"
    private const val FILE_NAME = "pending_signals.json"
    private const val SEND_WORK = "signals-send"

    @Volatile private var queue: SignalQueue? = null
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    fun queue(context: Context): SignalQueue = queue ?: synchronized(this) {
        queue ?: run {
            val app = context.applicationContext
            SignalQueue(File(app.filesDir, FILE_NAME), SignalSender { batch ->
                val config = ServerConfig.load(app)
                if (!config.isConfigured) SignalsPosted(401) else config.api().signals.send(batch)
            }).also { queue = it }
        }
    }

    /**
     * Queue [events]. [now]: send them as soon as there is a network (a car or
     * a place, an unlock in the wake window); otherwise the periodic run takes them.
     */
    fun add(context: Context, events: List<SignalEvent>, now: Boolean) {
        if (events.isEmpty()) return
        queue(context).add(events)
        Log.i(TAG, "Queued ${events.joinToString { it.javaClass.simpleName }}${if (now) ", sending" else ""}")
        if (now) scheduleSend(context)
    }

    /** Send what is waiting, in the background (the server just came back online). */
    fun flushAsync(context: Context) {
        val app = context.applicationContext
        scope.launch { if (queue(app).pending().isNotEmpty()) queue(app).flush() }
    }

    /**
     * A [SignalSendWorker] run once there is a network. REPLACE: an event
     * queued while one runs gets its own run (an interrupted batch is sent
     * again, which the server takes once).
     */
    fun scheduleSend(context: Context) {
        val request = OneTimeWorkRequestBuilder<SignalSendWorker>()
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setBackoffCriteria(BackoffPolicy.LINEAR, 1, TimeUnit.MINUTES)
            .build()
        WorkManager.getInstance(context.applicationContext).enqueueUniqueWork(SEND_WORK, ExistingWorkPolicy.REPLACE, request)
    }
}

/** Sends the queued signals; retried (1, 2, 3… minutes) while the server can't be reached. */
class SignalSendWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result {
        val r = SignalSync.queue(applicationContext).flush()
        return if (r.retry) Result.retry() else Result.success()
    }
}
