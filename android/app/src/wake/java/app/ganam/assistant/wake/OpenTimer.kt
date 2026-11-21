package app.ganam.assistant.wake

import android.os.SystemClock
import android.util.Log

/**
 * Times one assistant open, from "Hey Dana" (or the button) to the first
 * frame of the sheet and the first audio sent, in logcat under `DanaPerf`:
 * `adb logcat -s DanaPerf`.
 */
object OpenTimer {
    private const val TAG = "DanaPerf"
    @Volatile private var startedAt = 0L
    private val marked = HashSet<String>()

    fun start(cause: String, at: Long = SystemClock.uptimeMillis()) {
        synchronized(marked) { marked.clear() }
        startedAt = at
        Log.i(TAG, "open: $cause")
    }

    /** Logs [step] once per open, with the time since [start]. */
    fun mark(step: String) {
        val t0 = startedAt
        if (t0 == 0L) return
        if (!synchronized(marked) { marked.add(step) }) return
        Log.i(TAG, "+${SystemClock.uptimeMillis() - t0} ms $step")
    }
}
