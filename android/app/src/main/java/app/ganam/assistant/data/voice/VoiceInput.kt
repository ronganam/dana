package app.ganam.assistant.data.voice

/** 16 kHz mono PCM16 audio for one server-transcribed voice turn. */
interface PcmStream {
    /** Blocks until audio is available; returns bytes read, or <= 0 when closed. */
    fun read(buffer: ByteArray): Int
    fun close()
}

/**
 * Where push-to-talk audio comes from. Normally ChatController opens its own
 * AudioRecord; the kiosk build installs a [source] backed by the always-on
 * wake-word microphone, so a turn starts right after "Hey Dana" without
 * reopening the mic (and without dropping the first words).
 */
object VoiceInput {
    @Volatile
    var source: (() -> PcmStream)? = null

    /** RMS (PCM16) above which a frame counts as speech when auto-stopping a turn. */
    @Volatile
    var silenceRms: Double = 800.0

    /** Silence after speech that ends the turn. */
    @Volatile
    var endOfSpeechMs: Long = 2000L

    /**
     * Frames before this wall-clock time never count as speech (the kiosk's
     * "listening" chime and the pre-roll burst at the start of a turn).
     */
    @Volatile
    var speechGateUntil: Long = 0L

    /** How long to wait for the first words before giving up quietly. */
    @Volatile
    var noSpeechTimeoutMs: Long = 3500L

    /** Consecutive loud 80 ms frames that count as the start of speech. */
    @Volatile
    var speechStartFrames: Int = 1
}
