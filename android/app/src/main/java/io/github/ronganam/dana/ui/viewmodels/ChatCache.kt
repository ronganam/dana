package io.github.ronganam.dana.ui.viewmodels

import android.content.SharedPreferences
import io.github.ronganam.dana.data.chat.ChatHistoryStore
import io.github.ronganam.dana.data.models.ChatMessage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import androidx.core.content.edit

/**
 * The on-device copy of the conversation ([ChatHistoryStore]): what is shown
 * is written on every settled change, and streaming changes are coalesced
 * into at most one write per second. The thread keeps only its newest
 * [THREAD_CACHE_MESSAGES] messages.
 */
internal class ChatCache(
    val store: ChatHistoryStore,
    private val prefs: SharedPreferences,
    private val scope: CoroutineScope,
    /** What is shown now. */
    private val messages: () -> List<ChatMessage>,
    /** The session shown, and whether it is the shared thread. */
    private val target: () -> Pair<String, Boolean>
) {
    private var pending = false
    private var job: Job? = null

    fun save(list: List<ChatMessage>) {
        val (sessionId, thread) = target()
        // The thread can be long; the cache only needs the recent part.
        store.save(sessionId, if (thread) list.takeLast(THREAD_CACHE_MESSAGES) else list)
        markActive()
    }

    fun markActive() {
        prefs.edit { putLong(KEY_LAST_ACTIVE, System.currentTimeMillis()) }
    }

    /** Coalesce streaming updates into at most one write per second. */
    fun saveSoon() {
        if (job?.isActive == true) {
            pending = true
            return
        }
        save(messages())
        pending = false
        job = scope.launch {
            while (pending) {
                delay(1000)
                pending = false
                save(messages())
            }
        }
    }

    /** Write any coalesced pending state immediately. */
    fun flush() {
        if (pending) {
            pending = false
            job?.cancel()
            job = null
            save(messages())
        }
    }

    companion object {
        /** Newest thread messages kept in the offline cache. */
        const val THREAD_CACHE_MESSAGES = 120
        const val KEY_LAST_ACTIVE = "last_active_timestamp"
    }
}
