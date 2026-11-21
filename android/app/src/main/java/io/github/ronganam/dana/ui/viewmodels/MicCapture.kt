package io.github.ronganam.dana.ui.viewmodels

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import io.github.ronganam.dana.data.voice.PcmStream
import io.github.ronganam.dana.data.voice.VoiceInput
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.isActive

/**
 * The microphone for push-to-talk: 16 kHz mono PCM16 in 80 ms chunks (from
 * [VoiceInput.source] when set, else [AudioRecord]), with the end of speech
 * found by loudness ([VoiceInput] settings).
 */
internal class MicCapture {
    private var audioRecord: AudioRecord? = null
    @Volatile private var pcmStream: PcmStream? = null

    /** Something louder than the room was said in this utterance. */
    @Volatile var heardSpeech = false
        private set

    fun reset() {
        heardSpeech = false
    }

    /**
     * Read chunks into [onChunk] while [keepGoing]; returns true when silence
     * ended the utterance (the caller stops it), false when stopped otherwise.
     */
    @SuppressLint("MissingPermission")
    suspend fun capture(keepGoing: () -> Boolean, onChunk: (ByteArray) -> Unit): Boolean {
        val stream = VoiceInput.source?.invoke() ?: openAudioRecordStream()
        pcmStream = stream
        try {
            val buffer = ByteArray(CHUNK_BYTES)
            var lastSpokenTime = System.currentTimeMillis()
            val silenceThreshold = VoiceInput.silenceRms
            val maxSilenceDuration = VoiceInput.endOfSpeechMs
            // Speech starts after a run of loud frames, not one: a single loud frame
            // (a click, the chime's tail, boosted room noise on the TV) sent near-silence
            // to the server, and the transcriber made up a sentence from it.
            var loudRun = 0

            while (currentCoroutineContext().isActive && keepGoing()) {
                val read = stream.read(buffer)
                if (read <= 0) {
                    if (pcmStream !== stream) break
                    continue
                }
                onChunk(buffer.copyOfRange(0, read))

                val rms = rms(buffer, read)
                val now = System.currentTimeMillis()
                if (now < VoiceInput.speechGateUntil) {
                    lastSpokenTime = now
                } else if (rms > silenceThreshold) {
                    loudRun++
                    if (heardSpeech || loudRun >= VoiceInput.speechStartFrames) {
                        lastSpokenTime = now
                        heardSpeech = true
                    }
                } else {
                    loudRun = 0
                    val limit = if (heardSpeech) maxSilenceDuration else VoiceInput.noSpeechTimeoutMs
                    if (now - lastSpokenTime > limit) {
                        Log.d(TAG, "Silence detected, auto-stopping voice recording.")
                        return true
                    }
                }
            }
            return false
        } finally {
            // Also covers a stop that raced ahead of `pcmStream = stream`.
            if (pcmStream === stream) pcmStream = null
            runCatching { stream.close() }
        }
    }

    private fun rms(buffer: ByteArray, read: Int): Double {
        var sum = 0.0
        val shortsCount = read / 2
        for (i in 0 until shortsCount) {
            val low = buffer[i * 2].toInt() and 0xFF
            val high = buffer[i * 2 + 1].toInt()
            val sample = ((high shl 8) or low).toShort()
            sum += sample * sample
        }
        return if (shortsCount > 0) Math.sqrt(sum / shortsCount) else 0.0
    }

    @SuppressLint("MissingPermission")
    private fun openAudioRecordStream(): PcmStream {
        val channelConfig = AudioFormat.CHANNEL_IN_MONO
        val audioFormat = AudioFormat.ENCODING_PCM_16BIT
        val minBufferSize = AudioRecord.getMinBufferSize(SAMPLE_RATE, channelConfig, audioFormat)
        val recorder = AudioRecord(MediaRecorder.AudioSource.MIC, SAMPLE_RATE, channelConfig, audioFormat, maxOf(minBufferSize, CHUNK_BYTES))
        audioRecord = recorder
        recorder.startRecording()
        return object : PcmStream {
            override fun read(buffer: ByteArray): Int = recorder.read(buffer, 0, buffer.size)
            override fun close() = Unit // released by release()
        }
    }

    /** Stop and free the microphone (any thread). */
    fun release() {
        pcmStream?.let { stream ->
            pcmStream = null
            try { stream.close() } catch (e: Exception) { Log.w(TAG, "Error closing PCM stream", e) }
        }
        try {
            audioRecord?.stop()
            audioRecord?.release()
        } catch (e: Exception) {
            Log.e(TAG, "Error releasing AudioRecord", e)
        } finally {
            audioRecord = null
        }
    }

    companion object {
        private const val TAG = "MicCapture"
        const val SAMPLE_RATE = 16000
        /** 80 ms at 16 kHz PCM16. */
        private const val CHUNK_BYTES = 2560
    }
}
