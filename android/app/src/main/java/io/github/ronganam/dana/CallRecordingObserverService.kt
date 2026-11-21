package io.github.ronganam.dana

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.FileObserver
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import io.github.ronganam.dana.data.sync.CallUploadStore
import io.github.ronganam.dana.data.sync.CallUploads
import io.github.ronganam.dana.data.sync.CallUploadSettings
import io.github.ronganam.dana.data.sync.RecordingInspector
import java.io.File

class CallRecordingObserverService : Service() {

    companion object {
        private const val TAG = "CallRecordingObserver"
        // Recordings left half-handled by a restart are retried for this long.
        private const val RECOVER_WINDOW_MS = 3L * 24 * 3600 * 1000
        private const val CHANNEL_ID = CallUploads.CHANNEL_ID
        private const val RECORDINGS_DIR = "/storage/emulated/0/Recordings/Call"

        @Volatile
        var running = false
            private set

        /**
         * Start it if it isn't running and storage access is granted. Called
         * from places Android reliably brings back after an update or a kill
         * (the notification listener, the hourly sync), because the update
         * broadcast and the app's own screen don't always arrive.
         */
        fun ensureRunning(context: Context) {
            if (running) return
            val permission = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                android.Manifest.permission.READ_MEDIA_AUDIO
            } else {
                android.Manifest.permission.READ_EXTERNAL_STORAGE
            }
            if (context.checkSelfPermission(permission) != android.content.pm.PackageManager.PERMISSION_GRANTED) return
            Log.i(TAG, "Observer not running; starting it")
            start(context.applicationContext)
        }

        /** Start (or keep) the observer; safe to call from the app or at boot. */
        fun start(context: Context) {
            val intent = Intent(context, CallRecordingObserverService::class.java)
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    context.startForegroundService(intent)
                } else {
                    context.startService(intent)
                }
            } catch (e: Exception) {
                Log.e(TAG, "Failed to start CallRecordingObserverService", e)
            }
        }
        private const val NOTIFICATION_ID = 9999
        private const val UPLOAD_ACTION = "io.github.ronganam.dana.UPLOAD_CALL"
        private const val RESCAN_ACTION = "io.github.ronganam.dana.RESCAN_CALLS"
        private const val EXTRA_FILE_PATH = "file_path"
        private const val EXTRA_CONTACT_NAME = "contact_name"
        private const val EXTRA_TIMESTAMP = "timestamp"
    }

    private var fileObserver: FileObserver? = null

    override fun onCreate() {
        super.onCreate()
        Log.d(TAG, "Service onCreate")
        running = true
        createNotificationChannel()
        try {
            startForegroundService()
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start foreground service", e)
        }
        startWatching()
        // Catch recordings made while the service wasn't running (e.g. right
        // after a reboot, before BootReceiver started it).
        rescanDirectory()
    }


    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            UPLOAD_ACTION -> {
                val filePath = intent.getStringExtra(EXTRA_FILE_PATH) ?: ""
                val contactName = intent.getStringExtra(EXTRA_CONTACT_NAME) ?: "Unknown"
                val timestamp = intent.getStringExtra(EXTRA_TIMESTAMP) ?: ""
                if (filePath.isNotEmpty()) {
                    // WorkManager waits for a network and retries with backoff.
                    CallUploads.enqueue(this, File(filePath), contactName, timestamp)
                    (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
                        .cancel(File(filePath).name.hashCode())
                }
            }
            RESCAN_ACTION -> {
                rescanDirectory()
            }
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun startForegroundService() {
        val pendingFlags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        } else {
            PendingIntent.FLAG_UPDATE_CURRENT
        }

        val rescanIntent = Intent(this, CallRecordingObserverService::class.java).apply {
            action = RESCAN_ACTION
        }
        val rescanPendingIntent = PendingIntent.getService(
            this, 0, rescanIntent, pendingFlags
        )

        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_menu_call)
            .setContentTitle("Call assistant")
            .setContentText("Watching for new call recordings")
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .addAction(
                android.R.drawable.ic_menu_search,
                "Rescan",
                rescanPendingIntent
            )
            .build()
        try {
            startForeground(NOTIFICATION_ID, notification)
        } catch (e: Exception) {
            Log.e(TAG, "startForeground failed, service will run without foreground status", e)
        }
    }

    private fun startWatching() {
        val directory = File(RECORDINGS_DIR)
        if (!directory.exists()) {
            directory.mkdirs()
        }

        Log.i(TAG, "Starting to watch directory: $RECORDINGS_DIR")
        
        // Watch for CLOSE_WRITE because it's only triggered when a file is fully written and closed.
        fileObserver = object : FileObserver(directory, CLOSE_WRITE) {
            override fun onEvent(event: Int, fileName: String?) {
                if (fileName != null) {
                    Log.i(TAG, "File event detected: $fileName, event: $event")
                    val fullFile = File(directory, fileName)
                    handleNewFile(fullFile)
                }
            }
        }
        fileObserver?.startWatching()
    }

    private val inspector = java.util.concurrent.Executors.newSingleThreadExecutor()

    /**
     * A new recording: long calls upload by themselves, short ones ask first,
     * skipped contacts are ignored. Duration/direction/number come from the
     * file and the call log ([RecordingInspector]).
     */
    private fun handleNewFile(file: File, recovering: Boolean = false) {
        val filename = file.name
        val callInfo = parseSamsungFilename(filename) ?: return
        // Remembered on disk, so restarts and rescans don't re-notify. PENDING
        // = spotted but not handled yet: if the app is killed in between (an
        // update, low memory) the next rescan picks it up again.
        val prior = CallUploadStore.state(this, filename)
        val retry = recovering && (prior == CallUploadStore.State.PENDING || prior == CallUploadStore.State.NOTIFIED)
        if (prior != null && !retry) return
        if (retry && System.currentTimeMillis() - file.lastModified() > RECOVER_WINDOW_MS) return
        if (prior == null) {
            CallUploadStore.mark(this, filename, CallUploadStore.State.PENDING, file.absolutePath, callInfo.contactName, callInfo.isoTimestamp)
        }
        if (!handling.add(filename)) return
        Log.i(TAG, "${if (retry) "Re-checking" else "New"} Samsung call recording: ${callInfo.contactName} at ${callInfo.isoTimestamp}")

        inspector.execute {
            try {
                RecordingInspector.awaitStable(file)
                val meta = RecordingInspector.inspect(this, file, callInfo.contactName, callInfo.isoTimestamp)
                when {
                    CallUploadSettings.isSkipped(this, meta.contact) -> {
                        CallUploadStore.mark(this, filename, CallUploadStore.State.SKIPPED)
                        Log.i(TAG, "Skipping ${meta.contact} (on the skip list)")
                    }
                    CallUploadSettings.autoUpload(this) && meta.durationSeconds >= CallUploadSettings.thresholdSeconds(this) -> {
                        Log.i(TAG, "Auto-uploading ${meta.contact} (${meta.durationSeconds}s, ${meta.direction})")
                        CallUploads.enqueue(this, meta)
                    }
                    // Already asked about it before the restart: don't ask twice.
                    prior == CallUploadStore.State.NOTIFIED -> Unit
                    CallUploadSettings.autoUpload(this) -> {
                        CallUploadStore.mark(this, filename, CallUploadStore.State.NOTIFIED)
                        CallUploads.notifyShortCall(this, meta)
                    }
                    else -> {
                        CallUploadStore.mark(this, filename, CallUploadStore.State.NOTIFIED)
                        showCallDetectedNotification(file, meta.contact, meta.timestamp)
                    }
                }
            } catch (e: Exception) {
                Log.e(TAG, "Handling $filename failed", e)
                CallUploadStore.mark(this, filename, CallUploadStore.State.NOTIFIED)
                showCallDetectedNotification(file, callInfo.contactName, callInfo.isoTimestamp)
            } finally {
                handling.remove(filename)
            }
        }
    }

    // Files being inspected right now (a file event and a rescan can race).
    private val handling = java.util.Collections.synchronizedSet(mutableSetOf<String>())

    private fun parseSamsungFilename(filename: String): CallInfo? {
        if (!filename.startsWith("Call") || !filename.endsWith(".m4a")) {
            return null
        }
        
        // Strip "Call" prefix and extension
        val nameAndDateTime = filename.substring(4, filename.length - 4).trim()
        
        val parts = nameAndDateTime.split("_")
        if (parts.size < 3) return null
        
        val timePart = parts.last()
        val datePart = parts[parts.size - 2]
        
        val rawName = parts.subList(0, parts.size - 2).joinToString("_")
        val cleanName = rawName
            .replace("\u200e", "")
            .replace("\u200f", "")
            .replace("\u202a", "")
            .replace("\u202b", "")
            .replace("\u202c", "")
            .replace("\u202d", "")
            .replace("\u202e", "")
            .trim()
            
        if (datePart.length != 6 || timePart.length != 6) return null
        val year = "20" + datePart.substring(0, 2)
        val month = datePart.substring(2, 4)
        val day = datePart.substring(4, 6)
        
        val hour = timePart.substring(0, 2)
        val minute = timePart.substring(2, 4)
        val second = timePart.substring(4, 6)
        
        val isoTimestamp = "$year-$month-${day}T$hour:$minute:$second"
        
        return CallInfo(cleanName, isoTimestamp)
    }

    private fun showCallDetectedNotification(file: File, contactName: String, timestamp: String) {
        val uploadIntent = Intent(this, CallRecordingObserverService::class.java).apply {
            action = UPLOAD_ACTION
            putExtra(EXTRA_FILE_PATH, file.absolutePath)
            putExtra(EXTRA_CONTACT_NAME, contactName)
            putExtra(EXTRA_TIMESTAMP, timestamp)
        }
        
        val pendingFlags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        } else {
            PendingIntent.FLAG_UPDATE_CURRENT
        }
        
        val uploadPendingIntent = PendingIntent.getService(
            this,
            file.name.hashCode(),
            uploadIntent,
            pendingFlags
        )
        
        val notificationId = file.name.hashCode()
        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_upload)
            .setContentTitle("New Call Recording Detected")
            .setContentText("Call with $contactName is ready to upload.")
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .addAction(
                android.R.drawable.ic_menu_upload,
                "Upload to Dana",
                uploadPendingIntent
            )
            .setAutoCancel(true)
            .build()
            
        val notificationManager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        notificationManager.notify(notificationId, notification)
    }

    /** Off the main thread: it touches every recording on the phone. */
    private fun rescanDirectory() {
        runCatching {
            scanner.execute {
                try { rescanNow() } catch (e: Exception) { Log.e(TAG, "Rescan failed", e) }
            }
        }
    }

    private val scanner = java.util.concurrent.Executors.newSingleThreadExecutor()

    private fun rescanNow() {
        val directory = File(RECORDINGS_DIR)
        if (!directory.exists()) return

        Log.i(TAG, "Rescanning directory: $RECORDINGS_DIR")
        val files = directory.listFiles { f -> f.name.endsWith(".m4a") }.orEmpty()
        if (!CallUploadStore.isSeeded(this)) {
            // First run: don't post a notification for every old recording.
            CallUploadStore.seed(this, files.map { it.name })
            return
        }
        files.forEach { handleNewFile(it, recovering = true) }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Call recordings",
                NotificationManager.IMPORTANCE_DEFAULT
            ).apply {
                description = "Handles call recording detection and upload progress"
            }
            val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            manager.createNotificationChannel(channel)
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        Log.d(TAG, "Service onDestroy")
        running = false
        fileObserver?.stopWatching()
        inspector.shutdown()
        scanner.shutdown()
    }

    private data class CallInfo(val contactName: String, val isoTimestamp: String)
}
