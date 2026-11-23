package io.github.ronganam.dana.data.sync

import android.content.Context
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.util.concurrent.TimeUnit

/**
 * When the recordings folder is scanned. There is no watcher process: Android
 * gives no change events for a Storage Access Framework tree, so the folder
 * is scanned every 15 minutes (WorkManager, survives reboots), when the app
 * comes to the foreground, and a few seconds after a call ends (the phone-state
 * broadcast `CallerCardReceiver` gets).
 */
object RecordingScans {
    private const val PERIODIC = "call-recordings-scan"
    private const val ONCE = "call-recordings-scan-now"
    /** After a call: the recorder needs a moment to close the file. */
    const val AFTER_CALL_DELAY_SECONDS = 10L

    /** Keep the periodic scan when call recordings are on and the folder readable; cancel it otherwise. */
    fun schedule(context: Context) {
        val wm = WorkManager.getInstance(context.applicationContext)
        if (!CallUploadSettings.canWatch(context)) {
            wm.cancelUniqueWork(PERIODIC)
            return
        }
        wm.enqueueUniquePeriodicWork(
            PERIODIC, ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<RecordingScanWorker>(15, TimeUnit.MINUTES).build()
        )
    }

    /** One scan soon ([delaySeconds] from now), when call recordings are on. */
    fun now(context: Context, delaySeconds: Long = 0) {
        if (!CallUploadSettings.canWatch(context)) return
        WorkManager.getInstance(context.applicationContext).enqueueUniqueWork(
            ONCE, ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<RecordingScanWorker>().setInitialDelay(delaySeconds, TimeUnit.SECONDS).build()
        )
    }

    /** Call recordings turned off. */
    fun stop(context: Context) {
        val wm = WorkManager.getInstance(context.applicationContext)
        wm.cancelUniqueWork(PERIODIC)
        wm.cancelUniqueWork(ONCE)
    }
}

/**
 * One scan of the recordings folder ([FolderScan] decides which files): long
 * calls upload by themselves, short ones ask first, skipped contacts are
 * ignored, and with auto-upload off every new recording asks.
 */
class RecordingScanWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val ctx = applicationContext
        if (!CallUploadSettings.canWatch(ctx)) return Result.success()
        val folder = RecordingsFolder.tree(ctx)?.toString() ?: return Result.success()
        val now = System.currentTimeMillis()
        val plan = FolderScan.plan(RecordingsFolder.list(ctx), CallUploadStore.isSeeded(ctx, folder), { CallUploadStore.state(ctx, it) }, now)
        if (!CallUploadStore.isSeeded(ctx, folder)) CallUploadStore.seed(ctx, folder, plan.seed)
        plan.handle.forEach { (doc, retry) ->
            try {
                handle(ctx, doc, retry)
            } catch (e: Exception) {
                Log.e(TAG, "Handling ${doc.name} failed", e)
            }
        }
        if (plan.settling) RecordingScans.now(ctx, FolderScan.SETTLE_MS / 1000 + 5)
        RecordingsFolder.prune(ctx, now)
        return Result.success()
    }

    private fun handle(ctx: Context, doc: RecordingDoc, retry: Boolean) {
        val prior = CallUploadStore.state(ctx, doc.name)
        val file = RecordingsFolder.copy(ctx, doc) ?: run {
            Log.w(TAG, "Couldn't read ${doc.name}; the next scan tries again")
            return
        }
        val meta = RecordingInspector.inspect(ctx, file, doc.name, doc.modifiedMs)
        // PENDING = spotted but not handled yet: a scan after the app was killed picks it up again.
        if (prior == null) CallUploadStore.mark(ctx, doc.name, CallUploadStore.State.PENDING, file.absolutePath, meta.contact, meta.timestamp)
        Log.i(TAG, "${if (retry) "Re-checking" else "New"} call recording: ${meta.durationSeconds}s, ${meta.direction ?: "no call-log match"}")
        when {
            CallUploadSettings.isSkipped(ctx, meta.contact) -> {
                CallUploadStore.mark(ctx, doc.name, CallUploadStore.State.SKIPPED)
                RecordingsFolder.release(ctx, file)
            }
            CallUploadSettings.autoUpload(ctx) && meta.durationSeconds >= CallUploadSettings.thresholdSeconds(ctx) ->
                CallUploads.enqueue(ctx, meta)
            // Already asked about it before the app was killed: don't ask twice.
            prior == CallUploadStore.State.NOTIFIED -> Unit
            else -> {
                CallUploadStore.mark(ctx, doc.name, CallUploadStore.State.NOTIFIED)
                CallUploads.notifyAsk(ctx, meta, short = CallUploadSettings.autoUpload(ctx))
            }
        }
    }

    private companion object {
        const val TAG = "RecordingScan"
    }
}
