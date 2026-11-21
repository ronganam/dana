package io.github.ronganam.dana.data.chat

import io.github.ronganam.dana.data.models.ChatMessage
import io.github.ronganam.dana.data.network.text
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.Locale

/** One reversible thing Dana just did (`undos: [{id, label}]` on `tool_end` / the closing `state`). */
data class UndoAction(val id: String, val label: String)

/** An undo as the thread shows it: a button until pressed, then what happened. */
data class UndoItem(
    val id: String,
    val label: String,
    val status: Status = Status.READY,
    /** The server's reason when the undo was refused. */
    val error: String? = null,
    /** When it was pressed (epoch ms); outcomes stay until a newer turn starts. */
    val at: Long = 0L
) {
    enum class Status { READY, BUSY, DONE, TOO_LATE, FAILED }
}

/**
 * Pure rules of the shared thread (`session_id: "main"`, server
 * `docs/agent-actions.md`): merging pages, the quiet separators between
 * conversations, and which Undo buttons are still meaningful.
 */
object ThreadModel {
    const val MAIN = "main"

    private val SEQ = Regex("""^msg_(?:user|assistant|tool)_(\d+)""")

    /** The server row of a thread message id (`msg_user_12`, `msg_tool_13_<call>`); null for ids made on the phone. */
    fun seqOf(id: String): Int? = SEQ.find(id)?.groupValues?.get(1)?.toIntOrNull()

    /**
     * The newest page (oldest first) over what is shown. Older server messages
     * that come before the page stay; from the page's first row on, the page
     * wins — that replaces the phone's live bubbles with the stored messages
     * (stable ids, times, devices). An empty page changes nothing.
     */
    fun mergeNewest(current: List<ChatMessage>, page: List<ChatMessage>): List<ChatMessage> {
        if (page.isEmpty()) return current
        val firstSeq = page.firstNotNullOfOrNull { seqOf(it.id) } ?: return page
        val older = current.filter { m -> seqOf(m.id)?.let { it < firstSeq } == true }
        return carryLiveDetails(current, older + page)
    }

    /** An older page goes on top; what is already shown (or newer) is not repeated. */
    fun prependOlder(current: List<ChatMessage>, older: List<ChatMessage>): List<ChatMessage> {
        val have = current.mapTo(HashSet()) { it.id }
        val firstSeq = current.firstNotNullOfOrNull { seqOf(it.id) }
        val add = older.filter { m ->
            m.id !in have && (firstSeq == null || (seqOf(m.id) ?: Int.MAX_VALUE) < firstSeq)
        }
        return add + current
    }

    /**
     * Stored messages carry no thought summary and no shortcut flag, so a
     * reply that just streamed would lose its "thinking" block when the
     * stored copy replaces it. Copy them over from the live bubble with the
     * same text.
     */
    private fun carryLiveDetails(before: List<ChatMessage>, after: List<ChatMessage>): List<ChatMessage> {
        val withScreen = carryScreen(before, after)
        val live = before.filter { !it.isUser && it.toolCall == null && (it.reasoning.isNotEmpty() || it.shortcut) && it.message.isNotBlank() }
        if (live.isEmpty()) return withScreen
        return carryReplies(live, withScreen)
    }

    /**
     * The sheet's "Screen" caption on the message just sent (the stored copy
     * may not say a screen came with it): from the live newest user message to
     * the stored newest one with the same text.
     */
    private fun carryScreen(before: List<ChatMessage>, after: List<ChatMessage>): List<ChatMessage> {
        val src = before.lastOrNull { it.isUser } ?: return after
        if (src.screen.isEmpty()) return after
        val i = after.indexOfLast { it.isUser }
        if (i < 0 || after[i].screen.isNotEmpty() || after[i].message.trim() != src.message.trim()) return after
        return after.toMutableList().also { it[i] = it[i].copy(screen = src.screen) }
    }

    private fun carryReplies(live: List<ChatMessage>, after: List<ChatMessage>): List<ChatMessage> {
        val byText = live.associateBy { it.message.trim() }
        return after.map { m ->
            val src = if (!m.isUser && m.toolCall == null) byText[m.message.trim()] else null
            if (src == null) m else m.copy(
                reasoning = m.reasoning.ifEmpty { src.reasoning },
                shortcut = m.shortcut || src.shortcut
            )
        }
    }

    /** What a thread list shows: messages, with a quiet separator above each new conversation. */
    sealed class Row {
        abstract val key: String
        data class Separator(override val key: String, val label: String) : Row()
        /** [forgotten]: above the memory line — Dana no longer has it in mind (drawn dimmed). */
        data class Message(val msg: ChatMessage, val forgotten: Boolean = false) : Row() {
            override val key: String get() = msg.id
        }
        /** Where Dana's memory of the thread starts ("Dana remembers from here"). */
        data class MemoryLine(val label: String) : Row() {
            override val key: String get() = MEMORY_KEY
        }
    }

    const val MEMORY_KEY = "memory_line"
    const val REMEMBERS_FROM_HERE = "Dana remembers from here"
    const val STARTS_FRESH = "New topic — Dana starts fresh"

    /**
     * Which thread messages Dana still has in mind (the server's
     * `context_from_seq` / `context_expires_at` on every thread state).
     * [Unknown]: an older server that doesn't say — nothing is drawn.
     */
    sealed class Memory {
        object Unknown : Memory()
        /** [fromSeq]: the oldest remembered row, null = nothing; [expiresAtMs]: the quiet gap runs out (epoch ms). */
        data class Known(val fromSeq: Int?, val expiresAtMs: Long?) : Memory()

        /** The oldest remembered seq at [nowMs]; null once nothing is (cleared, empty, quiet too long). */
        fun fromSeqAt(nowMs: Long): Int? = when (this) {
            Unknown -> null
            is Known -> fromSeq?.takeIf { expiresAtMs == null || nowMs < expiresAtMs }
        }

        /** Dana remembers something, so "New topic" has something to forget. */
        fun remembersAt(nowMs: Long): Boolean = fromSeqAt(nowMs) != null
    }

    /** The memory fields of a thread state; [Memory.Unknown] when the server doesn't send them. */
    fun parseMemory(json: JsonObject): Memory {
        if (!json.containsKey("context_from_seq")) return Memory.Unknown
        fun number(key: String) = (json[key] as? JsonPrimitive)?.takeIf { it !is JsonNull }?.content?.toDoubleOrNull()
        val from = number("context_from_seq")?.toInt()
        val exp = number("context_expires_at")
        return Memory.Known(from, exp?.let { (it * 1000).toLong() })
    }

    /**
     * Where the memory line goes in [messages]: the index of the first
     * remembered message ([size] = after the last one), or null for no line —
     * unknown memory, nothing stored yet, or everything shown is remembered
     * (the memory starts at or before the first message shown).
     *
     * Remembering nothing (or the quiet gap ran out) puts it after the last
     * stored message: bubbles made on the phone since (just sent, streaming)
     * are what Dana works from now. The same when the remembered row isn't
     * among the loaded ones yet.
     */
    fun memoryIndex(messages: List<ChatMessage>, memory: Memory, nowMs: Long): Int? {
        if (memory !is Memory.Known) return null
        val lastStored = messages.indexOfLast { seqOf(it.id) != null }
        if (lastStored < 0) return null
        val afterStored = lastStored + 1
        val from = memory.fromSeqAt(nowMs) ?: return afterStored
        val i = messages.indexOfFirst { m -> seqOf(m.id)?.let { it >= from } == true }
        return when {
            i < 0 -> afterStored
            i == 0 -> null
            else -> i
        }
    }

    /**
     * Dana remembers messages older than what is shown (the power-button
     * sheet's fresh view hides them; an older page not loaded yet).
     */
    fun remembersEarlier(messages: List<ChatMessage>, memory: Memory, nowMs: Long): Boolean {
        val from = memory.fromSeqAt(nowMs) ?: return false
        val first = messages.firstNotNullOfOrNull { seqOf(it.id) } ?: return true
        return from < first
    }

    /**
     * [messages] with the memory line in place: messages above it are
     * marked [Row.Message.forgotten]. "Dana remembers from here" when
     * something follows it, "New topic — Dana starts fresh" at the bottom.
     */
    fun withMemory(rows: List<Row>, messages: List<ChatMessage>, memory: Memory, nowMs: Long): List<Row> {
        val at = memoryIndex(messages, memory, nowMs) ?: return rows
        val forgotten = messages.subList(0, at).mapTo(HashSet()) { it.id }
        val firstAfter = messages.drop(at).firstOrNull { !it.isThinking }?.id
        val label = if (firstAfter == null) STARTS_FRESH else REMEMBERS_FROM_HERE
        val out = ArrayList<Row>(rows.size + 1)
        var placed = false
        for (r in rows) {
            if (!placed && firstAfter != null) {
                // Above the first remembered message, and above its conversation separator.
                val startsHere = (r is Row.Message && r.msg.id == firstAfter) ||
                    (r is Row.Separator && r.key == "sep_$firstAfter")
                if (startsHere) { out += Row.MemoryLine(label); placed = true }
            }
            out += if (r is Row.Message && r.msg.id in forgotten) r.copy(forgotten = true) else r
        }
        if (!placed) {
            // Nothing remembered below: after the last forgotten row (a thinking bubble stays under it).
            val lastForgotten = out.indexOfLast { it is Row.Message && it.forgotten }
            out.add(lastForgotten + 1, Row.MemoryLine(label))
        }
        return out
    }

    /**
     * A separator goes above a user message that starts a conversation: the
     * first one shown, one after more than [gapMinutes] of quiet, or one from
     * another device. It says when and from where ("13:32 · watch").
     * Messages without a time (old caches) never get one.
     */
    fun rows(messages: List<ChatMessage>, gapMinutes: Double, now: Long, zone: ZoneId, memory: Memory = Memory.Unknown): List<Row> =
        withMemory(conversationRows(messages, gapMinutes, now, zone), messages, memory, now)

    private fun conversationRows(messages: List<ChatMessage>, gapMinutes: Double, now: Long, zone: ZoneId): List<Row> {
        val out = ArrayList<Row>(messages.size + 4)
        var prevTs: Long? = null
        var prevDevice = ""
        var first = true
        val gapMs = (gapMinutes * 60_000).toLong().coerceAtLeast(60_000)
        for (m in messages) {
            val ts = m.timestamp
            if (ts != null && m.isUser && !m.isThinking) {
                val newDevice = m.device.isNotEmpty() && prevDevice.isNotEmpty() && m.device != prevDevice
                val gap = prevTs != null && ts - prevTs > gapMs
                if (first || gap || newDevice) out += Row.Separator("sep_${m.id}", separatorLabel(ts, m.device, now, zone))
                first = false
            }
            out += Row.Message(m)
            if (ts != null) {
                prevTs = ts
                if (m.device.isNotEmpty()) prevDevice = m.device
            }
        }
        return out
    }

    private val HM = DateTimeFormatter.ofPattern("HH:mm", Locale.US)
    private val DOW = DateTimeFormatter.ofPattern("EEE", Locale.US)
    private val DAY_MONTH = DateTimeFormatter.ofPattern("d MMM", Locale.US)
    private val DAY_MONTH_YEAR = DateTimeFormatter.ofPattern("d MMM yyyy", Locale.US)

    /** "13:32 · watch", "Yesterday 08:19 · Mac", "Thu 21:04 · phone", "3 Nov 10:00". */
    fun separatorLabel(ts: Long, device: String, now: Long, zone: ZoneId): String {
        val at = Instant.ofEpochMilli(ts).atZone(zone)
        val today = Instant.ofEpochMilli(now).atZone(zone).toLocalDate()
        val day: LocalDate = at.toLocalDate()
        val days = ChronoUnit.DAYS.between(day, today)
        val time = at.format(HM)
        val whenText = when {
            days <= 0L -> time
            days == 1L -> "Yesterday $time"
            days < 7L -> "${at.format(DOW)} $time"
            day.year == today.year -> "${at.format(DAY_MONTH)} $time"
            else -> "${at.format(DAY_MONTH_YEAR)} $time"
        }
        val where = deviceLabel(device)
        return if (where.isEmpty()) whenText else "$whenText · $where"
    }

    /** How a device is named in the thread; unknown ones are left out. */
    fun deviceLabel(device: String): String = when (device.lowercase(Locale.US)) {
        "phone" -> "phone"
        "watch" -> "watch"
        "tablet" -> "tablet"
        "tv" -> "TV"
        "mac" -> "Mac"
        "web" -> "web"
        else -> ""
    }

    /** `undos: [{id, label}]`: entries without an id dropped, a missing label reads "Undo", one per id. */
    fun parseUndos(arr: JsonArray?): List<UndoAction> =
        arr.orEmpty().filterIsInstance<JsonObject>().mapNotNull { o ->
            val id = o.text("id")?.takeIf { it.isNotBlank() } ?: return@mapNotNull null
            UndoAction(id, o.text("label")?.takeIf { it.isNotBlank() } ?: "Undo")
        }.distinctBy { it.id }

    /**
     * The Undo row under the latest reply after the server named the latest
     * run's still-valid undos ([server]). Those show as buttons (keeping a
     * press in flight); an outcome ("Undone.") stays until a newer user
     * message than the press ([lastUserAt]); a button the server no longer
     * lists (used elsewhere, expired) goes.
     */
    fun reconcileUndos(current: List<UndoItem>, server: List<UndoAction>, lastUserAt: Long?): List<UndoItem> {
        val byId = current.associateBy { it.id }
        val listed = server.map { a ->
            val had = byId[a.id]
            if (had != null && had.status != UndoItem.Status.READY) had else UndoItem(a.id, a.label)
        }
        val listedIds = server.mapTo(HashSet()) { it.id }
        val outcomes = current.filter { it.id !in listedIds && it.status != UndoItem.Status.READY && it.status != UndoItem.Status.BUSY }
            .filter { lastUserAt == null || lastUserAt <= it.at }
        return outcomes + listed
    }

    /** Add the undos a `tool_end` reported while the run streams. */
    fun addUndos(current: List<UndoItem>, more: List<UndoAction>): List<UndoItem> {
        val have = current.mapTo(HashSet()) { it.id }
        return current + more.filter { it.id !in have }.map { UndoItem(it.id, it.label) }
    }
}
