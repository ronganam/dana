package app.ganam.assistant.wear.perf

import android.os.Process
import android.os.SystemClock
import android.util.Log

/**
 * Times one voice turn on the watch, from the press (assist button, app
 * launch or mic tap) to "listening", "transcript shown" and "reply text
 * shown", in logcat under `DanaPerf` (same tag as the phone/kiosk/TV
 * `OpenTimer`): `adb logcat -s DanaPerf`.
 *
 * Each line is `+<ms since the press> <step> (+<ms since the previous step>)`.
 */
object WearPerf {
    private const val TAG = "DanaPerf"
    @Volatile private var startedAt = 0L
    @Volatile private var lastAt = 0L
    private val marked = HashSet<String>()

    /** Starts a new turn. [at] is an [SystemClock.uptimeMillis] timestamp. */
    fun start(cause: String, at: Long = SystemClock.uptimeMillis()) {
        synchronized(marked) { marked.clear() }
        startedAt = at
        lastAt = at
        val processAge = SystemClock.uptimeMillis() - Process.getStartUptimeMillis()
        Log.i(TAG, "open: $cause (process age ${processAge} ms)")
    }

    /** True while a turn started less than [ms] ago. */
    fun startedWithin(ms: Long): Boolean =
        startedAt != 0L && SystemClock.uptimeMillis() - startedAt < ms

    /** Logs [step] once per turn, with the time since [start]. */
    fun mark(step: String) {
        val t0 = startedAt
        if (t0 == 0L) return
        if (!synchronized(marked) { marked.add(step) }) return
        val now = SystemClock.uptimeMillis()
        val sincePrev = now - lastAt
        lastAt = now
        Log.i(TAG, "+${now - t0} ms $step (+$sincePrev)")
    }

    /** Free-form detail line for the current turn (not deduplicated). */
    fun note(message: String) {
        Log.i(TAG, message)
    }
}
