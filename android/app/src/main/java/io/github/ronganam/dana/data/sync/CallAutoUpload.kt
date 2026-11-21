package io.github.ronganam.dana.data.sync

import io.github.ronganam.dana.data.network.ServerConfig
import android.Manifest
import android.app.PendingIntent
import io.github.ronganam.dana.SuggestionsPopupActivity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.MediaMetadataRetriever
import android.provider.CallLog
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.work.BackoffPolicy
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import io.github.ronganam.dana.CallDebriefActivity
import kotlinx.coroutines.CancellationException
import java.io.File
import java.time.LocalDateTime
import java.time.ZoneId
import java.util.concurrent.TimeUnit
import kotlin.math.abs
import androidx.core.content.edit

/** User settings for call recordings and their automatic upload (DanaPrefs). */
object CallUploadSettings {
    private const val KEY_ENABLED = "call_recordings_enabled"
    private const val KEY_AUTO = "call_auto_upload"
    private const val KEY_THRESHOLD = "call_auto_threshold_seconds"
    private const val KEY_WIFI = "call_upload_wifi_only"
    private const val KEY_SKIP = "call_skip_contacts"

    private fun prefs(c: Context) = ServerConfig.prefs(c)

    /**
     * Watching for call recordings at all (off until the user turns it on in
     * Settings): only phones whose dialer saves recordings to Recordings/Call
     * (Samsung's built-in call recorder) have any.
     */
    fun enabled(c: Context): Boolean = prefs(c).getBoolean(KEY_ENABLED, false)
    fun setEnabled(c: Context, v: Boolean) = prefs(c).edit { putBoolean(KEY_ENABLED, v) }

    /** The permission that lets the app read the recordings folder. */
    val storagePermission: String
        get() = if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
            Manifest.permission.READ_MEDIA_AUDIO
        } else {
            Manifest.permission.READ_EXTERNAL_STORAGE
        }

    fun canWatch(c: Context): Boolean =
        enabled(c) && c.checkSelfPermission(storagePermission) == PackageManager.PERMISSION_GRANTED

    fun autoUpload(c: Context): Boolean = prefs(c).getBoolean(KEY_AUTO, true)
    fun setAutoUpload(c: Context, v: Boolean) = prefs(c).edit { putBoolean(KEY_AUTO, v) }

    /** Calls at least this long upload by themselves; shorter ones ask first. */
    fun thresholdSeconds(c: Context): Int = prefs(c).getInt(KEY_THRESHOLD, 10)
    fun setThresholdSeconds(c: Context, v: Int) = prefs(c).edit { putInt(KEY_THRESHOLD, v.coerceIn(0, 600)) }

    fun wifiOnly(c: Context): Boolean = prefs(c).getBoolean(KEY_WIFI, false)
    fun setWifiOnly(c: Context, v: Boolean) = prefs(c).edit { putBoolean(KEY_WIFI, v) }

    /** Contacts whose calls are never uploaded. */
    fun skippedContacts(c: Context): Set<String> = prefs(c).getStringSet(KEY_SKIP, emptySet()).orEmpty()
    fun isSkipped(c: Context, contact: String) = skippedContacts(c).any { it.equals(contact.trim(), ignoreCase = true) }
    fun addSkipped(c: Context, contact: String) =
        prefs(c).edit { putStringSet(KEY_SKIP, skippedContacts(c) + contact.trim()) }
    fun removeSkipped(c: Context, contact: String) =
        prefs(c).edit { putStringSet(KEY_SKIP, skippedContacts(c).filterNot { it.equals(contact, true) }.toSet()) }
}

/** What we know about a recording before uploading it. */
data class RecordingMeta(
    val file: File,
    val contact: String,
    val timestamp: String,
    val durationSeconds: Int,
    val direction: String?,
    val phoneNumber: String?
)

object RecordingInspector {
    private const val TAG = "RecordingInspector"

    /** Length of an audio file in seconds (0 when unreadable). */
    fun durationSeconds(file: File): Int {
        val r = MediaMetadataRetriever()
        return try {
            r.setDataSource(file.absolutePath)
            ((r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 0L) / 1000L).toInt()
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't read duration of ${file.name}", e)
            0
        } finally {
            runCatching { r.release() }
        }
    }

    /**
     * Wait until a just-written recording stops growing (CLOSE_WRITE can fire
     * for intermediate writes on some recorders).
     */
    fun awaitStable(file: File, maxWaitMs: Long = 8000) {
        var last = -1L
        var waited = 0L
        while (waited < maxWaitMs) {
            val now = file.length()
            if (now > 0 && now == last) return
            last = now
            Thread.sleep(1000)
            waited += 1000
        }
    }

    data class CallLogEntry(val direction: String, val number: String?, val durationSeconds: Int, val name: String?)

    /** The call-log entry closest to the recording's start time (needs READ_CALL_LOG). */
    fun callLogMatch(context: Context, isoTimestamp: String): CallLogEntry? {
        if (context.checkSelfPermission(Manifest.permission.READ_CALL_LOG) != PackageManager.PERMISSION_GRANTED) return null
        val startMs = runCatching {
            LocalDateTime.parse(isoTimestamp).atZone(ZoneId.systemDefault()).toInstant().toEpochMilli()
        }.getOrNull() ?: return null
        val from = startMs - 3 * 60_000L
        val to = startMs + 2 * 60_000L
        return try {
            context.contentResolver.query(
                CallLog.Calls.CONTENT_URI,
                arrayOf(CallLog.Calls.TYPE, CallLog.Calls.NUMBER, CallLog.Calls.DURATION, CallLog.Calls.DATE, CallLog.Calls.CACHED_NAME),
                "${CallLog.Calls.DATE} BETWEEN ? AND ?",
                arrayOf(from.toString(), to.toString()),
                "${CallLog.Calls.DATE} DESC"
            )?.use { c ->
                var best: CallLogEntry? = null
                var bestDelta = Long.MAX_VALUE
                while (c.moveToNext()) {
                    val delta = abs(c.getLong(3) - startMs)
                    if (delta < bestDelta) {
                        bestDelta = delta
                        val direction = when (c.getInt(0)) {
                            CallLog.Calls.INCOMING_TYPE, CallLog.Calls.ANSWERED_EXTERNALLY_TYPE -> "incoming"
                            CallLog.Calls.OUTGOING_TYPE -> "outgoing"
                            CallLog.Calls.MISSED_TYPE, CallLog.Calls.REJECTED_TYPE -> "missed"
                            else -> "incoming"
                        }
                        best = CallLogEntry(direction, c.getString(1), c.getInt(2), c.getString(4))
                    }
                }
                best
            }
        } catch (e: Exception) {
            Log.w(TAG, "Call log lookup failed", e)
            null
        }
    }

    fun inspect(context: Context, file: File, contact: String, timestamp: String): RecordingMeta {
        val log = callLogMatch(context, timestamp)
        val fileDuration = durationSeconds(file)
        // The recording is the truth for length (the log counts ringing/hold on some ROMs).
        val duration = if (fileDuration > 0) fileDuration else log?.durationSeconds ?: 0
        val name = contact.takeIf { it.isNotBlank() && !it.all { ch -> ch.isDigit() || ch == '+' } } ?: log?.name ?: contact
        return RecordingMeta(file, name, timestamp, duration, log?.direction, log?.number)
    }
}

/**
 * After an upload: wait for the server's analysis (it runs in the background)
 * and post the result — title, labels and how many suggestions are waiting —
 * with a Review action that opens the Debrief screen.
 */
class CallResultWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val callId = inputData.getString(KEY_CALL_ID) ?: return Result.failure()
        val fileName = inputData.getString(KEY_FILE) ?: callId
        val contact = inputData.getString(KEY_CONTACT) ?: "Unknown"
        val ctx = applicationContext
        val detail = try {
            ServerConfig.load(ctx).api().calls.detail(callId)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Log.w(TAG, "Checking call $callId failed: ${e.message}")
            return if (runAttemptCount < MAX_ATTEMPTS) Result.retry() else Result.failure()
        }
        val call = detail.call
        when (call.status) {
            "processing" -> return if (runAttemptCount < MAX_ATTEMPTS) Result.retry() else Result.failure()
            "failed" -> {
                CallUploads.notify(ctx, fileName, NotificationCompat.Builder(ctx, CallUploads.CHANNEL_ID)
                    .setSmallIcon(android.R.drawable.stat_notify_error)
                    .setContentTitle("Couldn't analyse the call with $contact")
                    .setContentText(call.error ?: "The recording had no speech.")
                    .setContentIntent(debriefIntent(ctx, callId))
                    .setAutoCancel(true))
                return Result.success()
            }
        }
        val pending = detail.suggestions.count { it.status == "pending" }
        val alert = detail.suggestions.firstOrNull { it.status == "pending" && it.kind == "alert" }
        val labelLine = call.labels.joinToString("  ") { "${it.emoji} ${it.name}" }
        val text = when {
            alert != null -> alert.alertText ?: alert.title
            pending == 1 -> "1 suggestion waiting · ${detail.suggestions.first { it.status == "pending" }.title}"
            pending > 1 -> "$pending suggestions waiting"
            else -> call.summary?.lineSequence()?.firstOrNull()?.removePrefix("- ")?.trim().orEmpty()
        }
        val builder = NotificationCompat.Builder(ctx, CallUploads.CHANNEL_ID)
            .setSmallIcon(if (alert != null) android.R.drawable.stat_sys_warning else android.R.drawable.stat_sys_upload_done)
            .setContentTitle("${call.labels.firstOrNull()?.emoji ?: "📞"} $contact · ${call.title ?: "Call"}")
            .setContentText(text)
            .setSubText(labelLine.ifBlank { null })
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setContentIntent(debriefIntent(ctx, callId))
            .setPriority(if (alert != null || pending > 0) NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_LOW)
            .setAutoCancel(true)
        if (pending > 0) {
            // Review = the suggestion pop-up for this call (the server also pushes it for fresh calls).
            val review = PendingIntent.getActivity(
                ctx, ("review:$callId").hashCode(),
                SuggestionsPopupActivity.intent(ctx, detail.suggestions.filter { it.status == "pending" }.map { it.id },
                    "From the call with $contact", callId, notificationKey = "call:$callId"),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            builder.addAction(android.R.drawable.ic_menu_view, "Review", review)
        }
        CallUploads.notify(ctx, fileName, builder)
        return Result.success()
    }

    companion object {
        private const val TAG = "CallResultWorker"
        private const val MAX_ATTEMPTS = 30
        const val KEY_CALL_ID = "call_id"
        const val KEY_FILE = "file"
        const val KEY_CONTACT = "contact"

        fun enqueue(context: Context, callId: String, fileName: String, contact: String, delaySeconds: Long = 20) {
            val request = OneTimeWorkRequestBuilder<CallResultWorker>()
                .setInputData(workDataOf(KEY_CALL_ID to callId, KEY_FILE to fileName, KEY_CONTACT to contact))
                .setInitialDelay(delaySeconds, TimeUnit.SECONDS)
                .setBackoffCriteria(BackoffPolicy.LINEAR, 30, TimeUnit.SECONDS)
                .build()
            WorkManager.getInstance(context.applicationContext)
                .enqueueUniqueWork("call-result-$callId", ExistingWorkPolicy.REPLACE, request)
        }

        fun debriefIntent(context: Context, callId: String): PendingIntent =
            PendingIntent.getActivity(
                context, callId.hashCode(),
                Intent(context, CallDebriefActivity::class.java)
                    .putExtra(CallDebriefActivity.EXTRA_CALL_ID, callId)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
    }
}
