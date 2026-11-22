package io.github.ronganam.dana.data.signals

import android.app.AppOpsManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.os.Build
import android.os.Process
import android.util.Log

/**
 * The screen's on / off history from UsageStatsManager (Usage access, Android
 * 9+: `SCREEN_INTERACTIVE` / `SCREEN_NON_INTERACTIVE`), read since the last
 * read and sent as "screen" stretches ([ScreenStretches]). Nothing about
 * which apps were used is read or sent.
 */
object ScreenTime {
    private const val TAG = "ScreenTime"

    val supported: Boolean get() = Build.VERSION.SDK_INT >= 28

    fun hasAccess(context: Context): Boolean {
        val ops = context.getSystemService(AppOpsManager::class.java) ?: return false
        // Deprecated on Android 10–15 for unsafeCheckOpNoThrow, which Android 16 deprecates back in its favour.
        @Suppress("DEPRECATION")
        val mode = ops.checkOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), context.packageName)
        return mode == AppOpsManager.MODE_ALLOWED
    }

    /** Read the history since the last read and queue its stretches ([now]: send them at once). */
    @Synchronized
    fun read(context: Context, now: Boolean, at: Long = System.currentTimeMillis()) {
        if (!supported || !SignalSettings.screenTime(context) || !hasAccess(context)) return
        val usage = context.getSystemService(UsageStatsManager::class.java) ?: return
        val (lastRead, open) = SignalSettings.screenRead(context)
        // Never further back than the server takes; a first read starts now.
        val from = maxOf(if (lastRead > 0) lastRead else at, at - SignalQueue.MAX_AGE_MS)
        val changes = mutableListOf<ScreenChange>()
        try {
            val events = usage.queryEvents(from, at)
            val e = UsageEvents.Event()
            while (events.hasNextEvent()) {
                events.getNextEvent(e)
                when (e.eventType) {
                    UsageEvents.Event.SCREEN_INTERACTIVE -> changes += ScreenChange(true, e.timeStamp)
                    UsageEvents.Event.SCREEN_NON_INTERACTIVE -> changes += ScreenChange(false, e.timeStamp)
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't read the screen history", e)
            return
        }
        val read = ScreenStretches.read(changes, open?.coerceAtLeast(from), at)
        SignalSync.add(context, read.stretches, now)
        SignalSettings.setScreenRead(context, at, read.openSince)
        Log.i(TAG, "${changes.size} screen changes → ${read.stretches.size} stretches")
    }
}
