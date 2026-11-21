package io.github.ronganam.dana.protocol

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer

/**
 * What the phone sends the watch to show (tiles, complications, cards, the
 * shopping list) and the small requests the watch sends back. Every field has
 * the default the reading side uses when it is missing.
 */

/** A calendar event in the watch's config ([WearConfig.calendarEventsJson]). */
@Serializable
data class WearEvent(
    val id: String = "",
    val summary: String = "",
    val start: String = "",
    val end: String = "",
    @SerialName("all_day") val allDay: Boolean = false,
    val location: String? = null,
) {
    companion object {
        fun encodeList(events: List<WearEvent>): String = WearJson.encodeToString(ListSerializer(serializer()), events)
        /** The events in [text]; empty when it isn't a list of events. */
        fun decodeList(text: String?): List<WearEvent> = ListSerializer(serializer()).decodeOrNull(text ?: "[]").orEmpty()
    }
}

/** A task: the [WearPaths.GET_TASKS] answer and the due tasks in the config ([WearConfig.dueTasksJson]). */
@Serializable
data class WearTaskItem(
    val id: String = "",
    val title: String = "",
    val description: String? = null,
    val priority: String = "medium",
    @SerialName("due_date") val dueDate: String? = null,
    val status: String = "needs_action",
    @SerialName("group_name") val groupName: String? = null,
    @SerialName("group_emoji") val groupEmoji: String? = null,
) {
    companion object {
        fun encodeList(tasks: List<WearTaskItem>): String = WearJson.encodeToString(ListSerializer(serializer()), tasks)
        /** The tasks in [text]; empty when it isn't a list of tasks. */
        fun decodeList(text: String?): List<WearTaskItem> = ListSerializer(serializer()).decodeOrNull(text ?: "[]").orEmpty()
    }
}

/** [WearPaths.CARD]: the caller card, a person before a meeting, the briefing; [WearPaths.CARD_CLEAR] only needs [kind]. */
@Serializable
data class WearCard(
    val kind: String = "",
    val title: String = "",
    val subtitle: String = "",
    val lines: List<String> = emptyList(),
    val ref: String = "",
) {
    fun encode(): String = WearJson.encodeToString(serializer(), this)

    companion object {
        fun decode(text: String): WearCard? = serializer().decodeOrNull(text)
    }
}

/** [WearPaths.SUGGESTION_ACTION]: accept or dismiss a suggestion from the Waiting tile. */
@Serializable
data class SuggestionAction(val id: String, val action: String = "") {
    fun encode(): String = WearJson.encodeToString(serializer(), this)

    companion object {
        fun decode(text: String): SuggestionAction? = serializer().decodeOrNull(text)
    }
}

/** The phone's answer to [SuggestionAction]. */
@Serializable
data class SuggestionActionReply(val success: Boolean = false, val id: String = "") {
    fun encode(): String = WearJson.encodeToString(serializer(), this)
}

/** One item to buy, as the phone's snapshot sends it. */
@Serializable
data class ShopItem(
    val id: String = "",
    val name: String = "",
    val quantity: String = "",
    val aisle: String = "",
    @SerialName("aisle_key") val aisleKey: String = "",
)

/**
 * [WearPaths.SHOPPING_ACTION]: tick / untick an item ([id]) or add typed
 * [text], on the list [listId]; [ref] is the watch's own id for the request,
 * sent back with the answer.
 */
@Serializable
data class ShopRequest(
    val action: String = "",
    val id: String? = null,
    @SerialName("list_id") val listId: String = "",
    val text: String? = null,
    val ref: String = "",
) {
    fun encode(): String = WearJson.encodeToString(serializer(), this)

    companion object {
        fun decode(text: String): ShopRequest? = serializer().decodeOrNull(text)
    }
}

/** The phone's answer to a [ShopRequest]: [error] is a sentence to show. */
@Serializable
data class ShopAnswer(
    val ok: Boolean = false,
    val queued: Boolean = false,
    val error: String? = null,
    val ref: String? = null,
) {
    fun encode(): String = WearJson.encodeToString(serializer(), this)

    companion object {
        fun decode(text: String): ShopAnswer? = serializer().decodeOrNull(text)
    }
}

@Serializable
data class SnapshotEvent(val time: String = "", val title: String = "")

@Serializable
data class SnapshotTask(val id: String = "", val title: String = "", val due: String = "")

@Serializable
data class SnapshotSuggestion(val id: String = "", val title: String = "", val source: String = "")

@Serializable
data class SnapshotPerson(
    val id: String = "",
    val name: String = "",
    val initials: String = "",
    val ago: String = "",
    val phone: String = "",
    val open: Int = 0,
    val drifting: Boolean = false,
)

/**
 * One snapshot for the phone's home-screen widgets (pref `widget_snapshot`)
 * and, trimmed ([forWatch]), the watch's tiles and complications
 * ([WearPaths.SNAPSHOT]). The shopping part: what is left to buy in aisle
 * order ([shopping], null when no list is known), how many in all, the list
 * they are on, its version, and whether it can be changed.
 */
@Serializable
data class DanaSnapshot(
    @SerialName("briefing_headline") val briefingHeadline: String? = null,
    @SerialName("briefing_date") val briefingDate: String? = null,
    @SerialName("briefing_points") val briefingPoints: List<String> = emptyList(),
    val events: List<SnapshotEvent> = emptyList(),
    val tasks: List<SnapshotTask> = emptyList(),
    val suggestions: List<SnapshotSuggestion> = emptyList(),
    @SerialName("suggestion_count") val suggestionCount: Int = 0,
    val people: List<SnapshotPerson> = emptyList(),
    /** Null when the phone hasn't counted the Inbox yet. */
    @SerialName("inbox_needs") val inboxNeeds: Int? = null,
    @SerialName("inbox_active") val inboxActive: Int = 0,
    @SerialName("inbox_points") val inboxPoints: List<String> = emptyList(),
    @SerialName("updated_at") val updatedAt: Long = 0,
    @SerialName("shopping_count") val shoppingCount: Int? = null,
    val shopping: List<ShopItem>? = null,
    @SerialName("shopping_list_id") val shoppingListId: String = "",
    @SerialName("shopping_version") val shoppingVersion: Long = 0,
    @SerialName("shopping_can_edit") val shoppingCanEdit: Boolean = true,
) {
    fun encode(): String = WearJson.encodeToString(serializer(), this)

    /** The parts the watch shows, kept well under the data-layer message size limit. */
    fun forWatch(): DanaSnapshot = copy(
        briefingPoints = briefingPoints.take(4),
        events = events.take(3),
        tasks = tasks.take(4),
        suggestions = suggestions.take(3),
        people = emptyList(),
        inboxPoints = emptyList(),
        shopping = shopping?.take(40),
    )

    companion object {
        /** The snapshot in [text]; null when there is none or it doesn't read. */
        fun decode(text: String?): DanaSnapshot? = text?.let { serializer().decodeOrNull(it) }
    }
}
