package app.ganam.assistant.data.chat

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import app.ganam.assistant.data.models.ChatMessage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import java.io.File

/** A cached chat on disk, used for the offline session list. */
data class CachedSession(
    val id: String,
    val lastModified: Long,
    val snippet: String
)

/**
 * Local fallback cache of chat transcripts, one JSON file per session under
 * `filesDir/chat_history/`.
 *
 * The server is the source of truth. This cache only matters when the server
 * can't be reached. It used to live in `GanamPrefs` as one
 * `session_history_<id>` key per session, never pruned, in the same file as
 * the server config. SharedPreferences loads and rewrites that whole XML file
 * on every commit, so it kept growing and getting slower. Now only the newest
 * [maxSessions] transcripts are kept, and writes run off the main thread.
 */
class ChatHistoryStore(
    context: Context,
    private val scope: CoroutineScope,
    private val maxSessions: Int = 40
) {
    private val dir = File(context.applicationContext.filesDir, "chat_history")

    @OptIn(ExperimentalCoroutinesApi::class)
    private val writer = Dispatchers.IO.limitedParallelism(1)

    /** Cached messages for [sessionId], or null when nothing is cached. */
    fun load(sessionId: String): List<ChatMessage>? {
        val file = fileFor(sessionId) ?: return null
        if (!file.exists()) return null
        return try {
            ChatHistoryCodec.decode(file.readText())
        } catch (e: Exception) {
            Log.w(TAG, "Unreadable chat cache for $sessionId", e)
            null
        }
    }

    /** Persist [messages] for [sessionId] in the background (latest write wins). */
    fun save(sessionId: String, messages: List<ChatMessage>) {
        val file = fileFor(sessionId) ?: return
        val json = ChatHistoryCodec.encode(messages.filter { !it.isThinking })
        scope.launch(writer) {
            try {
                dir.mkdirs()
                val tmp = File(dir, "${file.name}.tmp")
                tmp.writeText(json)
                if (!tmp.renameTo(file)) {
                    file.writeText(json)
                    tmp.delete()
                }
                prune()
            } catch (e: Exception) {
                Log.w(TAG, "Failed to write chat cache for $sessionId", e)
            }
        }
    }

    /** Newest cached sessions first, for the offline session list. */
    fun listSessions(): List<CachedSession> {
        val files = dir.listFiles { f -> f.isFile && f.name.endsWith(".json") } ?: return emptyList()
        return files.sortedByDescending { it.lastModified() }.map { f ->
            val id = f.name.removeSuffix(".json")
            val snippet = try {
                ChatHistoryCodec.decode(f.readText())
                    .lastOrNull { it.message.isNotBlank() }?.message?.take(80)
            } catch (e: Exception) {
                null
            }
            CachedSession(id, f.lastModified(), snippet ?: "Empty chat")
        }
    }

    /**
     * One-time move of the old `session_history_*` prefs keys into files, and
     * removal of the unused `current_session_history` mirror.
     */
    fun migrateFromPrefs(prefs: SharedPreferences) {
        val legacy = prefs.all.filterKeys { it.startsWith(LEGACY_PREFIX) || it == LEGACY_CURRENT }
        if (legacy.isEmpty()) return
        scope.launch(writer) {
            try {
                dir.mkdirs()
                legacy.forEach { (key, value) ->
                    if (key == LEGACY_CURRENT || value !is String) return@forEach
                    val file = fileFor(key.removePrefix(LEGACY_PREFIX)) ?: return@forEach
                    if (!file.exists() && value.length > 2) file.writeText(value)
                }
                prune()
                prefs.edit().apply { legacy.keys.forEach { remove(it) } }.apply()
                Log.i(TAG, "Migrated ${legacy.size} chat caches out of SharedPreferences")
            } catch (e: Exception) {
                Log.w(TAG, "Chat cache migration failed", e)
            }
        }
    }

    private fun prune() {
        val files = dir.listFiles { f -> f.isFile && f.name.endsWith(".json") } ?: return
        if (files.size <= maxSessions) return
        files.sortedByDescending { it.lastModified() }.drop(maxSessions).forEach { it.delete() }
    }

    private fun fileFor(sessionId: String): File? {
        // Session ids are UUIDs. Refuse anything that could escape the directory.
        if (sessionId.isBlank() || !SAFE_ID.matches(sessionId)) return null
        return File(dir, "$sessionId.json")
    }

    companion object {
        private const val TAG = "ChatHistoryStore"
        private const val LEGACY_PREFIX = "session_history_"
        private const val LEGACY_CURRENT = "current_session_history"
        private val SAFE_ID = Regex("[A-Za-z0-9_-]{1,128}")
    }
}
