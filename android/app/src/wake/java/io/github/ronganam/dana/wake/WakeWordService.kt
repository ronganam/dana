package io.github.ronganam.dana.wake

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import io.github.ronganam.dana.R
import java.util.concurrent.TimeUnit

/**
 * Foreground service (type microphone) that keeps the mic open and runs the
 * "Hey Dana" detector. Android 9+ silences the mic for background apps, so
 * this keeps listening even if another app covers the home screen.
 *
 * Every detection is published on [WakeBus.wake]. A flavor whose assistant
 * lives in its own activity (the TV, where any app may be in front) names it
 * with `<meta-data android:name="io.github.ronganam.dana.wake.ACTIVITY">` on this
 * service; it is then started with the wake frame in [EXTRA_WAKE_FRAME].
 */
class WakeWordService : Service() {
    @Volatile private var running = false
    private var worker: Thread? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var wakeActivity: String? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        startAsForeground()
        wakeLock = (getSystemService(Context.POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Dana:wakeword")
            .apply { setReferenceCounted(false); acquire() }
        WakeBus.threshold = WakePrefs.threshold(this)
        wakeActivity = runCatching {
            packageManager.getServiceInfo(ComponentName(this, javaClass), PackageManager.GET_META_DATA)
                .metaData?.getString(META_ACTIVITY)
        }.getOrNull()
        MicHub.configure(WakePrefs.micSource(this), WakePrefs.echoCancel(this), reopen = false)
        MicHub.start()
        running = true
        worker = Thread(::detectLoop, "dana-wakeword").apply { start() }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    override fun onDestroy() {
        running = false
        WakeBus.setListening(false)
        worker?.interrupt()
        MicHub.stop()
        wakeLock?.let { if (it.isHeld) it.release() }
        super.onDestroy()
    }

    private fun detectLoop() {
        val detector = try {
            WakeWordDetector(this, WakePrefs.MODEL_ASSET)
        } catch (e: Exception) {
            Log.e(TAG, "Wake word model failed to load", e)
            WakeBus.setStatus("Wake word unavailable: ${e.message}")
            return
        }
        WakeBus.setStatus("Listening for “Hey Dana”")
        WakeBus.setListening(true)
        var lastWakeAt = 0L
        var peak = 0f
        var peakAt = 0L
        var above = 0
        var wasEnabled = true
        detector.use {
            while (running) {
                val frame = try {
                    MicHub.detectorQueue.poll(1, TimeUnit.SECONDS) ?: continue
                } catch (_: InterruptedException) {
                    break
                }
                val enabled = WakeBus.detectionEnabled
                if (WakeBus.resetRequested || (enabled && !wasEnabled)) {
                    WakeBus.resetRequested = false
                    detector.reset()
                    above = 0
                }
                wasEnabled = enabled
                if (!enabled) continue

                val score = try {
                    detector.process(frame.pcm)
                } catch (e: Exception) {
                    Log.e(TAG, "Detector error", e)
                    0f
                }
                val now = System.currentTimeMillis()
                if (score > peak || now - peakAt > 2000) { peak = score; peakAt = now; WakeBus.setScore(score) }

                above = if (score >= WakeBus.threshold) above + 1 else 0
                if (above >= PATIENCE && now - lastWakeAt > DEBOUNCE_MS) {
                    lastWakeAt = now
                    above = 0
                    OpenTimer.start("wake word")
                    Log.i(TAG, "Wake word detected (score=%.2f, frame=%d)".format(score, frame.index))
                    WakeBus.emitWake(frame.index)
                    wakeActivity?.let { launchAssistant(it, frame.index) }
                }
            }
        }
    }

    private fun launchAssistant(className: String, frame: Long) {
        try {
            startActivity(
                Intent().setClassName(this, className)
                    .putExtra(EXTRA_WAKE_FRAME, frame)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)
            )
        } catch (e: Exception) {
            Log.e(TAG, "Couldn't open the assistant", e)
        }
    }

    private fun startAsForeground() {
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, "Wake word", NotificationManager.IMPORTANCE_MIN)
            )
        }
        val open = PendingIntent.getActivity(
            this, 0,
            packageManager.getLeanbackLaunchIntentForPackage(packageName)
                ?: packageManager.getLaunchIntentForPackage(packageName) ?: Intent(),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val notification: Notification = Notification.Builder(this, CHANNEL)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("Dana is listening for “Hey Dana”")
            .setContentIntent(open)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    companion object {
        private const val TAG = "WakeWordService"
        private const val CHANNEL = "kiosk_wakeword" // id kept: existing installs already have it
        private const val NOTIFICATION_ID = 7301
        /** Consecutive 80 ms frames above threshold before firing. */
        private const val PATIENCE = 2
        private const val DEBOUNCE_MS = 2500L
        private const val META_ACTIVITY = "io.github.ronganam.dana.wake.ACTIVITY"
        const val EXTRA_WAKE_FRAME = "wake_frame"

        fun start(context: Context) {
            val intent = Intent(context, WakeWordService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) context.startForegroundService(intent)
            else context.startService(intent)
        }
    }
}
