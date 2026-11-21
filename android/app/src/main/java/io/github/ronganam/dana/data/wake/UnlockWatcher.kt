package io.github.ronganam.dana.data.wake

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.util.Log
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import io.github.ronganam.dana.data.network.ApiService
import java.time.LocalDate
import java.time.LocalTime

/**
 * The morning wake signal (server `core/webhooks.py::handle_wake`): the first
 * unlocks of the day between 05:00 and 12:00 are reported to the server, which
 * confirms from the screen history that the user is really up and then fires the
 * wake reminders and the briefing. An alarm ringing or a glance at the clock
 * never unlocks the phone, so they don't count.
 *
 * ACTION_USER_PRESENT can't be declared in the manifest, so the receiver is
 * registered at runtime by the notification listener (which Android keeps
 * bound) and the app process. Once the server says "done" nothing more is sent
 * that day.
 */
object UnlockWatcher {
    private const val TAG = "UnlockWatcher"
    private const val PREFS = "DanaPrefs"
    private const val KEY_DONE = "wake_done_date"
    private const val KEY_LAST = "wake_last_report"
    private const val MIN_GAP_MS = 2 * 60 * 1000L

    @Volatile private var registered = false

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action == Intent.ACTION_USER_PRESENT) onUnlock(context.applicationContext)
        }
    }

    fun register(context: Context) {
        if (io.github.ronganam.dana.BuildConfig.KIOSK || io.github.ronganam.dana.BuildConfig.TV) return
        if (registered) return
        synchronized(this) {
            if (registered) return
            runCatching {
                androidx.core.content.ContextCompat.registerReceiver(
                    context.applicationContext, receiver, IntentFilter(Intent.ACTION_USER_PRESENT),
                    // USER_PRESENT is sent by SystemUI (not the system uid), which a
                    // NOT_EXPORTED receiver never gets. It's a protected broadcast,
                    // so no app can fake it.
                    androidx.core.content.ContextCompat.RECEIVER_EXPORTED
                )
                registered = true
            }.onFailure { Log.w(TAG, "Couldn't watch unlocks", it) }
        }
    }

    internal fun onUnlock(context: Context, now: Long = System.currentTimeMillis()) {
        val hour = LocalTime.now().hour
        Log.i(TAG, "Unlock seen at ${LocalTime.now().withNano(0)}")
        if (hour < 5 || hour >= 12) return
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (prefs.getString(KEY_DONE, null) == LocalDate.now().toString()) return
        if (now - prefs.getLong(KEY_LAST, 0) < MIN_GAP_MS) return
        prefs.edit().putLong(KEY_LAST, now).apply()
        WorkManager.getInstance(context).enqueueUniqueWork(
            "wake_unlock", ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<UnlockReportWorker>()
                .setInputData(workDataOf("at" to now))
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
        )
    }

    internal fun markDone(context: Context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY_DONE, LocalDate.now().toString()).apply()
    }
}

class UnlockReportWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val at = inputData.getLong("at", System.currentTimeMillis())
        // A report delayed over an hour (no network) is no longer a wake signal.
        if (System.currentTimeMillis() - at > 60 * 60 * 1000L) return Result.success()
        return try {
            val status = ApiService.fromPrefs(applicationContext).reportUnlock(at)
            Log.i("UnlockWatcher", "Unlock reported: $status")
            if (status == "done") UnlockWatcher.markDone(applicationContext)
            Result.success()
        } catch (e: Exception) {
            Log.w("UnlockWatcher", "Unlock report failed", e)
            if (runAttemptCount < 3) Result.retry() else Result.success()
        }
    }
}
