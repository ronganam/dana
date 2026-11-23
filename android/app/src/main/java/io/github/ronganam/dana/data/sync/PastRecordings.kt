package io.github.ronganam.dana.data.sync

import android.content.Context
import android.content.pm.ServiceInfo
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.net.toUri
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.ExistingWorkPolicy
import androidx.work.ForegroundInfo
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import io.github.ronganam.dana.R
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.withContext
import java.util.concurrent.TimeUnit

/**
 * "Upload past recordings" (Settings → Call recordings): the recordings
 * folder's old calls, sent by one unique worker ([PastRecordingsWorker]),
 * one recording at a time, oldest first. The handled-recordings file
 * ([CallUploadStore]) is the only progress record, so a run stopped by a
 * lost network continues where it stopped. The rules are pure, for tests.
 */
object PastRecordings {
    const val WORK = "past-recordings"
    const val KEY_STARTED = "started_ms"
    const val KEY_SINCE = "since_ms"
    const val KEY_DONE = "done"
    const val KEY_TOTAL = "total"
    const val KEY_UPLOADED = "uploaded"
    const val KEY_FAILED = "failed"
    const val KEY_STOPPED = "stopped"

    /** What the backlog run is doing, for Settings. */
    sealed interface Backlog {
        /** No run, or the last one was cancelled. */
        data object Idle : Backlog
        /** [done] of [total] handled ([total] 0: still looking); [waiting] for a network or a retry. */
        data class Running(val done: Int, val total: Int, val waiting: Boolean) : Backlog
        /** The last run ended; [stopped]: it gave up while the server was out of reach. */
        data class Finished(val uploaded: Int, val failed: Int, val stopped: Boolean) : Backlog
    }

    /** How far back a run goes, by the recording's last change; the user picks it in Settings. */
    enum class Range(val days: Int?, val label: String) {
        WEEK(7, "Last 7 days"), MONTH(30, "Last 30 days"), QUARTER(90, "Last 3 months"), ALL(null, "Everything"),
        /** From a date the user picks ([customSinceMs]: that day's start). */
        SINCE(null, "Since a date…");

        /** The oldest last-change time this range takes at [nowMs] (0: no limit; SINCE: [customSinceMs]). */
        fun sinceMs(nowMs: Long, customSinceMs: Long = 0L): Long = when {
            this == SINCE -> customSinceMs
            days != null -> nowMs - days * DAY_MS
            else -> 0L
        }

        companion object {
            val DEFAULT = MONTH
            fun of(name: String?): Range = entries.firstOrNull { it.name == name } ?: DEFAULT
        }
    }

    private const val DAY_MS = 24L * 60 * 60 * 1000

    /** What happens to one candidate once its call and length are known. */
    enum class Verdict { UPLOAD, TOO_SHORT, SKIPPED_CONTACT }

    /** How many recordings this run has uploaded and failed so far. */
    data class Tally(val uploaded: Int, val failed: Int) {
        val done get() = uploaded + failed
    }

    /**
     * The folder's recordings this run still has to look at, oldest first:
     * audio already known to the store as SEEN (an old call), NOTIFIED (asked
     * about, never answered) or FAILED before the run started ([startedMs];
     * one that failed during this run is not tried again until the next).
     * New files (no state, PENDING) belong to the folder scan; QUEUED,
     * UPLOADED and SKIPPED are done. Recordings last changed before [sinceMs]
     * (the run's [Range]) are left out.
     */
    fun candidates(
        docs: List<RecordingDoc>, entryOf: (String) -> CallUploadStore.Entry?, startedMs: Long, sinceMs: Long = 0L
    ): List<RecordingDoc> =
        docs.filter { FolderScan.isAudio(it.name, it.mime) && it.modifiedMs >= sinceMs }.distinctBy { it.name }.filter { doc ->
            val entry = entryOf(doc.name) ?: return@filter false
            when (entry.state) {
                CallUploadStore.State.SEEN, CallUploadStore.State.NOTIFIED -> true
                CallUploadStore.State.FAILED -> entry.updatedMs < startedMs
                else -> false
            }
        }.sortedBy { it.modifiedMs }

    /** What this run (started at [startedMs]) already did with the folder's recordings. */
    fun tally(docs: List<RecordingDoc>, entryOf: (String) -> CallUploadStore.Entry?, startedMs: Long): Tally {
        val since = docs.distinctBy { it.name }.mapNotNull { entryOf(it.name) }.filter { it.updatedMs >= startedMs }
        return Tally(
            uploaded = since.count { it.state == CallUploadStore.State.UPLOADED },
            failed = since.count { it.state == CallUploadStore.State.FAILED }
        )
    }

    /** Upload a call of [seconds] with a contact the user [skipped] or not, at [thresholdSeconds]. */
    fun verdict(seconds: Int, thresholdSeconds: Int, skipped: Boolean): Verdict = when {
        skipped -> Verdict.SKIPPED_CONTACT
        seconds < thresholdSeconds -> Verdict.TOO_SHORT
        else -> Verdict.UPLOAD
    }

    /** The backlog as Settings shows it, from the unique work's last state and its progress / output numbers. */
    fun backlog(state: WorkInfo.State?, done: Int, total: Int, uploaded: Int, failed: Int, stopped: Boolean): Backlog = when (state) {
        WorkInfo.State.RUNNING -> Backlog.Running(done, total, waiting = false)
        WorkInfo.State.ENQUEUED, WorkInfo.State.BLOCKED -> Backlog.Running(done, total, waiting = true)
        WorkInfo.State.SUCCEEDED, WorkInfo.State.FAILED -> Backlog.Finished(uploaded, failed, stopped)
        WorkInfo.State.CANCELLED, null -> Backlog.Idle
    }

    /** Start the backlog run over [range] ([customSinceMs] for SINCE); one already running or waiting is kept, never doubled. */
    fun start(context: Context, range: Range, customSinceMs: Long = 0L) {
        val app = context.applicationContext
        val now = System.currentTimeMillis()
        val network = if (CallUploadSettings.wifiOnly(app)) NetworkType.UNMETERED else NetworkType.CONNECTED
        val request = OneTimeWorkRequestBuilder<PastRecordingsWorker>()
            .setInputData(workDataOf(KEY_STARTED to now, KEY_SINCE to range.sinceMs(now, customSinceMs)))
            .setConstraints(Constraints.Builder().setRequiredNetworkType(network).build())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 1, TimeUnit.MINUTES)
            .build()
        WorkManager.getInstance(app).enqueueUniqueWork(WORK, ExistingWorkPolicy.KEEP, request)
    }

    /** Stop the run (the current upload is cancelled; the next run starts from it). */
    fun stop(context: Context) {
        WorkManager.getInstance(context.applicationContext).cancelUniqueWork(WORK)
    }

    /** The backlog's state, live. */
    fun status(context: Context): Flow<Backlog> =
        WorkManager.getInstance(context.applicationContext).getWorkInfosForUniqueWorkFlow(WORK).map { infos ->
            val info = infos.lastOrNull()
            val progress = info?.progress ?: Data.EMPTY
            val output = info?.outputData ?: Data.EMPTY
            backlog(
                info?.state,
                progress.getInt(KEY_DONE, 0), progress.getInt(KEY_TOTAL, 0),
                output.getInt(KEY_UPLOADED, 0), output.getInt(KEY_FAILED, 0), output.getBoolean(KEY_STOPPED, false)
            )
        }
}

/**
 * The backlog run: looks at every candidate's call and length without
 * copying it, then copies, uploads ([CallUploads.send]) and releases one
 * recording at a time, as a foreground `dataSync` job with a progress
 * notification and a Cancel action. One failed file is marked FAILED and
 * passed; an unreachable server ends the attempt with a retry, and the
 * next attempt continues from the store's states.
 */
class PastRecordingsWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {

    private class Item(val doc: RecordingDoc, val rec: RecordingName, val call: LoggedCall?, val fileSeconds: Int, val contact: String)

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val ctx = applicationContext
        val started = inputData.getLong(PastRecordings.KEY_STARTED, 0L)
        val since = inputData.getLong(PastRecordings.KEY_SINCE, 0L)
        val docs = RecordingsFolder.list(ctx)
        val entryOf = { name: String -> CallUploadStore.entry(ctx, name) }
        val threshold = CallUploadSettings.thresholdSeconds(ctx)
        val queue = PastRecordings.candidates(docs, entryOf, started, since).mapNotNull { doc -> look(ctx, doc, threshold) }
        var tally = PastRecordings.tally(docs, entryOf, started)
        val total = tally.done + queue.size
        Log.i(TAG, "Past recordings: ${queue.size} to upload, ${tally.done} done earlier in this run")

        for (item in queue) {
            report(tally.done, total, item.contact)
            val file = RecordingsFolder.copy(ctx, item.doc)
            if (file == null) {
                CallUploadStore.mark(ctx, item.doc.name, CallUploadStore.State.FAILED)
                tally = tally.copy(failed = tally.failed + 1)
                continue
            }
            val meta = RecordingInspector.describe(file, item.rec, item.call, item.doc.modifiedMs, item.fileSeconds)
            when (val outcome = CallUploads.send(ctx, meta)) {
                is CallUploads.Outcome.Uploaded -> tally = tally.copy(uploaded = tally.uploaded + 1)
                CallUploads.Outcome.Rejected, is CallUploads.Outcome.Failed -> {
                    fail(ctx, meta)
                    tally = tally.copy(failed = tally.failed + 1)
                }
                is CallUploads.Outcome.Unreachable -> {
                    if (runAttemptCount + 1 < MAX_ATTEMPTS) {
                        Log.w(TAG, "Server out of reach (${outcome.reason}); continuing later")
                        return@withContext Result.retry()
                    }
                    fail(ctx, meta)
                    tally = tally.copy(failed = tally.failed + 1)
                    summary(ctx, tally, stopped = true)
                    return@withContext Result.failure(output(tally, stopped = true))
                }
            }
        }
        summary(ctx, tally, stopped = false)
        Result.success(output(tally, stopped = false))
    }

    /** [doc]'s call and length (read in place, no copy), or null when it isn't to be uploaded. */
    private fun look(ctx: Context, doc: RecordingDoc, threshold: Int): Item? {
        val rec = RecordingNames.parse(doc.name)
        val call = RecordingInspector.matchCall(ctx, rec, doc.modifiedMs)
        val contact = RecordingInspector.contactOf(rec, call)
        val skipped = CallUploadSettings.isSkipped(ctx, contact)
        val fileSeconds = if (skipped) 0 else RecordingInspector.durationSeconds(ctx, doc.uri.toUri())
        // The recording is the truth for length; the call log only when it can't be read.
        val seconds = if (fileSeconds > 0) fileSeconds else call?.durationSeconds ?: 0
        return when (PastRecordings.verdict(seconds, threshold, skipped)) {
            PastRecordings.Verdict.UPLOAD -> Item(doc, rec, call, fileSeconds, contact)
            PastRecordings.Verdict.TOO_SHORT -> null
            PastRecordings.Verdict.SKIPPED_CONTACT -> {
                CallUploadStore.mark(ctx, doc.name, CallUploadStore.State.SKIPPED)
                null
            }
        }
    }

    private fun fail(ctx: Context, meta: RecordingMeta) {
        CallUploadStore.mark(ctx, meta.file.name, CallUploadStore.State.FAILED, meta.file.absolutePath, meta.contact, meta.timestamp)
        RecordingsFolder.release(ctx, meta.file)
    }

    private suspend fun report(done: Int, total: Int, contact: String) {
        setProgress(workDataOf(PastRecordings.KEY_DONE to done, PastRecordings.KEY_TOTAL to total))
        val ctx = applicationContext
        CallUploads.ensureChannel(ctx)
        val builder = NotificationCompat.Builder(ctx, CallUploads.CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_upload)
            .setContentTitle(ctx.getString(R.string.past_calls_progress, (done + 1).coerceAtMost(total), total))
            .setContentText(ctx.getString(R.string.past_calls_progress_text, contact))
            .setProgress(total, done, false)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .addAction(
                android.R.drawable.ic_menu_close_clear_cancel, ctx.getString(R.string.past_calls_cancel),
                WorkManager.getInstance(ctx).createCancelPendingIntent(id)
            )
        try {
            // Hundreds of uploads take long: a foreground job so the 10-minute worker limit and Doze don't cut it off.
            val notification = builder.build()
            setForeground(
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    ForegroundInfo(PROGRESS_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
                } else {
                    ForegroundInfo(PROGRESS_ID, notification)
                }
            )
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't run in the foreground; continuing", e)
            CallUploads.notify(ctx, PROGRESS_KEY, builder)
        }
    }

    private fun summary(ctx: Context, tally: PastRecordings.Tally, stopped: Boolean) {
        if (tally.done == 0 && !stopped) return
        val res = ctx.resources
        val text = buildList {
            add(res.getQuantityString(R.plurals.past_calls_uploaded, tally.uploaded, tally.uploaded))
            if (tally.failed > 0) add(res.getQuantityString(R.plurals.past_calls_failed, tally.failed, tally.failed))
        }.joinToString(" · ")
        CallUploads.notify(ctx, SUMMARY_KEY, NotificationCompat.Builder(ctx, CallUploads.CHANNEL_ID)
            .setSmallIcon(if (stopped || tally.failed > 0) android.R.drawable.stat_notify_error else android.R.drawable.stat_sys_upload_done)
            .setContentTitle(ctx.getString(if (stopped) R.string.past_calls_stopped else R.string.past_calls_done))
            .setContentText(text)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setAutoCancel(true))
    }

    private fun output(tally: PastRecordings.Tally, stopped: Boolean) = workDataOf(
        PastRecordings.KEY_UPLOADED to tally.uploaded, PastRecordings.KEY_FAILED to tally.failed, PastRecordings.KEY_STOPPED to stopped
    )

    private companion object {
        const val TAG = "PastRecordings"
        const val MAX_ATTEMPTS = 10
        const val PROGRESS_KEY = "past-recordings"
        const val SUMMARY_KEY = "past-recordings-done"
        // The same id CallUploads.notify uses for PROGRESS_KEY, so the fallback updates the same notification.
        val PROGRESS_ID = CallUploads.notificationId(PROGRESS_KEY)
    }
}
