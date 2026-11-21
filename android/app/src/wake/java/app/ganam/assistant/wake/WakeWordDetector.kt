package app.ganam.assistant.wake

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import java.nio.FloatBuffer
import kotlin.random.Random

/**
 * Streaming openWakeWord (https://github.com/dscripka/openWakeWord), ported
 * from its Python `AudioFeatures` + `Model.predict`:
 *
 *  audio (1280 samples / 80 ms) → melspectrogram.onnx (x/10 + 2, 32 bins)
 *  → embedding_model.onnx over the last 76 mel frames (96-d, one per frame)
 *  → classifier (e.g. hey_dana.onnx) over the last N embeddings → score 0..1.
 *
 * Not thread-safe: feed it from one thread.
 */
class WakeWordDetector(context: Context, classifierAsset: String) : AutoCloseable {
    private val env = OrtEnvironment.getEnvironment()
    private val opts = OrtSession.SessionOptions().apply {
        setIntraOpNumThreads(1)
        setInterOpNumThreads(1)
    }
    private val mel = session(context, "wakeword/melspectrogram.onnx")
    private val embed = session(context, "wakeword/embedding_model.onnx")
    private val classifier = session(context, classifierAsset)

    private val melIn = mel.inputNames.first()
    private val embedIn = embed.inputNames.first()
    private val clsIn = classifier.inputNames.first()
    /** Embedding frames the classifier looks at (16 for standard openWakeWord models). */
    private val clsFrames: Int = (classifier.inputInfo[clsIn]!!.info as ai.onnxruntime.TensorInfo).shape[1].toInt()

    private val raw = ShortRing(SAMPLE_RATE * 10)
    private val melFrames = ArrayDeque<FloatArray>()
    private val features = ArrayDeque<FloatArray>()
    private var predictions = 0

    init {
        reset()
    }

    /** Clear history (e.g. after the overlay closes) so stale audio can't trigger. */
    fun reset() {
        raw.clear()
        melFrames.clear()
        repeat(WINDOW) { melFrames.addLast(FloatArray(MEL_BINS) { 1f }) }
        features.clear()
        // Same warm-up as the reference: embeddings of 4 s of low-level noise.
        val noise = ShortArray(SAMPLE_RATE * 4) { Random.nextInt(-1000, 1000).toShort() }
        val spec = melspectrogram(noise, 0, noise.size)
        var i = 0
        while (i + WINDOW <= spec.size) {
            features.addLast(embedding(spec, i))
            i += 8
        }
        trimFeatures()
        predictions = 0
    }

    /** Feed one 80 ms frame (1280 samples); returns the wake-word score. */
    fun process(frame: ShortArray): Float {
        require(frame.size == MicHub.FRAME) { "frames must be ${MicHub.FRAME} samples" }
        raw.addAll(frame)
        if (raw.size >= 400) {
            // Mel over the new samples plus 3 hops of context, as in the reference.
            val n = minOf(raw.size, MicHub.FRAME + 160 * 3)
            val window = raw.last(n)
            for (f in melspectrogram(window, 0, window.size)) melFrames.addLast(f)
            while (melFrames.size > MEL_MAX) melFrames.removeFirst()
            if (melFrames.size >= WINDOW) {
                val spec = melFrames.toList().takeLast(WINDOW)
                features.addLast(embedding(spec, 0))
                trimFeatures()
            }
        }
        predictions++
        if (predictions <= 5) return 0f // the reference ignores the first 5 frames
        return classify()
    }

    private fun trimFeatures() {
        while (features.size > FEATURE_MAX) features.removeFirst()
    }

    private fun melspectrogram(pcm: ShortArray, from: Int, len: Int): List<FloatArray> {
        val input = FloatArray(len) { pcm[from + it].toFloat() }
        OnnxTensor.createTensor(env, FloatBuffer.wrap(input), longArrayOf(1, len.toLong())).use { t ->
            mel.run(mapOf(melIn to t)).use { out ->
                val tensor = out[0] as OnnxTensor
                val shape = tensor.info.shape // [1, 1, frames, 32]
                val frames = shape[shape.size - 2].toInt()
                val buf = tensor.floatBuffer
                return List(frames) { f -> FloatArray(MEL_BINS) { b -> buf.get(f * MEL_BINS + b) / 10f + 2f } }
            }
        }
    }

    private fun embedding(spec: List<FloatArray>, start: Int): FloatArray {
        val input = FloatArray(WINDOW * MEL_BINS)
        for (f in 0 until WINDOW) System.arraycopy(spec[start + f], 0, input, f * MEL_BINS, MEL_BINS)
        OnnxTensor.createTensor(env, FloatBuffer.wrap(input), longArrayOf(1, WINDOW.toLong(), MEL_BINS.toLong(), 1)).use { t ->
            embed.run(mapOf(embedIn to t)).use { out ->
                val buf = (out[0] as OnnxTensor).floatBuffer
                return FloatArray(EMBED_DIM) { buf.get(it) }
            }
        }
    }

    private fun classify(): Float {
        val take = features.toList().takeLast(clsFrames)
        val input = FloatArray(clsFrames * EMBED_DIM)
        take.forEachIndexed { i, f -> System.arraycopy(f, 0, input, i * EMBED_DIM, EMBED_DIM) }
        OnnxTensor.createTensor(env, FloatBuffer.wrap(input), longArrayOf(1, clsFrames.toLong(), EMBED_DIM.toLong())).use { t ->
            classifier.run(mapOf(clsIn to t)).use { out ->
                return (out[0] as OnnxTensor).floatBuffer.get(0)
            }
        }
    }

    private fun session(context: Context, asset: String): OrtSession {
        val bytes = context.assets.open(asset).use { it.readBytes() }
        return env.createSession(bytes, opts)
    }

    override fun close() {
        classifier.close()
        embed.close()
        mel.close()
        opts.close()
    }

    /** Fixed-capacity ring of PCM samples. */
    private class ShortRing(private val capacity: Int) {
        private val data = ShortArray(capacity)
        private var head = 0
        var size = 0
            private set

        fun clear() { head = 0; size = 0 }

        fun addAll(src: ShortArray) {
            for (s in src) {
                data[(head + size) % capacity] = s
                if (size < capacity) size++ else head = (head + 1) % capacity
            }
        }

        fun last(n: Int): ShortArray {
            val out = ShortArray(n)
            val start = head + size - n
            for (i in 0 until n) out[i] = data[(start + i) % capacity]
            return out
        }
    }

    companion object {
        private const val SAMPLE_RATE = 16000
        private const val MEL_BINS = 32
        private const val WINDOW = 76
        private const val EMBED_DIM = 96
        private const val MEL_MAX = 10 * 97
        private const val FEATURE_MAX = 120
    }
}
