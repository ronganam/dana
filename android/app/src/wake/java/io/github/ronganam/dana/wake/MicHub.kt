package io.github.ronganam.dana.wake

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor
import android.util.Log
import io.github.ronganam.dana.data.voice.PcmStream
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlin.math.sqrt

/**
 * The single always-open microphone (kiosk and TV) (16 kHz mono PCM16, 80 ms frames).
 *
 * Every frame goes to the wake-word detector and to any open voice-turn
 * [PcmStream]. A short ring of recent frames lets a turn start from the moment
 * "Hey Dana" was detected, so words spoken straight after it aren't lost while
 * the overlay opens.
 */
object MicHub {
    private const val TAG = "MicHub"
    const val SAMPLE_RATE = 16000
    const val FRAME = 1280 // samples, 80 ms
    private const val RING_FRAMES = 40 // 3.2 s

    data class Level(val rms: Double, val noiseFloor: Double)

    private val _level = MutableStateFlow(Level(0.0, 300.0))
    val level: StateFlow<Level> = _level.asStateFlow()

    /** Frames for the wake-word thread; dropped (not blocked on) if it falls behind. */
    val detectorQueue = ArrayBlockingQueue<Frame>(50)

    class Frame(val index: Long, val pcm: ShortArray)

    private val ring = ArrayDeque<Frame>()
    private val streams = CopyOnWriteArrayList<HubStream>()
    @Volatile private var thread: Thread? = null
    @Volatile private var running = false
    @Volatile private var frameIndex = 0L
    @Volatile private var noiseFloor = 300.0

    val currentFrame: Long get() = frameIndex

    @Volatile private var preferredSource = MediaRecorder.AudioSource.VOICE_RECOGNITION
    @Volatile private var echoCancel = false
    @Volatile private var reopenRequested = false

    /** Which [MediaRecorder.AudioSource] to open, and whether to add the platform echo canceller. */
    fun configure(source: Int, aec: Boolean, reopen: Boolean) {
        val changed = source != preferredSource || aec != echoCancel
        preferredSource = source
        echoCancel = aec
        if (changed && reopen && running) reopenRequested = true
    }

    /** Debug builds only: audio that replaces the mic input until it runs out (see the "inject" intent extra). */
    private val injected = java.util.concurrent.ConcurrentLinkedQueue<ShortArray>()

    fun inject(pcm: ShortArray) {
        var i = 0
        while (i < pcm.size) {
            injected.add(ShortArray(FRAME) { j -> if (i + j < pcm.size) pcm[i + j] else 0 })
            i += FRAME
        }
    }

    @Synchronized
    fun start() {
        if (running) return
        running = true
        thread = Thread(::captureLoop, "dana-mic").apply {
            priority = Thread.MAX_PRIORITY
            start()
        }
    }

    @Synchronized
    fun stop() {
        running = false
        thread?.interrupt()
        thread = null
    }

    val isRunning: Boolean get() = running

    /**
     * A voice-turn stream starting at the frame after [fromFrame] (pre-roll
     * from the ring), or at the live edge when null.
     */
    fun openStream(fromFrame: Long?): PcmStream {
        val stream = HubStream()
        synchronized(ring) {
            if (fromFrame != null) ring.filter { it.index > fromFrame }.forEach { stream.queue.offer(it.pcm) }
            streams.add(stream)
        }
        return stream
    }

    @SuppressLint("MissingPermission")
    private fun captureLoop() {
        var backoffMs = 500L
        while (running) {
            val recorder = try {
                openRecorder()
            } catch (e: Exception) {
                Log.e(TAG, "Couldn't open the microphone", e)
                null
            }
            if (recorder == null) {
                sleepQuietly(backoffMs)
                backoffMs = (backoffMs * 2).coerceAtMost(10_000L)
                continue
            }
            backoffMs = 500L
            val effects = disableProcessing(recorder.audioSessionId)
            try {
                recorder.startRecording()
                Log.i(TAG, "Mic open (session ${recorder.audioSessionId})")
                val buf = ShortArray(FRAME)
                while (running && !reopenRequested) {
                    var filled = 0
                    while (filled < FRAME && running) {
                        val n = recorder.read(buf, filled, FRAME - filled)
                        if (n < 0) throw IllegalStateException("AudioRecord.read=$n")
                        filled += n
                    }
                    if (!running || reopenRequested) break
                    publish(injected.poll() ?: buf.copyOf())
                }
            } catch (e: Exception) {
                if (running) Log.w(TAG, "Mic capture stopped; reopening", e)
                sleepQuietly(500)
            } finally {
                reopenRequested = false
                effects.forEach { runCatching { it.release() } }
                runCatching { recorder.stop() }
                recorder.release()
            }
        }
        Log.i(TAG, "Mic closed")
    }

    @SuppressLint("MissingPermission")
    private fun openRecorder(): AudioRecord? {
        val min = AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val size = maxOf(min, FRAME * 2 * 8)
        // VOICE_RECOGNITION (default): tuned for ASR (no AGC/aggressive NS on most devices).
        // VOICE_COMMUNICATION engages the HAL's echo cancellation where a device has one (TVs).
        val sources = linkedSetOf(preferredSource, MediaRecorder.AudioSource.VOICE_RECOGNITION, MediaRecorder.AudioSource.MIC)
        for (source in sources) {
            val r = AudioRecord(source, SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, size)
            if (r.state == AudioRecord.STATE_INITIALIZED) {
                Log.i(TAG, "Mic source $source (preferred $preferredSource, aec=$echoCancel)")
                return r
            }
            r.release()
        }
        return null
    }

    /**
     * No audio effects on the mic session at all (the TV). On the TCL TV,
     * AudioFlinger tearing down a record session's effects (when the app dies or
     * the mic reopens) hangs while holding the input thread's lock, and the whole
     * audio stack locks up until a reboot.
     */
    @Volatile var effectsAllowed = true

    /** The wake-word model was trained on unprocessed audio; turn off AGC/NS where the platform added them. */
    private fun disableProcessing(sessionId: Int): List<android.media.audiofx.AudioEffect> {
        val out = mutableListOf<android.media.audiofx.AudioEffect>()
        if (!effectsAllowed) return out
        runCatching {
            if (AutomaticGainControl.isAvailable()) AutomaticGainControl.create(sessionId)?.let { it.enabled = false; out += it }
        }
        runCatching {
            if (NoiseSuppressor.isAvailable()) NoiseSuppressor.create(sessionId)?.let { it.enabled = false; out += it }
        }
        if (echoCancel) runCatching {
            if (AcousticEchoCanceler.isAvailable()) AcousticEchoCanceler.create(sessionId)?.let { it.enabled = true; out += it }
            else Log.w(TAG, "No acoustic echo canceller on this device")
        }
        return out
    }

    private fun publish(pcm: ShortArray) {
        val index = ++frameIndex
        val frame = Frame(index, pcm)

        var sum = 0.0
        for (s in pcm) sum += s.toDouble() * s
        val rms = sqrt(sum / pcm.size)
        // Fast to fall, slow to rise: tracks the room's background level.
        noiseFloor = if (rms < noiseFloor) noiseFloor * 0.8 + rms * 0.2 else noiseFloor * 0.997 + rms * 0.003
        noiseFloor = noiseFloor.coerceIn(2.0, 4000.0) // the TV's mic idles around 6
        _level.value = Level(rms, noiseFloor)

        synchronized(ring) {
            ring.addLast(frame)
            while (ring.size > RING_FRAMES) ring.removeFirst()
            for (s in streams) s.queue.offer(pcm)
        }
        if (!detectorQueue.offer(frame)) {
            detectorQueue.poll()
            detectorQueue.offer(frame)
        }
    }

    private fun sleepQuietly(ms: Long) {
        try { Thread.sleep(ms) } catch (_: InterruptedException) { Thread.currentThread().interrupt() }
    }

    private class HubStream : PcmStream {
        val queue = LinkedBlockingQueue<ShortArray>()
        @Volatile private var closed = false
        private var pending: ShortArray? = null
        private var pendingPos = 0

        override fun read(buffer: ByteArray): Int {
            var written = 0
            while (written + 1 < buffer.size) {
                if (closed) return if (written > 0) written else -1
                val frame = pending ?: run {
                    // Hand back what we have rather than waiting for a full buffer.
                    val next = if (written > 0) queue.poll() else queue.poll(250, TimeUnit.MILLISECONDS)
                    if (next == null) return if (written > 0) written else 0
                    pendingPos = 0
                    next.also { pending = it }
                }
                while (pendingPos < frame.size && written + 1 < buffer.size) {
                    val s = frame[pendingPos++].toInt()
                    buffer[written++] = (s and 0xFF).toByte()
                    buffer[written++] = (s shr 8 and 0xFF).toByte()
                }
                if (pendingPos >= frame.size) pending = null
            }
            return written
        }

        override fun close() {
            closed = true
            streams.remove(this)
            queue.clear()
        }
    }
}
