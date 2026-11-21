package io.github.ronganam.dana.data.network

import io.github.ronganam.dana.data.models.ActResult
import io.github.ronganam.dana.data.models.DaySlotInfo
import io.github.ronganam.dana.data.models.DaySlotItem
import io.github.ronganam.dana.data.models.NotificationsOverview
import io.github.ronganam.dana.data.models.NowAction
import io.github.ronganam.dana.data.models.NowBlock
import io.github.ronganam.dana.data.models.NowItem
import io.github.ronganam.dana.data.models.NowNotification
import io.github.ronganam.dana.data.models.NowNotificationButton
import io.github.ronganam.dana.data.models.NowNotificationCounts
import io.github.ronganam.dana.data.models.NowPayload
import io.github.ronganam.dana.data.models.NowRef
import io.github.ronganam.dana.data.models.OverviewApp
import io.github.ronganam.dana.data.models.OverviewCounts
import io.github.ronganam.dana.data.models.QuietEntry
import io.github.ronganam.dana.data.models.RuleSuggestion
import io.github.ronganam.dana.data.models.SearchResult
import io.github.ronganam.dana.data.network.ApiClient.Companion.enc
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/*
 * The wire shapes of `/api/now`, `/api/notifications/overview` and
 * `/api/search` (server docs/now.md). Lenient on purpose: missing fields get
 * defaults, unknown block types become [NowBlock.Unknown], rows without an id
 * are dropped.
 */

@Serializable
internal data class NowActionWire(val action: OptString = null, val label: OptString = null, val args: JsonObject? = null, val client: Boolean = false) {
    fun toModel(): NowAction? = action?.let { a ->
        NowAction(a, label ?: a.replaceFirstChar { it.uppercase() }, args ?: JsonObject(emptyMap()), client)
    }
}

@Serializable
internal data class NowRefWire(val type: OptString = null, val id: OptString = null) {
    fun toModel(): NowRef? = if (type != null && id != null) NowRef(type, id) else null
}

@Serializable
internal data class NowButtonWire(val title: OptString = null, val reply: Boolean = false)

@Serializable
internal data class NowNotificationWire(
    val id: OptString = null,
    val key: OptString = null,
    @SerialName("package") val packageName: String = "",
    val app: OptString = null,
    val canReply: Boolean = false,
    val onPhone: Boolean = false,
    val actions: LenientList<NowButtonWire> = emptyList()
) {
    fun toModel(): NowNotification? = id?.let {
        NowNotification(it, key, packageName, app, canReply, onPhone, actions.mapNotNull { a -> a.title?.let { t -> NowNotificationButton(t, a.reply) } })
    }
}

@Serializable
internal data class BlockLineWire(val text: OptString = null, val who: OptString = null, val hit: Boolean = false, val mine: Boolean = false)

@Serializable
internal data class BlockCellWire(val label: String = "", val value: OptString = null, val ok: Boolean? = null)

@Serializable
internal data class BlockOptionWire(
    val label: String = "",
    val value: JsonElement = JsonNull,
    val selected: Boolean = false,
    val checked: Boolean = false,
    val actionLabel: OptString = null,
    val message: OptString = null
)

@Serializable
internal data class NowBlockWire(
    val type: String = "",
    val label: OptString = null,
    val lines: LenientList<BlockLineWire> = emptyList(),
    val messages: LenientList<BlockLineWire> = emptyList(),
    val items: TextList = emptyList(),
    val text: OptString = null,
    val cells: LenientList<BlockCellWire> = emptyList(),
    val key: String = "",
    val options: LenientList<BlockOptionWire> = emptyList()
) {
    fun toModel(): NowBlock = when (type) {
        "lines" -> NowBlock.Lines(label, lines.mapNotNull { l -> l.text?.let { NowBlock.Lines.Line(it, l.who, l.hit) } })
        "chat" -> NowBlock.Chat(label, messages.mapNotNull { m -> m.text?.let { NowBlock.Chat.Message(it, m.mine, m.who) } })
        "facts" -> NowBlock.Facts(label, items)
        "text" -> NowBlock.Text(label, text.orEmpty())
        "week" -> NowBlock.Week(label, cells.map { NowBlock.Week.Cell(it.label, it.value ?: "–", it.ok) })
        "choices" -> NowBlock.Choices(label, key, options.map {
            NowBlock.Choices.Option(it.label, it.value, it.selected, it.actionLabel, it.message)
        })
        "checks" -> NowBlock.Checks(label, key, options.map { NowBlock.Checks.Option(it.label, it.value, it.checked) })
        else -> NowBlock.Unknown(type, label)
    }
}

@Serializable
internal data class NowItemWire(
    val id: OptString = null,
    val kind: OptString = null,
    val source: OptString = null,
    val title: OptString = null,
    val why: OptString = null,
    val primary: NowActionWire? = null,
    val secondary: LenientList<NowActionWire> = emptyList(),
    val schedulable: Boolean = false,
    val ref: NowRefWire? = null,
    val notification: NowNotificationWire? = null,
    val blocks: LenientList<NowBlockWire> = emptyList()
) {
    fun toModel(): NowItem? = id?.let {
        NowItem(
            id = it, kind = kind ?: it.substringBefore(':'), source = source.orEmpty(), title = title.orEmpty(), why = why.orEmpty(),
            primary = primary?.toModel(), secondary = secondary.mapNotNull { a -> a.toModel() }, schedulable = schedulable,
            ref = ref?.toModel(), notification = notification?.toModel(), blocks = blocks.map { b -> b.toModel() }
        )
    }
}

@Serializable
internal data class QuietWire(
    val id: OptString = null,
    val kind: OptString = null,
    val title: OptString = null,
    val source: OptString = null,
    val primary: NowActionWire? = null,
    val secondary: LenientList<NowActionWire> = emptyList(),
    val ref: NowRefWire? = null
) {
    fun toModel(): QuietEntry? = id?.let {
        QuietEntry(it, kind ?: "suggestion", title.orEmpty(), source.orEmpty(), primary?.toModel(), secondary.mapNotNull { a -> a.toModel() }, ref?.toModel())
    }
}

@Serializable
internal data class SlotItemWire(val title: String = "", val kind: String = "", val time: OptString = null, val taskId: OptString = null, val eventId: OptString = null)

@Serializable
internal data class SlotWire(val key: OptString = null, val label: OptString = null, val items: LenientList<SlotItemWire> = emptyList()) {
    fun toModel(): DaySlotInfo? = key?.let { k ->
        DaySlotInfo(k, label ?: k, items.map { DaySlotItem(it.title, it.kind, it.time, it.taskId, it.eventId) })
    }
}

@Serializable
internal data class DayWire(val slots: LenientList<SlotWire> = emptyList(), val calendar: Boolean = true)

@Serializable
internal data class NowPayloadWire(
    val now: OptString = null,
    val lede: OptString = null,
    val items: LenientList<NowItemWire> = emptyList(),
    val quiet: LenientList<QuietWire> = emptyList(),
    val quietTotal: Int? = null,
    val notifications: NowNotificationCounts = NowNotificationCounts(),
    val day: DayWire? = null
) {
    fun toModel() = NowPayload(
        now = now, lede = lede, items = items.mapNotNull { it.toModel() }, quiet = quiet.mapNotNull { it.toModel() },
        quietTotal = quietTotal ?: quiet.size, notifications = notifications,
        daySlots = day?.slots.orEmpty().mapNotNull { it.toModel() }, calendarOk = day?.calendar ?: true
    )
}

@Serializable
internal data class OverviewAppWire(
    @SerialName("package") val packageName: OptString = null,
    val app: OptString = null,
    val rule: OptString = null,
    val ruleAction: OptString = null,
    val count: Int = 0,
    val needsYou: Int = 0,
    val noise: Int = 0,
    val summary: TextList = emptyList(),
    val summarySource: OptString = null,
    val latestAt: OptString = null
) {
    fun toModel(): OverviewApp? = packageName?.let { pkg ->
        OverviewApp(pkg, app ?: pkg, rule ?: "normal", ruleAction, count, needsYou, noise, summary, summarySource, latestAt)
    }
}

@Serializable
internal data class RuleSuggestionWire(
    @SerialName("package") val packageName: OptString = null,
    val app: OptString = null,
    val action: OptString = null,
    val cleared: Int = 0,
    val opened: Int = 0,
    val reason: OptString = null
) {
    fun toModel(): RuleSuggestion? = packageName?.let { pkg -> RuleSuggestion(pkg, app ?: pkg, action ?: "quiet", cleared, opened, reason.orEmpty()) }
}

@Serializable
internal data class OverviewWire(
    val counts: OverviewCounts = OverviewCounts(),
    val apps: LenientList<OverviewAppWire> = emptyList(),
    val quietApps: LenientList<OverviewAppWire> = emptyList(),
    val ruleSuggestions: LenientList<RuleSuggestionWire> = emptyList(),
    val summariesRefreshing: Boolean = false
) {
    fun toModel() = NotificationsOverview(
        counts, apps.mapNotNull { it.toModel() }, quietApps.mapNotNull { it.toModel() }, ruleSuggestions.mapNotNull { it.toModel() }, summariesRefreshing
    )
}

@Serializable
internal data class SearchResultWire(
    val kind: String = "",
    val id: OptString = null,
    val title: OptString = null,
    val subtitle: OptString = null,
    @SerialName("package") val packageName: OptString = null,
    val projectId: OptString = null
) {
    fun toModel(): SearchResult? = id?.let { SearchResult(kind, it, title.orEmpty(), subtitle.orEmpty(), packageName, projectId) }
}

@Serializable
internal data class SearchWire(val results: LenientList<SearchResultWire> = emptyList())

/** Result of `POST /api/undo/{id}`. */
enum class UndoOutcome { DONE, TOO_LATE }

/** Now, acting on its cards, Undo and search (server docs/now.md). */
class NowApi(private val http: ApiClient) {

    /** The raw `/api/now` answer (cached on disk so the screen is never blank); read it with [parse]. */
    suspend fun nowText(): String = http.text(http.request("/api/now"), "Failed to load Now", "{}")

    /** Act on a Now card. Throws [ApiException] with the server's reason (400 / 404 / 409 / 500). */
    suspend fun act(id: String, action: String, args: JsonObject = JsonObject(emptyMap())): ActResult {
        val payload = buildJsonObject {
            put("id", id)
            put("action", action)
            if (args.isNotEmpty()) put("args", args)
        }
        return NotificationsApi.actResult(http.post("/api/now/act", payload, "That didn't work"))
    }

    /** Undo an action by its `undo_id`. 404 = expired / already used; a refused undo throws with the reason. */
    suspend fun undo(undoId: String): UndoOutcome = try {
        http.post("/api/undo/${enc(undoId)}", JsonObject(emptyMap()), "Couldn't undo")
        UndoOutcome.DONE
    } catch (e: ApiException) {
        if (e.code == 404) UndoOutcome.TOO_LATE else throw e
    }

    suspend fun search(query: String, limit: Int = 5): List<SearchResult> =
        http.decode(SearchWire.serializer(), http.text(http.request("/api/search?q=${enc(query)}&limit=$limit"), "Search failed", "{}"))
            .results.mapNotNull { it.toModel() }

    companion object {
        /** A `/api/now` answer (from the server or the disk cache). */
        fun parse(text: String): NowPayload = ApiJson.decodeFromString(NowPayloadWire.serializer(), text.ifBlank { "{}" }).toModel()
    }
}
