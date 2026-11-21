package io.github.ronganam.dana.chat

import io.github.ronganam.dana.data.chat.ThreadModel
import io.github.ronganam.dana.data.models.ChatMessage
import io.github.ronganam.dana.data.network.ChatApi
import io.github.ronganam.dana.ui.viewmodels.ChatTranscript
import io.github.ronganam.dana.ui.viewmodels.ThreadView
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Paging, the gap and the fresh view of the shared thread ([ThreadView]). */
class ThreadViewTest {
    private val transcript = ChatTranscript(persist = {}, persistSoon = {})
    private var cached: List<ChatMessage> = emptyList()
    private val view = ThreadView(transcript) { cached = it }

    private fun msg(id: String, text: String, ts: Long, user: Boolean = true) =
        """{"id": "$id", "sender": "${if (user) "You" else "Assistant"}", "message": "$text", "isUser": $user, "ts": ${ts / 1000.0}}"""

    private fun page(vararg messages: String, more: Boolean = false, before: String? = null, extra: String = "") = ChatApi.parseState(
        """{"status": "idle", "messages": [${messages.joinToString()}], "has_more": $more, "before": ${before?.let { "\"$it\"" } ?: "null"}, "gap_minutes": 45$extra}"""
    )

    @Test
    fun newestPageWithOlderPagesBehindIt() {
        val now = System.currentTimeMillis()
        val p = page(msg("m3", "three", now - 3_000), msg("m4", "four", now - 2_000, user = false), more = true, before = "c3")
        view.applyPage(p, p.messages)
        assertEquals(listOf("m3", "m4"), transcript.value.map { it.id })
        assertEquals(45.0, view.gapMinutes.value, 0.0)
        assertTrue(view.hasOlder.value)
        assertEquals("c3", view.olderToLoad())
        assertEquals(transcript.value, cached)

        view.setLoadingOlder(true)
        assertNull("one page at a time", view.olderToLoad())
        view.prependOlder(page(msg("m1", "one", now - 9_000), msg("m2", "two", now - 8_000)))
        view.setLoadingOlder(false)
        assertEquals(listOf("m1", "m2", "m3", "m4"), transcript.value.map { it.id })
        assertFalse(view.hasOlder.value)
        assertNull(view.olderToLoad())
    }

    @Test
    fun aFreshViewHidesWhatWasSaidBefore() {
        val now = System.currentTimeMillis()
        view.startFresh()
        val p = page(msg("old", "earlier", now - 60_000), msg("new", "just now", now + 1_000), more = true, before = "c1")
        view.applyPage(p, p.messages)
        assertEquals(listOf("new"), transcript.value.map { it.id })
        assertFalse("older pages are not offered in a fresh view", view.hasOlder.value)

        view.showAll()
        view.applyPage(p, p.messages)
        assertEquals(listOf("old", "new"), transcript.value.map { it.id })
    }

    @Test
    fun aLearnedPhraseMarksTheLastReply() {
        val now = System.currentTimeMillis()
        val p = page(msg("u", "lights off", now - 2_000), msg("a", "Done.", now - 1_000, user = false), extra = """, "shortcut": true""")
        view.applyPage(p, p.messages)
        assertTrue(transcript.value.last().shortcut)
        assertFalse(transcript.value.first().shortcut)
    }

    @Test
    fun theTypingIndicatorStaysLast() {
        transcript.showThinking("Thinking…")
        val p = page(msg("u", "hi", System.currentTimeMillis()))
        view.applyPage(p, p.messages)
        assertEquals(listOf(false, true), transcript.value.map { it.isThinking })
        assertEquals(ThreadModel.Memory.Unknown, view.memory.value)
    }
}
