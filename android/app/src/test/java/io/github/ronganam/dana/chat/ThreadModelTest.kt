package io.github.ronganam.dana.chat

import io.github.ronganam.dana.data.chat.ThreadModel
import io.github.ronganam.dana.data.chat.UndoAction
import io.github.ronganam.dana.data.chat.UndoItem
import io.github.ronganam.dana.data.models.ChatMessage
import io.github.ronganam.dana.data.network.ApiJson
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime

class ThreadModelTest {
    private val zone = ZoneId.of("Asia/Jerusalem")
    private fun at(y: Int, mo: Int, d: Int, h: Int, mi: Int) = ZonedDateTime.of(y, mo, d, h, mi, 0, 0, zone).toInstant().toEpochMilli()
    private val now = at(2026, 11, 14, 18, 0)

    private fun user(seq: Int, text: String, ts: Long? = null, device: String = "phone") =
        ChatMessage(id = "msg_user_$seq", sender = "You", message = text, isUser = true, timestamp = ts, device = device)

    private fun dana(seq: Int, text: String, ts: Long? = null) =
        ChatMessage(id = "msg_assistant_$seq", sender = "Dana", message = text, isUser = false, timestamp = ts)

    @Test
    fun seqOfReadsServerIds() {
        assertEquals(12, ThreadModel.seqOf("msg_user_12"))
        assertEquals(13, ThreadModel.seqOf("msg_tool_13_call_abc"))
        assertNull(ThreadModel.seqOf("6f1c-local-uuid"))
    }

    @Test
    fun newestPageReplacesLiveBubblesAndKeepsOlderRows() {
        val older = listOf(user(1, "a"), dana(2, "b"))
        val live = listOf(
            ChatMessage(id = "local-1", sender = "You", message = "add milk", isUser = true),
            ChatMessage(id = "local-2", sender = "Dana", message = "Done.", isUser = false, reasoning = "thinking", shortcut = true)
        )
        val page = listOf(user(3, "add milk"), dana(4, "Done."))
        val merged = ThreadModel.mergeNewest(older + live, page)
        assertEquals(listOf("msg_user_1", "msg_assistant_2", "msg_user_3", "msg_assistant_4"), merged.map { it.id })
        // Stored messages have no thought summary or shortcut flag: carried over from the live bubble.
        assertEquals("thinking", merged.last().reasoning)
        assertTrue(merged.last().shortcut)
    }

    @Test
    fun emptyPageChangesNothing() {
        val shown = listOf(user(1, "a"))
        assertEquals(shown, ThreadModel.mergeNewest(shown, emptyList()))
    }

    @Test
    fun olderPageGoesOnTopWithoutRepeats() {
        val shown = listOf(user(5, "e"), dana(6, "f"))
        val older = listOf(user(3, "c"), dana(4, "d"), user(5, "e"))
        assertEquals(listOf("msg_user_3", "msg_assistant_4", "msg_user_5", "msg_assistant_6"), ThreadModel.prependOlder(shown, older).map { it.id })
    }

    @Test
    fun separatorsMarkNewConversations() {
        val t0 = at(2026, 11, 14, 13, 32)
        val messages = listOf(
            user(1, "hi", t0, "watch"),
            dana(2, "hello", t0 + 5_000),
            user(3, "and also", t0 + 60_000, "watch"),       // same conversation
            dana(4, "ok", t0 + 65_000),
            user(5, "later", t0 + 40 * 60_000, "watch"),     // > 20 min gap
            dana(6, "sure", t0 + 40 * 60_000 + 3_000),
            user(7, "from the phone", t0 + 41 * 60_000, "phone") // device changed
        )
        val rows = ThreadModel.rows(messages, 20.0, now, zone)
        val seps = rows.filterIsInstance<ThreadModel.Row.Separator>().map { it.label }
        assertEquals(listOf("13:32 · watch", "14:12 · watch", "14:13 · phone"), seps)
        // A separator sits right above the user message that starts the conversation.
        val i = rows.indexOfFirst { it is ThreadModel.Row.Separator && it.label == "14:12 · watch" }
        assertEquals("msg_user_5", (rows[i + 1] as ThreadModel.Row.Message).msg.id)
    }

    @Test
    fun messagesWithoutTimesGetNoSeparator() {
        val rows = ThreadModel.rows(listOf(user(1, "old cache"), dana(2, "reply")), 20.0, now, zone)
        assertTrue(rows.none { it is ThreadModel.Row.Separator })
    }

    @Test
    fun separatorLabels() {
        assertEquals("13:32 · watch", ThreadModel.separatorLabel(at(2026, 11, 14, 13, 32), "watch", now, zone))
        assertEquals("Yesterday 08:19 · Mac", ThreadModel.separatorLabel(at(2026, 11, 13, 8, 19), "mac", now, zone))
        assertEquals("Thu 21:04 · TV", ThreadModel.separatorLabel(at(2026, 11, 12, 21, 4), "tv", now, zone))
        assertEquals("3 Nov 10:00", ThreadModel.separatorLabel(at(2026, 11, 3, 10, 0), "telegram", now, zone))
        assertEquals("3 Nov 2025 10:00 · tablet", ThreadModel.separatorLabel(at(2025, 11, 3, 10, 0), "tablet", now, zone))
    }

    @Test
    fun parsesUndos() {
        val arr = obj("""[{"id":"a1","label":"Added milk to the shopping list"},{"id":"a1","label":"dupe"},{"label":"no id"},{"id":"b2"}]""") as JsonArray
        assertEquals(
            listOf(UndoAction("a1", "Added milk to the shopping list"), UndoAction("b2", "Undo")),
            ThreadModel.parseUndos(arr)
        )
        assertEquals(emptyList<UndoAction>(), ThreadModel.parseUndos(null))
    }

    @Test
    fun undosReconcileWithWhatTheServerStillLists() {
        val pressed = UndoItem("a1", "Added milk", UndoItem.Status.DONE, at = 1_000)
        val busy = UndoItem("b2", "Moved the reminder", UndoItem.Status.BUSY)
        val stale = UndoItem("c3", "Old button")
        val server = listOf(UndoAction("b2", "Moved the reminder"), UndoAction("d4", "Turned on the light"))

        val r = ThreadModel.reconcileUndos(listOf(pressed, busy, stale), server, lastUserAt = 500)
        // The outcome stays (no newer message), the press in flight is kept, the unlisted button goes.
        assertEquals(listOf("a1", "b2", "d4"), r.map { it.id })
        assertEquals(UndoItem.Status.BUSY, r[1].status)

        // A newer message clears old outcomes.
        val later = ThreadModel.reconcileUndos(listOf(pressed), emptyList(), lastUserAt = 2_000)
        assertTrue(later.isEmpty())
    }

    @Test
    fun addUndosSkipsKnownIds() {
        val cur = listOf(UndoItem("a1", "x"))
        val r = ThreadModel.addUndos(cur, listOf(UndoAction("a1", "x"), UndoAction("b2", "y")))
        assertEquals(listOf("a1", "b2"), r.map { it.id })
    }

    // --- Where Dana's memory starts ---

    private fun local(id: String, text: String, isUser: Boolean = true) =
        ChatMessage(id = id, sender = if (isUser) "You" else "Dana", message = text, isUser = isUser)

    private fun obj(text: String) = ApiJson.parseToJsonElement(text)

    private val thread = listOf(user(1, "a"), dana(2, "b"), user(3, "c"), dana(4, "d"))
    private fun known(from: Int?, expires: Long? = now + 60_000) = ThreadModel.Memory.Known(from, expires)

    private fun lineAndFaded(rows: List<ThreadModel.Row>): Pair<Int, List<String>> =
        rows.indexOfFirst { it is ThreadModel.Row.MemoryLine } to
            rows.filterIsInstance<ThreadModel.Row.Message>().filter { it.forgotten }.map { it.msg.id }

    @Test
    fun memoryParsesTheStateFields() {
        assertEquals(ThreadModel.Memory.Unknown, ThreadModel.parseMemory(obj("{}") as JsonObject))
        assertEquals(ThreadModel.Memory.Known(null, null), ThreadModel.parseMemory(obj("""{"context_from_seq":null,"context_expires_at":null}""") as JsonObject))
        assertEquals(ThreadModel.Memory.Known(7, 1_763_550_000_500L), ThreadModel.parseMemory(obj("""{"context_from_seq":7,"context_expires_at":1763550000.5}""") as JsonObject))
    }

    @Test
    fun lineGoesBeforeTheFirstRememberedMessage() {
        assertEquals(2, ThreadModel.memoryIndex(thread, known(3), now))
        val rows = ThreadModel.withMemory(thread.map { ThreadModel.Row.Message(it) }, thread, known(3), now)
        val (line, faded) = lineAndFaded(rows)
        assertEquals(2, line)
        assertEquals(ThreadModel.REMEMBERS_FROM_HERE, (rows[line] as ThreadModel.Row.MemoryLine).label)
        assertEquals(listOf("msg_user_1", "msg_assistant_2"), faded)
    }

    @Test
    fun aToolRowSeqStillFindsItsPlace() {
        // The remembered row may be one that isn't shown (a tool result): the next shown one starts it.
        assertEquals(2, ThreadModel.memoryIndex(thread, known(3), now))
        val withTool = listOf(user(1, "a"), dana(2, "b"), ChatMessage(id = "msg_tool_5_call", sender = "Dana", message = "", isUser = false), dana(6, "e"))
        assertEquals(2, ThreadModel.memoryIndex(withTool, known(4), now))
    }

    @Test
    fun noLineWhenEverythingShownIsRemembered() {
        assertNull(ThreadModel.memoryIndex(thread, known(1), now))
        assertNull(ThreadModel.memoryIndex(thread, known(0), now))   // starts on an earlier page
        assertTrue(ThreadModel.remembersEarlier(thread, known(0), now))
        assertTrue(!ThreadModel.remembersEarlier(thread, known(1), now))
    }

    @Test
    fun noLineFromAnOlderServer() {
        assertNull(ThreadModel.memoryIndex(thread, ThreadModel.Memory.Unknown, now))
        val rows = ThreadModel.rows(thread, 20.0, now, zone)
        assertTrue(rows.none { it is ThreadModel.Row.MemoryLine || (it is ThreadModel.Row.Message && it.forgotten) })
    }

    @Test
    fun rememberingNothingPutsTheLineAtTheBottom() {
        val rows = ThreadModel.rows(thread, 20.0, now, zone, known(null, null))
        assertTrue(rows.last() is ThreadModel.Row.MemoryLine)
        assertEquals(ThreadModel.STARTS_FRESH, (rows.last() as ThreadModel.Row.MemoryLine).label)
        assertEquals(4, rows.count { it is ThreadModel.Row.Message && it.forgotten })
    }

    @Test
    fun anExpiredGapCountsAsRememberingNothing() {
        val expiring = known(3, expires = now)
        assertEquals(2, ThreadModel.memoryIndex(thread, expiring, now - 1))
        assertEquals(4, ThreadModel.memoryIndex(thread, expiring, now))
        assertTrue(!expiring.remembersAt(now))
        assertTrue(expiring.remembersAt(now - 1))
        assertTrue(!ThreadModel.remembersEarlier(thread, expiring, now))
    }

    @Test
    fun aMessageSentSinceIsBelowTheLine() {
        // Cleared, then a new message (a phone bubble without a server id) and Dana thinking.
        val shown = thread + local("local-1", "new topic") + ChatMessage(id = "thinking", sender = "Dana", message = "", isUser = false, isThinking = true)
        assertEquals(4, ThreadModel.memoryIndex(shown, known(null, null), now))
        val rows = ThreadModel.withMemory(shown.map { ThreadModel.Row.Message(it) }, shown, known(null, null), now)
        val (line, faded) = lineAndFaded(rows)
        assertEquals(4, line)
        assertEquals(ThreadModel.REMEMBERS_FROM_HERE, (rows[line] as ThreadModel.Row.MemoryLine).label)
        assertEquals(thread.map { it.id }, faded)
    }

    @Test
    fun onlyThinkingBelowStillSaysStartsFresh() {
        val shown = thread + ChatMessage(id = "thinking", sender = "Dana", message = "", isUser = false, isThinking = true)
        val rows = ThreadModel.withMemory(shown.map { ThreadModel.Row.Message(it) }, shown, known(null, null), now)
        val line = rows.indexOfFirst { it is ThreadModel.Row.MemoryLine }
        assertEquals(4, line)
        assertEquals(ThreadModel.STARTS_FRESH, (rows[line] as ThreadModel.Row.MemoryLine).label)
        assertEquals("thinking", (rows.last() as ThreadModel.Row.Message).msg.id)
    }

    @Test
    fun rememberedRowNotLoadedYetGoesAfterTheStoredOnes() {
        // The state names a newer row than the page shows (it lands with the next merge).
        assertEquals(4, ThreadModel.memoryIndex(thread, known(9), now))
    }

    @Test
    fun nothingStoredMeansNoLine() {
        assertNull(ThreadModel.memoryIndex(emptyList(), known(null, null), now))
        assertNull(ThreadModel.memoryIndex(listOf(local("l1", "hi")), known(null, null), now))
        // The sheet's empty fresh view: Dana still has the last conversation in mind.
        assertTrue(ThreadModel.remembersEarlier(emptyList(), known(3), now))
        assertTrue(!ThreadModel.remembersEarlier(emptyList(), known(null, null), now))
    }

    @Test
    fun lineSitsAboveTheConversationSeparator() {
        val t0 = at(2026, 11, 14, 13, 0)
        val messages = listOf(user(1, "a", t0), dana(2, "b", t0 + 1_000), user(3, "c", t0 + 30 * 60_000), dana(4, "d", t0 + 30 * 60_000 + 1_000))
        val rows = ThreadModel.rows(messages, 20.0, now, zone, known(3))
        val line = rows.indexOfFirst { it is ThreadModel.Row.MemoryLine }
        assertTrue(rows[line + 1] is ThreadModel.Row.Separator)
        assertEquals("msg_user_3", (rows[line + 2] as ThreadModel.Row.Message).msg.id)
    }
}
