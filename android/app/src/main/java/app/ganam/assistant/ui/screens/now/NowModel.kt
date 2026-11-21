package app.ganam.assistant.ui.screens.now

import app.ganam.assistant.data.models.NowAction
import app.ganam.assistant.data.models.NowBlock
import app.ganam.assistant.data.models.NowItem
import app.ganam.assistant.data.models.NowNotification
import app.ganam.assistant.data.models.NowRef
import app.ganam.assistant.data.models.QuietEntry
import app.ganam.assistant.ui.shell.DaySlot
import org.json.JSONArray
import org.json.JSONObject

/*
 * Pure rules of the Now queue (no Android, no Compose), tested in NowModelTest:
 * which buttons a row shows, what a swipe does, the args an action sends
 * (its own + the choices / checks the owner picked), the label the primary
 * button wears, which row is selected after one leaves, and the optimistic
 * removals that hide a row from the moment it is acted on until the server
 * agrees.
 */

/** Actions that take a card out of the queue without doing it ("the item's equivalent" of dismiss). */
private val DISMISS_LIKE = listOf("dismiss", "skip", "ignore")

/**
 * The owner's picks on one card: the chosen option per `choices` block and
 * the ticked options per `checks` block (by option index, keyed by the
 * block's `key`). Missing = the server's default (`selected` / `checked`).
 */
data class ItemDraft(
    val choices: Map<String, Int> = emptyMap(),
    val checks: Map<String, Set<Int>> = emptyMap()
)

/** What a row offers: the primary, up to two more inline, the rest folded under the evidence. */
data class RowActions(
    val primary: NowAction?,
    val inline: List<NowAction>,
    val more: List<NowAction>,
    /** What swiping left does (null: nothing). */
    val dismiss: NowAction?
)

/** The icon of a row's leading tile when it is not an app's own (see [NowModel.visual]). */
enum class NowGlyph { APPROVAL, ALERT, CALL, EVENT, REMINDER, CHECK, REVIEW, NOTIFICATION, SUGGESTION, MONEY }

/**
 * What a row's leading tile shows: an app's real icon ([App]; [pkg] is null
 * when only the app's name is known — the tile finds the package by name and
 * falls back to [fallback]) or the kind's own glyph.
 */
sealed class NowVisual {
    data class App(val pkg: String?, val name: String, val fallback: NowGlyph) : NowVisual()
    data class Glyph(val glyph: NowGlyph) : NowVisual()
}

object NowModel {

    /**
     * The leading tile of a queue row or a quieter suggestion. Anything that
     * came from an app's notification wears that app's icon (the exact
     * package when the item carries the notification, else the app name that
     * starts the server's `source`: "Slack · 2 h ago"); the rest wear the
     * glyph of what they are. Calls are a phone, not an app.
     */
    fun visual(
        kind: String,
        ref: NowRef?,
        source: String,
        notification: NowNotification?,
        primary: NowAction?,
        hasTriggerChoice: Boolean = false
    ): NowVisual {
        if (notification != null) {
            return NowVisual.App(notification.packageName.ifBlank { null }, notification.app ?: appNameOf(source) ?: "", NowGlyph.NOTIFICATION)
        }
        return when (kind) {
            "approval" -> NowVisual.Glyph(NowGlyph.APPROVAL)
            "reminder" -> NowVisual.Glyph(NowGlyph.REMINDER)
            "check" -> NowVisual.Glyph(NowGlyph.CHECK)
            "review" -> NowVisual.Glyph(NowGlyph.REVIEW)
            "notification" -> NowVisual.Glyph(NowGlyph.NOTIFICATION)
            "money" -> NowVisual.Glyph(NowGlyph.MONEY)
            else -> when {
                ref?.type == "call" -> NowVisual.Glyph(NowGlyph.CALL)
                ref?.type == "notification" ->
                    appNameOf(source)?.let { NowVisual.App(null, it, NowGlyph.NOTIFICATION) } ?: NowVisual.Glyph(NowGlyph.NOTIFICATION)
                kind == "suggestion" && primary != null && primary.action in DISMISS_LIKE -> NowVisual.Glyph(NowGlyph.ALERT)
                kind == "suggestion" && !hasTriggerChoice && primary?.label?.contains("calendar", ignoreCase = true) == true ->
                    NowVisual.Glyph(NowGlyph.EVENT)
                ref?.type == "task" -> NowVisual.Glyph(NowGlyph.REMINDER)
                else -> NowVisual.Glyph(NowGlyph.SUGGESTION)
            }
        }
    }

    fun visual(item: NowItem): NowVisual =
        visual(item.kind, item.ref, item.source, item.notification, item.primary, hasTriggerChoice = choiceBlocks(item).any { it.key == "triggers" })

    fun visual(q: QuietEntry): NowVisual = visual(q.kind, q.ref, q.source, null, q.primary)

    /** The app name that starts a `source` ("Slack · 2 h ago" → "Slack"); null when the source is only a time or a bare word like "Call". */
    fun appNameOf(source: String): String? {
        val parts = source.split(" · ").map { it.trim() }
        // "Slack · 2 h ago" has two parts; a lone part is not "App · when".
        if (parts.size < 2) return null
        return parts[0].takeIf { it.isNotEmpty() && it.lowercase() !in NON_APP_SOURCES }
    }

    private val NON_APP_SOURCES = setOf("call", "reminder", "check", "review", "approval", "dana", "money")

    /** The secondary that removes the card (dismiss / skip / ignore); an alert's "Got it" primary counts too. */
    fun dismissAction(item: NowItem): NowAction? =
        DISMISS_LIKE.firstNotNullOfOrNull { a -> item.secondary.firstOrNull { it.action == a } }
            ?: item.primary?.takeIf { it.action in DISMISS_LIKE }

    /**
     * Row buttons: the primary, then — when there are more than two others —
     * the first non-dismiss one and the dismiss; the rest wait under the
     * evidence ("Later" for a reminder's snoozes).
     */
    fun rowActions(item: NowItem): RowActions {
        val dismiss = dismissAction(item)
        val others = item.secondary
        val inline = if (others.size <= 2) others else {
            listOfNotNull(others.firstOrNull { it !== dismiss }, dismiss.takeIf { it in others })
        }
        return RowActions(item.primary, inline, others.filter { it !in inline }, dismiss)
    }

    fun choiceBlocks(item: NowItem): List<NowBlock.Choices> = item.blocks.filterIsInstance<NowBlock.Choices>()
    fun checkBlocks(item: NowItem): List<NowBlock.Checks> = item.blocks.filterIsInstance<NowBlock.Checks>()

    /** Index of the chosen option of [block] (the owner's pick, else the server's `selected`, else the first). */
    fun chosenIndex(block: NowBlock.Choices, draft: ItemDraft?): Int {
        val picked = draft?.choices?.get(block.key)
        if (picked != null && picked in block.options.indices) return picked
        return block.options.indexOfFirst { it.selected }.takeIf { it >= 0 } ?: 0
    }

    /** Indexes of the ticked options of [block]. */
    fun checkedIndexes(block: NowBlock.Checks, draft: ItemDraft?): Set<Int> =
        draft?.checks?.get(block.key)?.filter { it in block.options.indices }?.toSet()
            ?: block.options.indices.filter { block.options[it].checked }.toSet()

    /** The primary button's label: the chosen option's `action_label` when a choice changes it. */
    fun primaryLabel(item: NowItem, draft: ItemDraft?): String? {
        val p = item.primary ?: return null
        val fromChoice = choiceBlocks(item).firstNotNullOfOrNull { b ->
            b.options.getOrNull(chosenIndex(b, draft))?.actionLabel
        }
        return fromChoice ?: p.label
    }

    /**
     * Args for [action] on [item]: the action's own `args`, and for the
     * primary also every `choices` value (`args[key]`) and `checks` list.
     */
    fun argsFor(item: NowItem, action: NowAction, draft: ItemDraft?, isPrimary: Boolean): JSONObject {
        val out = action.argsJson?.let { runCatching { JSONObject(it) }.getOrNull() } ?: JSONObject()
        if (!isPrimary) return out
        choiceBlocks(item).forEach { b ->
            val opt = b.options.getOrNull(chosenIndex(b, draft)) ?: return@forEach
            if (b.key.isNotBlank()) out.put(b.key, readValue(opt.valueJson))
        }
        checkBlocks(item).forEach { b ->
            if (b.key.isBlank()) return@forEach
            val arr = JSONArray()
            checkedIndexes(b, draft).sorted().forEach { i -> arr.put(readValue(b.options[i].valueJson)) }
            out.put(b.key, arr)
        }
        return out
    }

    /** A JSON value from its text form (`{"time": …}`, `"abc"`, `3`, `true`, `null`). */
    fun readValue(text: String): Any = runCatching { JSONObject("{\"v\":$text}").get("v") }.getOrDefault(JSONObject.NULL)

    /** Server slot key for a day-strip cell: the hour ("21"), "home" or "wake". */
    fun slotKey(slot: DaySlot): String = when (slot) {
        is DaySlot.Hour -> slot.hour.toString()
        DaySlot.AtHome -> "home"
        DaySlot.OnWaking -> "wake"
    }

    /**
     * After [removedId] leaves [visible] (the list before it left), the row to
     * select: the one that took its place, else the one above, else none.
     */
    fun nextSelection(visible: List<String>, removedId: String): String? {
        val i = visible.indexOf(removedId)
        if (i < 0) return visible.firstOrNull()
        val rest = visible.filterIndexed { j, _ -> j != i }
        return rest.getOrNull(i) ?: rest.lastOrNull()
    }

    /** A notification item's reply button (the first that takes text), from what the server knows. */
    fun replyButton(item: NowItem): String? = item.notification?.actions?.firstOrNull { it.reply }?.title
}

/**
 * Rows acted on but not yet gone from the server's answer. A row is hidden
 * from [add] until a refresh that *started after* its action finished comes
 * back (the server's word is final then — whether the row is there or not).
 * [restore] brings it back at once (the action failed, or Undo).
 */
class PendingRemovals {
    private val entries = LinkedHashMap<String, Long?>()
    private var clock = 0L

    val ids: Set<String> get() = entries.keys.toSet()

    fun add(id: String) { entries[id] = null }

    /** The action on [id] succeeded on the server. */
    fun finished(id: String) {
        if (id in entries) entries[id] = ++clock
    }

    fun restore(id: String) { entries.remove(id) }

    /** Call when a refresh starts; pass the token to [refreshed]. */
    fun refreshStarted(): Long = clock

    /** A refresh that started at [token] came back: forget rows whose action finished before it started. */
    fun refreshed(token: Long) {
        entries.entries.removeAll { (_, done) -> done != null && done <= token }
    }

    fun visible(items: List<NowItem>): List<NowItem> {
        if (entries.isEmpty()) return items
        val hidden = entries.keys
        return items.filter { it.id !in hidden }
    }
}
