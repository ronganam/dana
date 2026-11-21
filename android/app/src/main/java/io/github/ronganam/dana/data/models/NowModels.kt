package io.github.ronganam.dana.data.models

/*
 * Now, the Notifications screen and search (server docs/now.md). Parsed in
 * data/network/NowJson.kt. Every field the server may leave out is nullable
 * or has a default; unknown fields are ignored.
 */

/** `GET /api/now`. */
data class NowPayload(
    /** The server's local time (ISO with offset). */
    val now: String?,
    val lede: String?,
    val items: List<NowItem>,
    val quiet: List<QuietEntry>,
    val quietTotal: Int,
    val notifications: NowNotificationCounts,
    val daySlots: List<DaySlotInfo>,
    val calendarOk: Boolean
)

data class NowNotificationCounts(val total: Int = 0, val noise: Int = 0, val needsYou: Int = 0)

/**
 * One row of the Now queue. [kind]: approval | notification | suggestion |
 * reminder | check | review (unknown kinds still render: source, title, why,
 * buttons). [id] goes back to `/api/now/act` as is.
 */
data class NowItem(
    val id: String,
    val kind: String,
    val source: String,
    val title: String,
    val why: String,
    val primary: NowAction?,
    val secondary: List<NowAction>,
    val schedulable: Boolean,
    val ref: NowRef?,
    val notification: NowNotification?,
    val blocks: List<NowBlock>
)

/** `client = true`: the phone does it itself (`open`, `reply`) and never posts it. */
data class NowAction(
    val action: String,
    val label: String,
    /** The action's own args (JSON object as text, so the model stays plain data). */
    val argsJson: String? = null,
    val client: Boolean = false
)

/** What the item is about: `thread`, `call`, `notification`, `task`, `check`, `review`. */
data class NowRef(val type: String, val id: String)

/** A notification item's shade handle: reply / open / press buttons through the listener. */
data class NowNotification(
    val id: String,
    val key: String?,
    val packageName: String,
    val app: String?,
    val canReply: Boolean,
    val onPhone: Boolean,
    val actions: List<NowNotificationButton>
)

data class NowNotificationButton(val title: String, val reply: Boolean)

/** Evidence rendered generically, in order. Unknown types parse to [Unknown] and draw nothing. */
sealed class NowBlock {
    abstract val label: String?

    data class Lines(override val label: String?, val lines: List<Line>) : NowBlock() {
        data class Line(val text: String, val who: String?, val hit: Boolean)
    }

    data class Chat(override val label: String?, val messages: List<Message>) : NowBlock() {
        data class Message(val text: String, val mine: Boolean, val who: String?)
    }

    data class Facts(override val label: String?, val items: List<String>) : NowBlock()
    data class Text(override val label: String?, val text: String) : NowBlock()

    data class Week(override val label: String?, val cells: List<Cell>) : NowBlock() {
        /** [ok] null = today, not known yet. */
        data class Cell(val label: String, val value: String, val ok: Boolean?)
    }

    /** Picking one changes the primary: its value goes as `args[key]`, its `action_label` on the button. */
    data class Choices(override val label: String?, val key: String, val options: List<Option>) : NowBlock() {
        data class Option(
            val label: String,
            /** The value as JSON text (an object, a string, a number…). */
            val valueJson: String,
            val selected: Boolean,
            val actionLabel: String?,
            val message: String?
        )
    }

    /** The checked values go as a list in `args[key]` with the primary. */
    data class Checks(override val label: String?, val key: String, val options: List<Option>) : NowBlock() {
        data class Option(val label: String, val valueJson: String, val checked: Boolean)
    }

    data class Unknown(val type: String, override val label: String?) : NowBlock()
}

/** A pending suggestion that never pops up ("N quieter suggestions"). */
data class QuietEntry(
    val id: String,
    val kind: String,
    val title: String,
    val source: String,
    val primary: NowAction?,
    val secondary: List<NowAction>,
    val ref: NowRef?
)

/** One cell of the server's day strip (`day.slots`): an hour ("19"), "home" or "wake". */
data class DaySlotInfo(val key: String, val label: String, val items: List<DaySlotItem>)

data class DaySlotItem(val title: String, val kind: String, val time: String?, val taskId: String?, val eventId: String?)

/** `POST /api/now/act` (and the clearing endpoints): what to show and how to undo it. */
data class ActResult(
    val message: String,
    val undoId: String?,
    /** Dismissed notifications: shade keys to cancel. */
    val cancelKeys: List<String> = emptyList(),
    /** Snoozed notifications: shade key → epoch ms to snooze until. */
    val snoozeKeys: Map<String, Long> = emptyMap(),
    val taskId: String? = null
)

// --- Notifications screen (`GET /api/notifications/overview`) -----------------------

data class NotificationsOverview(
    val counts: OverviewCounts,
    val apps: List<OverviewApp>,
    val quietApps: List<OverviewApp>,
    val ruleSuggestions: List<RuleSuggestion>,
    val summariesRefreshing: Boolean
)

data class OverviewCounts(val total: Int = 0, val noise: Int = 0, val needsYou: Int = 0, val snoozed: Int = 0)

/**
 * [rule] in the screen's words: `alert` (Interrupt me), `normal` (Let Dana
 * decide), `quiet` (Keep quiet). [ruleAction] is the stored rule (alert |
 * important | mute | null).
 */
data class OverviewApp(
    val packageName: String,
    val app: String,
    val rule: String,
    val ruleAction: String?,
    val count: Int,
    val needsYou: Int,
    val noise: Int,
    val summary: List<String>,
    /** `ai` or `latest` (the newest notification as one line). */
    val summarySource: String?,
    val latestAt: String?
)

data class RuleSuggestion(
    val packageName: String,
    val app: String,
    val action: String,
    val cleared: Int,
    val opened: Int,
    val reason: String
)

// --- Search (`GET /api/search`) ---------------------------------------------------------

/** [kind]: reminder | call | person | note (projects as `project:<id>`) | notification | shopping. */
/** One search hit. [packageName] (notifications) and [projectId] (notes) say where it opens. */
data class SearchResult(
    val kind: String,
    val id: String,
    val title: String,
    val subtitle: String,
    val packageName: String? = null,
    val projectId: String? = null
)
