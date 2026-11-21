package io.github.ronganam.dana.data.sync

import io.github.ronganam.dana.data.network.ServerConfig
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.ForegroundInfo
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import io.github.ronganam.dana.data.network.ApiException
import io.github.ronganam.dana.ui.viewmodels.describeError
import kotlinx.coroutines.CancellationException
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Call-recording uploads.
 *
 * - [CallUploadStore] remembers which recordings were already handled, on
 *   disk, so a rescan or a service restart doesn't re-notify old calls.
 * - [CallUploadWorker] does the upload through WorkManager: it waits for a
 *   network, retries with backoff when the server is unreachable, and survives
 *   the app being killed.
 * - Failure notifications carry a Retry action ([CallUploadReceiver]).
 */
object CallUploads {
    private const val TAG = "CallUploads"
    const val CHANNEL_ID = "call_observer_channel"
    private const val MAX_AUTOMATIC_ATTEMPTS = 5

    const val KEY_PATH = "path"
    const val KEY_CONTACT = "contact"
    const val KEY_TIMESTAMP = "timestamp"
    const val KEY_DURATION = "duration"
    const val KEY_DIRECTION = "direction"
    const val KEY_NUMBER = "number"

    fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            if (nm.getNotificationChannel(CHANNEL_ID) != null) return
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Call recordings", NotificationManager.IMPORTANCE_DEFAULT)
                    .apply { description = "Handles call recording detection and upload progress" }
            )
        }
    }

    /** Queue [file] for upload. [replace] restarts a failed/finished attempt. */
    fun enqueue(
        context: Context, file: File, contact: String, timestamp: String, replace: Boolean = false,
        durationSeconds: Int = 0, direction: String? = null, phoneNumber: String? = null
    ) {
        val app = context.applicationContext
        CallUploadStore.mark(app, file.name, CallUploadStore.State.QUEUED, file.absolutePath, contact, timestamp)
        val network = if (CallUploadSettings.wifiOnly(app)) NetworkType.UNMETERED else NetworkType.CONNECTED
        val request = OneTimeWorkRequestBuilder<CallUploadWorker>()
            .setInputData(workDataOf(
                KEY_PATH to file.absolutePath, KEY_CONTACT to contact, KEY_TIMESTAMP to timestamp,
                KEY_DURATION to durationSeconds, KEY_DIRECTION to direction, KEY_NUMBER to phoneNumber
            ))
            .setConstraints(Constraints.Builder().setRequiredNetworkType(network).build())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 1, TimeUnit.MINUTES)
            .addTag(TAG)
            .build()
        WorkManager.getInstance(app).enqueueUniqueWork(
            workName(file.name),
            if (replace) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.KEEP,
            request
        )
        Log.i(TAG, "Queued upload of ${file.name}")
    }

    fun enqueue(context: Context, meta: RecordingMeta, replace: Boolean = false) =
        enqueue(context, meta.file, meta.contact, meta.timestamp, replace, meta.durationSeconds, meta.direction, meta.phoneNumber)

    /**
     * Upload every recording still on the phone that is long enough and was
     * never uploaded from here. The server recognises recordings it already
     * has (same bytes / file name), so this is safe to run any time.
     */
    fun uploadPastRecordings(context: Context): Int {
        val dir = File(RECORDINGS_DIR)
        val threshold = CallUploadSettings.thresholdSeconds(context)
        var queued = 0
        dir.listFiles { f -> f.name.endsWith(".m4a") }.orEmpty().sortedBy { it.lastModified() }.forEach { file ->
            val state = CallUploadStore.state(context, file.name)
            if (state == CallUploadStore.State.UPLOADED || state == CallUploadStore.State.QUEUED) return@forEach
            val info = io.github.ronganam.dana.ui.viewmodels.CallsController.parseSamsungFilename(file.name) ?: return@forEach
            if (CallUploadSettings.isSkipped(context, info.first)) return@forEach
            val meta = RecordingInspector.inspect(context, file, info.first, info.second)
            if (meta.durationSeconds < threshold) return@forEach
            enqueue(context, meta)
            queued++
        }
        return queued
    }

    const val RECORDINGS_DIR = "/storage/emulated/0/Recordings/Call"

    /** Heads-up "short call — upload?" with Upload / Skip; tapping opens the short-call sheet. */
    fun notifyShortCall(context: Context, meta: RecordingMeta) {
        val name = meta.file.name
        val upload = PendingIntent.getBroadcast(
            context, name.hashCode() + 11,
            Intent(context, CallUploadReceiver::class.java).setAction(CallUploadReceiver.ACTION_UPLOAD_SHORT).putMeta(meta),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val skip = PendingIntent.getBroadcast(
            context, name.hashCode() + 12,
            Intent(context, CallUploadReceiver::class.java).setAction(CallUploadReceiver.ACTION_SKIP).putMeta(meta),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val open = PendingIntent.getActivity(
            context, name.hashCode() + 13,
            Intent(context, io.github.ronganam.dana.ShortCallActivity::class.java).putMeta(meta)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        notify(context, name, NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_upload)
            .setContentTitle("Short call with ${meta.contact} (${meta.durationSeconds}s)")
            .setContentText("Upload it to Dana?")
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(open)
            .addAction(android.R.drawable.ic_menu_upload, "Upload", upload)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Skip", skip)
            .setAutoCancel(true))
    }

    internal fun Intent.putMeta(meta: RecordingMeta): Intent = apply {
        putExtra(KEY_PATH, meta.file.absolutePath)
        putExtra(KEY_CONTACT, meta.contact)
        putExtra(KEY_TIMESTAMP, meta.timestamp)
        putExtra(KEY_DURATION, meta.durationSeconds)
        putExtra(KEY_DIRECTION, meta.direction)
        putExtra(KEY_NUMBER, meta.phoneNumber)
    }

    internal fun Intent.readMeta(): RecordingMeta? {
        val path = getStringExtra(KEY_PATH) ?: return null
        return RecordingMeta(
            File(path), getStringExtra(KEY_CONTACT) ?: "Unknown", getStringExtra(KEY_TIMESTAMP) ?: "",
            getIntExtra(KEY_DURATION, 0), getStringExtra(KEY_DIRECTION), getStringExtra(KEY_NUMBER)
        )
    }

    fun cancelNotification(context: Context, fileName: String) {
        (context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).cancel(notificationId(fileName))
    }

    private fun workName(fileName: String) = "call-upload-$fileName"

    internal fun notificationId(fileName: String) = fileName.hashCode()

    internal fun notify(context: Context, fileName: String, builder: NotificationCompat.Builder) {
        ensureChannel(context)
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        try {
            nm.notify(notificationId(fileName), builder.build())
        } catch (e: SecurityException) {
            Log.w(TAG, "Notification permission missing", e)
        }
    }

    internal fun retryIntent(context: Context, path: String, contact: String, timestamp: String): PendingIntent {
        val intent = Intent(context, CallUploadReceiver::class.java).apply {
            action = CallUploadReceiver.ACTION_RETRY
            putExtra(KEY_PATH, path)
            putExtra(KEY_CONTACT, contact)
            putExtra(KEY_TIMESTAMP, timestamp)
        }
        return PendingIntent.getBroadcast(
            context, File(path).name.hashCode(), intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }

    internal val maxAutomaticAttempts get() = MAX_AUTOMATIC_ATTEMPTS
}

class CallUploadWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val path = inputData.getString(CallUploads.KEY_PATH) ?: return Result.failure()
        val contact = inputData.getString(CallUploads.KEY_CONTACT) ?: "Unknown"
        val timestamp = inputData.getString(CallUploads.KEY_TIMESTAMP) ?: ""
        val file = File(path)
        val ctx = applicationContext

        if (!file.exists()) {
            CallUploadStore.mark(ctx, file.name, CallUploadStore.State.FAILED)
            CallUploads.notify(ctx, file.name, baseNotification("Upload Failed", "The recording of the call with $contact no longer exists.", android.R.drawable.stat_notify_error))
            return Result.failure()
        }

        val duration = inputData.getInt(CallUploads.KEY_DURATION, 0)
        val direction = inputData.getString(CallUploads.KEY_DIRECTION)
        val number = inputData.getString(CallUploads.KEY_NUMBER)
        val progress = baseNotification("Uploading call with $contact", "Sending the recording to your assistant…", android.R.drawable.stat_sys_upload)
            .setProgress(100, 0, true)
            .setOngoing(true)
        try {
            // Long calls take minutes to transcribe; run as a foreground job
            // so the 10-minute worker limit and Doze don't cut it off.
            setForeground(foregroundInfo(file.name, progress))
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't run the upload in the foreground; continuing", e)
            CallUploads.notify(ctx, file.name, progress)
        }

        val failure: String = try {
            val result = ServerConfig.load(ctx).api().calls.upload(
                file, contact, "Me", timestamp,
                durationSeconds = duration, direction = direction, phoneNumber = number
            )
            if (result != null) {
                CallUploadStore.mark(ctx, file.name, CallUploadStore.State.UPLOADED)
                val callId = result.id.orEmpty()
                val status = result.status.orEmpty()
                if (callId.isNotEmpty()) {
                    // Quiet "analysing" line; CallResultWorker replaces it with the result.
                    CallUploads.notify(ctx, file.name, baseNotification(
                        "Call with $contact uploaded", "Analysing — suggestions will show up here.",
                        android.R.drawable.stat_sys_upload_done
                    ).setPriority(NotificationCompat.PRIORITY_LOW).setSilent(true))
                    CallResultWorker.enqueue(ctx, callId, file.name, contact,
                        delaySeconds = if (status == "processing") 20 else 0)
                }
                return Result.success()
            }
            // 4xx: retrying the same file won't help; let the user decide.
            return failed(file, contact, timestamp, "The server rejected the recording.")
        } catch (e: CancellationException) {
            throw e
        } catch (e: IOException) {
            describeError(e)
        } catch (e: ApiException) {
            describeError(e)
        } catch (e: Exception) {
            Log.e(TAG, "Unexpected upload error", e)
            return failed(file, contact, timestamp, describeError(e))
        }

        // Unreachable or a server error: retry with backoff, then give up.
        return if (runAttemptCount + 1 < CallUploads.maxAutomaticAttempts) {
            Log.w(TAG, "Upload of ${file.name} failed ($failure); will retry")
            CallUploads.notify(ctx, file.name, baseNotification("Upload Waiting", "Call with $contact: $failure. Retrying automatically.", android.R.drawable.stat_sys_upload)
                .addAction(android.R.drawable.ic_menu_upload, "Retry now", CallUploads.retryIntent(ctx, file.absolutePath, contact, timestamp)))
            Result.retry()
        } else {
            failed(file, contact, timestamp, failure)
        }
    }

    private fun failed(file: File, contact: String, timestamp: String, reason: String): Result {
        CallUploadStore.mark(applicationContext, file.name, CallUploadStore.State.FAILED)
        CallUploads.notify(applicationContext, file.name,
            baseNotification("Upload Failed", "Call with $contact: $reason", android.R.drawable.stat_notify_error)
                .addAction(android.R.drawable.ic_menu_upload, "Retry", CallUploads.retryIntent(applicationContext, file.absolutePath, contact, timestamp)))
        return Result.failure()
    }

    private fun baseNotification(title: String, text: String, icon: Int) =
        NotificationCompat.Builder(applicationContext, CallUploads.CHANNEL_ID)
            .setSmallIcon(icon)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setAutoCancel(true)

    private fun foregroundInfo(fileName: String, builder: NotificationCompat.Builder): ForegroundInfo {
        CallUploads.ensureChannel(applicationContext)
        // A different id than the per-file notification: WorkManager removes
        // the foreground notification when the job ends, which would
        // otherwise wipe the success/failure message.
        val id = CallUploads.notificationId(fileName) + 1
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            ForegroundInfo(id, builder.build(), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            ForegroundInfo(id, builder.build())
        }
    }

    private companion object {
        const val TAG = "CallUploadWorker"
    }
}

/** Notification actions: Retry a failed upload, Upload / Skip a short call. */
class CallUploadReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        with(CallUploads) {
            when (intent.action) {
                ACTION_RETRY -> {
                    val path = intent.getStringExtra(KEY_PATH) ?: return
                    enqueue(
                        context, File(path),
                        intent.getStringExtra(KEY_CONTACT) ?: "Unknown",
                        intent.getStringExtra(KEY_TIMESTAMP) ?: "",
                        replace = true,
                        durationSeconds = intent.getIntExtra(KEY_DURATION, 0),
                        direction = intent.getStringExtra(KEY_DIRECTION),
                        phoneNumber = intent.getStringExtra(KEY_NUMBER)
                    )
                }
                ACTION_UPLOAD_SHORT -> {
                    val meta = intent.readMeta() ?: return
                    cancelNotification(context, meta.file.name)
                    enqueue(context, meta, replace = true)
                }
                ACTION_SKIP -> {
                    val meta = intent.readMeta() ?: return
                    cancelNotification(context, meta.file.name)
                    CallUploadStore.mark(context, meta.file.name, CallUploadStore.State.SKIPPED)
                }
            }
        }
    }

    companion object {
        const val ACTION_RETRY = "io.github.ronganam.dana.RETRY_CALL_UPLOAD"
        const val ACTION_UPLOAD_SHORT = "io.github.ronganam.dana.UPLOAD_SHORT_CALL"
        const val ACTION_SKIP = "io.github.ronganam.dana.SKIP_CALL"
    }
}

/**
 * Which call recordings the app has already handled, keyed by file name, in
 * `filesDir/call_uploads.json`. Replaces an in-memory set that was lost on
 * every restart.
 */
object CallUploadStore {
    private const val TAG = "CallUploadStore"
    private const val FILE_NAME = "call_uploads.json"
    private const val KEY_SEEDED = "__seeded__"
    private val lock = Any()

    /** PENDING = spotted, still being inspected (retried by the next rescan if the app dies first). */
    enum class State { SEEN, PENDING, NOTIFIED, QUEUED, UPLOADED, FAILED, SKIPPED }

    fun isKnown(context: Context, fileName: String): Boolean = synchronized(lock) { read(context).has(fileName) }

    fun state(context: Context, fileName: String): State? = synchronized(lock) {
        read(context).optJSONObject(fileName)?.optString("state")?.let { raw -> State.values().firstOrNull { it.name == raw } }
    }

    fun isSeeded(context: Context): Boolean = synchronized(lock) { read(context).optBoolean(KEY_SEEDED, false) }

    /**
     * First run: record the recordings already on the phone as seen, so the
     * first rescan doesn't post a notification for every old call. Old calls
     * can still be uploaded from the Calls screen.
     */
    fun seed(context: Context, existing: List<String>) = synchronized(lock) {
        val json = read(context)
        existing.forEach { if (!json.has(it)) json.put(it, entry(State.SEEN, null, null, null)) }
        json.put(KEY_SEEDED, true)
        write(context, json)
    }

    fun mark(
        context: Context,
        fileName: String,
        state: State,
        path: String? = null,
        contact: String? = null,
        timestamp: String? = null
    ) = synchronized(lock) {
        val json = read(context)
        val prev = json.optJSONObject(fileName)
        json.put(fileName, entry(
            state,
            path ?: prev?.optString("path"),
            contact ?: prev?.optString("contact"),
            timestamp ?: prev?.optString("timestamp")
        ))
        write(context, json)
    }

    private fun entry(state: State, path: String?, contact: String?, timestamp: String?) = JSONObject().apply {
        put("state", state.name)
        path?.let { put("path", it) }
        contact?.let { put("contact", it) }
        timestamp?.let { put("timestamp", it) }
        put("updated_at", System.currentTimeMillis())
    }

    private fun file(context: Context) = File(context.filesDir, FILE_NAME)

    /** The file's contents, read once per process (callers hold [lock]). A rescan
     *  asks about every recording on the phone; re-reading the whole file for
     *  each one froze the app for tens of seconds. */
    private var cache: JSONObject? = null

    private fun read(context: Context): JSONObject {
        cache?.let { return it }
        val json = try {
            val f = file(context)
            if (f.exists()) JSONObject(f.readText()) else JSONObject()
        } catch (e: Exception) {
            Log.e(TAG, "Couldn't read $FILE_NAME", e)
            JSONObject()
        }
        cache = json
        return json
    }

    private fun write(context: Context, json: JSONObject) {
        cache = json
        try {
            val f = file(context)
            val tmp = File(f.parentFile, "$FILE_NAME.tmp")
            tmp.writeText(json.toString())
            if (!tmp.renameTo(f)) {
                f.writeText(json.toString())
                tmp.delete()
            }
        } catch (e: Exception) {
            Log.e(TAG, "Couldn't write $FILE_NAME", e)
        }
    }
}
