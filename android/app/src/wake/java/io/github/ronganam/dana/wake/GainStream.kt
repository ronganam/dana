package io.github.ronganam.dana.wake

import android.util.Log
import io.github.ronganam.dana.data.voice.PcmStream
import kotlin.math.sqrt

/**
 * The voice turn's audio with a fixed gain (PCM16 little endian, clipped).
 *
 * Far-field mics differ a lot: the TV's sits at RMS ~6 in a quiet room, the
 * tablet's at ~200. Without gain the TV's speech never crossed the speech
 * threshold, so every turn ended as "no speech", and Gemini got very quiet
 * audio. The gain brings the room floor to about [TARGET_FLOOR]; the speech
 * threshold is then set on the boosted signal (see [AssistantSession]).
 */
class GainStream(
    private val inner: PcmStream,
    val gain: Double,
    private val floor: Double,
    private val threshold: Double,
) : PcmStream {
    private val levels = ArrayList<Int>()

    override fun read(buffer: ByteArray): Int {
        val n = inner.read(buffer)
        if (n <= 0) return n
        var sum = 0.0
        var i = 0
        while (i + 1 < n) {
            val s = (buffer[i].toInt() and 0xFF) or (buffer[i + 1].toInt() shl 8)
            val boosted = (s.toShort() * gain).toInt().coerceIn(Short.MIN_VALUE.toInt(), Short.MAX_VALUE.toInt())
            buffer[i] = boosted.toByte()
            buffer[i + 1] = (boosted shr 8).toByte()
            sum += boosted.toDouble() * boosted
            i += 2
        }
        levels += sqrt(sum / (n / 2).coerceAtLeast(1)).toInt()
        return n
    }

    override fun close() {
        inner.close()
        // Level distribution of the turn (after gain), for tuning the speech threshold.
        val sorted = levels.sorted()
        fun pct(p: Int) = if (sorted.isEmpty()) 0 else sorted[(sorted.size - 1) * p / 100]
        var run = 0; var longest = 0
        for (l in levels) { run = if (l > threshold) run + 1 else 0; longest = maxOf(longest, run) }
        Log.i("DanaPerf", "voice turn audio: %d reads, room %.0f, gain %.1f, threshold %.0f, p50 %d p90 %d max %d, above %d, longest run %d"
            .format(levels.size, floor, gain, threshold, pct(50), pct(90), pct(100), levels.count { it > threshold }, longest))
        Log.i("DanaPerf", "voice turn levels: " + levels.joinToString(" "))
    }

    companion object {
        /** Room floor after gain; speech counts above [speechRatio]× this. */
        const val TARGET_FLOOR = 120.0
        const val MAX_GAIN = 12.0
        /**
         * Speech threshold = this × the boosted room floor (min [MIN_THRESHOLD]).
         * The TV sets 1.6: its silence measures 25–40 after gain and quiet speech
         * 65–100, so 2.5× dropped quiet requests as "no speech".
         */
        @Volatile var speechRatio = 2.5
        @Volatile var minThreshold = 60.0

        /** Speech threshold on the boosted signal for a room at [floor] (raw). */
        fun thresholdFor(floor: Double): Double = (floor * gainFor(floor) * speechRatio).coerceIn(minThreshold, 1500.0)

        fun gainFor(floor: Double): Double = (TARGET_FLOOR / floor.coerceAtLeast(1.0)).coerceIn(1.0, MAX_GAIN)
    }
}
