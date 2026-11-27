package io.github.ronganam.dana.ui.screens.now

import io.github.ronganam.dana.data.models.NowAction
import io.github.ronganam.dana.data.models.NowBlock
import io.github.ronganam.dana.data.models.NowItem
import io.github.ronganam.dana.data.models.NowNotification
import io.github.ronganam.dana.data.models.NowRef
import io.github.ronganam.dana.data.models.QuietEntry
import io.github.ronganam.dana.ui.shell.DaySlot
import io.github.ronganam.dana.ui.theme.Hue
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject

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

/** What a row offers: the one visible primary, and every other action in its overflow menu (server order). */
data class RowActions(
    val primary: NowAction?,
    val overflow: List<NowAction>,
    /** What swiping towards the start does (null: nothing). */
    val dismiss: NowAction?,
    /** What swiping towards the end does: the card's own first snooze (null: nothing). */
    val snooze: NowAction? = null
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

/**
 * What leads a queue row: a notification item's app icon ([App]: its
 * package, and the letter its fallback tile shows until the icon is there),
 * every other kind its 8dp dot in [hue].
 */
sealed class RowLead {
    data class App(val pkg: String, val letter: String) : RowLead()
    data class Dot(val hue: Hue) : RowLead()
}

object NowModel {

    /** A row's lead (see [RowLead]): the app icon for an item that carries its notification, else the kind's dot. */
    fun rowLead(item: NowItem): RowLead {
        val n = item.notification
        val pkg = n?.packageName?.trim().orEmpty()
        if (n == null || pkg.isEmpty()) return RowLead.Dot(hueOf(item))
        val name = n.app?.trim()?.ifEmpty { null } ?: appNameOf(item.source) ?: pkg.substringAfterLast('.')
        return RowLead.App(pkg, initial(name))
    }

    /** The first letter of [name] (a whole code point), upper-cased; "?" when blank. */
    fun initial(name: String): String {
        val t = name.trim()
        if (t.isEmpty()) return "?"
        return String(Character.toChars(t.codePointAt(0))).uppercase()
    }

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

    /** A row's actions: only the primary shows; the secondaries (snoozes, dismiss, …) wait in the overflow, in server order. */
    fun rowActions(item: NowItem): RowActions =
        RowActions(item.primary, item.secondary, dismissAction(item), snoozeAction(item))

    /** A row's muted line: "source · why", either left out when blank. */
    fun metaLine(source: String, why: String): String =
        listOf(source, why).map { it.trim() }.filter { it.isNotEmpty() }.joinToString(" · ")

    /** Only an approval's primary is filled (ember); every other is a quiet pill. */
    fun solidPrimary(item: NowItem): Boolean = item.kind == "approval"

    /** The card's first `snooze` secondary (the shortest, server order); never invented for a kind that offers none. */
    fun snoozeAction(item: NowItem): NowAction? = item.secondary.firstOrNull { it.action == "snooze" }

    /**
     * A card's hue, by `kind` only (never by addon name): approval ember,
     * notification teal, suggestion plum (sage for one from a call), reminder
     * amber, money rose for an unusual charge and sage for a saving / target,
     * check rose, review sky; any other kind (an addon's, e.g. trains) sky.
     */
    fun hueOf(kind: String, refType: String?): Hue = when (kind) {
        "approval" -> Hue.EMBER
        "notification" -> Hue.TEAL
        "suggestion" -> if (refType == "call") Hue.SAGE else Hue.PLUM
        "reminder" -> Hue.AMBER
        "money" -> if (refType == "transaction") Hue.ROSE else Hue.SAGE
        "check" -> Hue.ROSE
        "review" -> Hue.SKY
        else -> Hue.SKY
    }

    fun hueOf(item: NowItem): Hue = hueOf(item.kind, item.ref?.type)
    fun hueOf(q: QuietEntry): Hue = hueOf(q.kind, q.ref?.type)

    /** A day-strip item's hue: events sky, reminders amber, anything else (an addon's) sky. */
    fun dayHue(kind: String): Hue = if (kind == "reminder") Hue.AMBER else Hue.SKY

    /** The header's greeting from the hour: morning 05–11, afternoon 12–17, evening otherwise. */
    fun greeting(hour: Int): String = when (hour) {
        in 5..11 -> "Good morning"
        in 12..17 -> "Good afternoon"
        else -> "Good evening"
    }

    /** "Updated just now" / "Updated 2 min ago" / "Updated 3 h ago" / "Updated 2 days ago". */
    fun updatedAgo(updatedAtMs: Long, nowMs: Long): String {
        val min = ((nowMs - updatedAtMs).coerceAtLeast(0) / 60_000).toInt()
        return when {
            min < 1 -> "Updated just now"
            min < 60 -> "Updated $min min ago"
            min < 48 * 60 -> "Updated ${min / 60} h ago"
            else -> "Updated ${min / (24 * 60)} days ago"
        }
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
    fun argsFor(item: NowItem, action: NowAction, draft: ItemDraft?, isPrimary: Boolean): JsonObject {
        if (!isPrimary) return action.args
        val out = action.args.toMutableMap()
        choiceBlocks(item).forEach { b ->
            val opt = b.options.getOrNull(chosenIndex(b, draft)) ?: return@forEach
            if (b.key.isNotBlank()) out[b.key] = opt.value
        }
        checkBlocks(item).forEach { b ->
            if (b.key.isBlank()) return@forEach
            out[b.key] = JsonArray(checkedIndexes(b, draft).sorted().map { i -> b.options[i].value })
        }
        return JsonObject(out)
    }

    /** Server slot key for a day-strip cell: the hour ("21"), "home" or "wake". */
    fun slotKey(slot: DaySlot): String = when (slot) {
        is DaySlot.Hour -> slot.hour.toString()
        DaySlot.AtHome -> "home"
        DaySlot.OnWaking -> "wake"
        is DaySlot.At -> slot.time.hour.toString()
        is DaySlot.Moment -> when (val w = slot.w) {
            io.github.ronganam.dana.ui.screens.reminders.RemindWhen.Wake -> "wake"
            is io.github.ronganam.dana.ui.screens.reminders.RemindWhen.Place -> if (!w.leave && io.github.ronganam.dana.ui.screens.reminders.isHomeZone(w.zone)) "home" else w.kind
            else -> w.kind
        }
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

/**
 * Which cards are new since the queue was last shown, so only those rise in:
 * nothing on the first non-empty look (the first load has its own arrival),
 * and a card seen before (back after Undo or a failed action) never again.
 */
class NewCards {
    private val seen = HashSet<String>()
    private var primed = false

    fun fresh(ids: List<String>): Set<String> {
        if (!primed) {
            if (ids.isEmpty()) return emptySet()
            primed = true
            seen += ids
            return emptySet()
        }
        val out = ids.filterTo(HashSet()) { it !in seen }
        seen += ids
        return out
    }
}
