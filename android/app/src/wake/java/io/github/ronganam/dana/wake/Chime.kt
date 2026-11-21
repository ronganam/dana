package io.github.ronganam.dana.wake

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import kotlin.math.PI
import kotlin.math.exp
import kotlin.math.sin

/** Short soft two-note chime: "I heard you" (rising) and "closing" (falling). */
object Chime {
    private const val RATE = 22050

    private val listen by lazy { render(listOf(784.0 to 0.09, 1175.0 to 0.16)) } // G5 → D6
    private val close by lazy { render(listOf(988.0 to 0.08, 659.0 to 0.14)) }   // B5 → E5

    fun listening() = play(listen)
    fun closing() = play(close)

    private fun render(notes: List<Pair<Double, Double>>): ShortArray {
        val out = ArrayList<Short>()
        for ((freq, dur) in notes) {
            val n = (RATE * dur).toInt()
            for (i in 0 until n) {
                val t = i.toDouble() / RATE
                val attack = (i / (RATE * 0.008)).coerceAtMost(1.0)
                val env = attack * exp(-t * 18)
                val v = (sin(2 * PI * freq * t) + 0.25 * sin(4 * PI * freq * t)) * env * 0.28
                out += (v * Short.MAX_VALUE).toInt().toShort()
            }
        }
        return out.toShortArray()
    }

    private fun play(pcm: ShortArray) {
        Thread {
            runCatching {
                val track = AudioTrack.Builder()
                    .setAudioAttributes(
                        AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_ASSISTANCE_SONIFICATION)
                            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                            .build()
                    )
                    .setAudioFormat(
                        AudioFormat.Builder()
                            .setSampleRate(RATE)
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                            .build()
                    )
                    .setTransferMode(AudioTrack.MODE_STATIC)
                    .setBufferSizeInBytes(pcm.size * 2)
                    .build()
                track.write(pcm, 0, pcm.size)
                track.play()
                Thread.sleep(pcm.size * 1000L / RATE + 100)
                track.release()
            }
        }.start()
    }
}
