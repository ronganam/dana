package io.github.ronganam.dana.data.sync

/** One file in the recordings folder, as the Storage Access Framework lists it. */
data class RecordingDoc(val uri: String, val name: String, val mime: String?, val modifiedMs: Long, val size: Long)

/**
 * Which files of the recordings folder a scan handles (pure, so the dedupe is
 * tested): the handled-recordings file ([CallUploadStore]) decides, by file name.
 */
object FolderScan {
    /** A file changed this recently may still be recording; the next scan takes it. */
    const val SETTLE_MS = 20_000L
    /** Recordings left half-handled (spotted, or asked about) by a killed app are retried for this long. */
    const val RECOVER_WINDOW_MS = 3L * 24 * 3600 * 1000

    private val AUDIO_EXTENSIONS = setOf("m4a", "mp3", "amr", "aac", "wav", "ogg", "opus", "3gp", "awb", "flac", "webm")

    /** An audio file, by its type or, when the provider gives none useful, its extension. */
    fun isAudio(name: String, mime: String?): Boolean =
        mime?.startsWith("audio/") == true ||
            ((mime == null || mime == "application/octet-stream" || mime.startsWith("video/3gpp")) &&
                name.substringAfterLast('.', "").lowercase() in AUDIO_EXTENSIONS)

    /**
     * [seed]: names to record as already seen (the first scan of a folder:
     * its old recordings are not news). [handle]: files to inspect now, with
     * whether it is a retry. [settling]: a file was too fresh, scan again soon.
     */
    data class Plan(val seed: List<String>, val handle: List<Pair<RecordingDoc, Boolean>>, val settling: Boolean)

    fun plan(docs: List<RecordingDoc>, seeded: Boolean, stateOf: (String) -> CallUploadStore.State?, nowMs: Long): Plan {
        val audio = docs.filter { isAudio(it.name, it.mime) }.distinctBy { it.name }
        val (fresh, settled) = audio.partition { nowMs - it.modifiedMs < SETTLE_MS }
        if (!seeded) return Plan(seed = settled.map { it.name }, handle = emptyList(), settling = fresh.isNotEmpty())
        val handle = settled.sortedBy { it.modifiedMs }.mapNotNull { doc ->
            when (stateOf(doc.name)) {
                null -> doc to false
                CallUploadStore.State.PENDING, CallUploadStore.State.NOTIFIED ->
                    if (nowMs - doc.modifiedMs <= RECOVER_WINDOW_MS) doc to true else null
                else -> null
            }
        }
        val settling = fresh.any { stateOf(it.name) == null }
        return Plan(seed = emptyList(), handle = handle, settling = settling)
    }
}
