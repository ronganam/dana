package io.github.ronganam.dana.data.sync

/**
 * Which items of a share (ACTION_SEND / ACTION_SEND_MULTIPLE) are audio files
 * to upload. Generic over the item type so the rules are tested without
 * Android: the activity passes `Uri`s and a lookup of each one's type.
 */
object SharedAudio {
    const val SEND = "android.intent.action.SEND"
    const val SEND_MULTIPLE = "android.intent.action.SEND_MULTIPLE"
    /** More than this in one share is cut (one confirmation screen per file). */
    const val MAX_FILES = 20

    /**
     * [single] is EXTRA_STREAM of a SEND, [multiple] of a SEND_MULTIPLE,
     * [clip] the ClipData items (some apps send only those). An item counts
     * when its own type is audio, or it has none and the share says audio.
     */
    fun <T> pick(
        action: String?,
        type: String?,
        single: T?,
        multiple: List<T>?,
        clip: List<T>,
        itemType: (T) -> String?
    ): List<T> {
        val items = when (action) {
            SEND -> listOfNotNull(single).ifEmpty { clip.take(1) }
            SEND_MULTIPLE -> multiple.orEmpty().ifEmpty { clip }
            else -> return emptyList()
        }
        val shareIsAudio = type?.startsWith("audio/") == true
        return items.distinct().filter { item ->
            val own = itemType(item)
            if (own.isNullOrBlank() || own == "application/octet-stream") shareIsAudio else own.startsWith("audio/")
        }.take(MAX_FILES)
    }
}
