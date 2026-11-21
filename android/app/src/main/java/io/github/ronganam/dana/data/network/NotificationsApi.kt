package io.github.ronganam.dana.data.network

import io.github.ronganam.dana.data.models.ActResult
import io.github.ronganam.dana.data.models.InboxSummaries
import io.github.ronganam.dana.data.models.InboxSummary
import io.github.ronganam.dana.data.models.NotificationApp
import io.github.ronganam.dana.data.models.NotificationCounts
import io.github.ronganam.dana.data.models.NotificationsOverview
import io.github.ronganam.dana.data.models.PhoneNotification
import io.github.ronganam.dana.data.network.ApiClient.Companion.enc
import io.github.ronganam.dana.data.network.ApiClient.Companion.jsonBody
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray

/** A notification captured on this phone, as the server's `posted` list takes it. */
@Serializable
data class CapturedNotification(
    val key: String,
    @SerialName("package") val packageName: String,
    val appName: String,
    val title: String,
    val text: String,
    val subText: String = "",
    val sender: String = "",
    val conversation: String = "",
    val isGroup: Boolean = false,
    val category: String = "",
    val channel: String = "",
    /** Epoch ms. */
    val postedAt: Long = 0,
    val actions: List<NotificationButton> = emptyList()
)

/** One of a notification's buttons: its title and whether it takes typed text. */
@Serializable
data class NotificationButton(val title: String, val reply: Boolean = false)

/** A notification gone from the shade: its key, why (`click`, `cancel`, …) and when (epoch ms). */
@Serializable
data class RemovedNotification(val key: String, val reason: String, val at: Long, val code: Int? = null)

@Serializable
private data class NotificationBatch(
    val posted: List<CapturedNotification>,
    val removed: List<RemovedNotification>,
    val activeKeys: List<String>,
    val snoozedKeys: List<String>
)

/** The server's answer to a batch: shade keys to cancel and to snooze (key → epoch ms). */
data class BatchResult(val accepted: Int, val duplicates: Int, val cancelKeys: List<String>, val snoozeKeys: Map<String, Long>)

@Serializable
private data class BatchResultWire(
    val accepted: Int = 0,
    val duplicates: Int = 0,
    val cancelKeys: TextList = emptyList(),
    val snoozeKeys: JsonElement? = null
)

/** A notification button pressed on the web: what the phone should do. */
@Serializable
data class NotificationRequest(
    val action: OptString = null,
    val key: OptString = null,
    val text: OptString = null,
    val status: OptString = null
)

@Serializable
internal data class PhoneNotificationWire(
    val id: String = "",
    val key: OptString = null,
    @SerialName("package") val packageName: String = "",
    val appName: OptString = null,
    val title: OptString = null,
    val text: OptString = null,
    val sender: OptString = null,
    val conversation: OptString = null,
    val isGroup: Boolean = false,
    val postedAt: OptString = null,
    val status: String = "active",
    val removedReason: OptString = null,
    val triageStatus: String = "pending",
    val category: OptString = null,
    val categoryEmoji: String = "🔔",
    val priority: String = "normal",
    val bucket: String = "normal",
    val escalated: Boolean = false,
    val suggestions: JsonElement? = null,
    val snoozedUntil: OptString = null
) {
    fun toModel() = PhoneNotification(
        id = id, key = key, packageName = packageName, appName = appName ?: packageName, title = title, text = text,
        sender = sender, conversation = conversation, isGroup = isGroup, postedAt = postedAt, status = status,
        removedReason = removedReason, triageStatus = triageStatus, category = category,
        categoryEmoji = categoryEmoji.ifEmpty { "🔔" }, priority = priority.ifEmpty { "normal" },
        bucket = bucket.ifEmpty { "normal" }, escalated = escalated,
        suggestions = CallsApi.suggestionsOf(suggestions), snoozedUntil = snoozedUntil
    )
}

@Serializable
private data class NotificationPage(
    val items: LenientList<PhoneNotificationWire> = emptyList(),
    val counts: NotificationCounts = NotificationCounts()
)

@Serializable
private data class InboxSummaryWire(val summary: OptString = null, val count: Int = 0, val stale: Boolean = false, val points: TextList? = null) {
    fun toModel() = InboxSummary(summary = summary, count = count, stale = stale,
        points = points ?: summary?.split('\n')?.filter { it.isNotBlank() }.orEmpty())
}

@Serializable
private data class SummariesWire(
    val overall: InboxSummaryWire? = null,
    val apps: Map<String, InboxSummaryWire> = emptyMap(),
    val refreshing: Boolean = false
)

@Serializable
private data class NotificationAppWire(
    @SerialName("package") val packageName: String = "",
    val appName: OptString = null,
    val total: Int = 0,
    val opened: Int = 0,
    val swiped: Int = 0,
    val suggestedDismiss: Int = 0,
    val rule: OptString = null
) {
    fun toModel() = NotificationApp(packageName, appName ?: packageName, total, opened, swiped, suggestedDismiss, rule)
}

@Serializable
private data class AppsWire(val apps: LenientList<NotificationAppWire> = emptyList())

@Serializable
private data class DeviceKeys(val deviceKeys: JsonElement? = null, val updated: Int = 0)

/** The notification Inbox, its rules and summaries (server docs/notifications.md). */
class NotificationsApi(private val http: ApiClient) {

    /** Upload captured notifications and shade removals. */
    suspend fun postBatch(
        posted: List<CapturedNotification>, removed: List<RemovedNotification>, activeKeys: List<String>, snoozedKeys: List<String>
    ): BatchResult {
        val body = jsonBody(NotificationBatch.serializer(), NotificationBatch(posted, removed, activeKeys, snoozedKeys))
        val wire = http.decode(BatchResultWire.serializer(),
            http.text(http.request("/api/notifications/batch") { post(body) }, "Notification upload failed", "{}"))
        return BatchResult(wire.accepted, wire.duplicates, wire.cancelKeys, snoozeKeys(wire.snoozeKeys))
    }

    suspend fun list(
        status: String = "active", bucket: String? = null, query: String? = null, limit: Int = 300
    ): Pair<List<PhoneNotification>, NotificationCounts> {
        val qs = buildString {
            append("?status=").append(enc(status)).append("&limit=").append(limit)
            bucket?.let { append("&bucket=").append(enc(it)) }
            query?.takeIf { it.isNotBlank() }?.let { append("&q=").append(enc(it)) }
        }
        val page = http.decode(NotificationPage.serializer(), http.text(http.request("/api/notifications$qs"), "Failed to fetch notifications", "{}"))
        return page.items.map { it.toModel() } to page.counts
    }

    /** Active notifications of one app, newest first. */
    suspend fun forApp(packageName: String, limit: Int = 100): List<PhoneNotification> {
        val text = http.text(http.request("/api/notifications?status=active&package=${enc(packageName)}&limit=$limit"), "Failed to fetch notifications", "{}")
        return http.decode(NotificationPage.serializer(), text).items.map { it.toModel() }
    }

    suspend fun get(id: String): PhoneNotification? = runCatching {
        http.decode(PhoneNotificationWire.serializer(), http.text(http.request("/api/notifications/${enc(id)}"), "Failed to fetch the notification", "{}")).toModel()
    }.getOrNull()?.takeIf { it.packageName.isNotBlank() }

    /** Dismiss / archive / restore. Returns the device keys to cancel in the shade. */
    suspend fun setStatus(ids: List<String>, status: String, bucket: String? = null, dismissSuggestions: Boolean = false): List<String> {
        val payload = buildJsonObject {
            putJsonArray("ids") { ids.forEach { add(JsonPrimitive(it)) } }
            put("status", status)
            bucket?.let { put("bucket", it) }
            if (dismissSuggestions) put("dismiss_suggestions", true)
        }
        val text = http.text(http.request("/api/notifications/status") { post(jsonBody(payload)) }, "Couldn't update notifications", "{}")
        return cancelKeys(http.decode(DeviceKeys.serializer(), text).deviceKeys)
    }

    /**
     * Clear notifications in Dana: by [ids], a whole app ([packageName]) or a
     * bucket (`dismiss` = the noise). Returns the shade keys to cancel and the undo id.
     */
    suspend fun clear(ids: List<String> = emptyList(), packageName: String? = null, bucket: String? = null): ActResult {
        val payload = buildJsonObject {
            put("status", "dismissed")
            if (ids.isNotEmpty()) putJsonArray("ids") { ids.forEach { add(JsonPrimitive(it)) } }
            packageName?.let { put("package", it) }
            bucket?.let { put("bucket", it) }
        }
        val json = http.post("/api/notifications/status", payload, "Couldn't clear")
        val n = json.long("updated")?.toInt() ?: 0
        return actResult(json, if (n == 1) "Cleared 1" else "Cleared $n")
    }

    /** The user saw these on the alert screen: the server stops re-nudging. */
    suspend fun markSeen(ids: List<String>) {
        val body = buildJsonObject { putJsonArray("ids") { ids.forEach { add(JsonPrimitive(it)) } } }
        http.text(http.request("/api/notifications/seen") { post(jsonBody(body)) }, "Couldn't mark notifications seen", "{}")
    }

    /** Snooze until [untilMillis] (epoch ms), or wake them with null. Returns the shade keys to snooze → their end time. */
    suspend fun snooze(ids: List<String>, untilMillis: Long?): Map<String, Long> {
        val payload = buildJsonObject {
            putJsonArray("ids") { ids.forEach { add(JsonPrimitive(it)) } }
            if (untilMillis == null) put("clear", true) else put("until", java.time.Instant.ofEpochMilli(untilMillis).toString())
        }
        val text = http.text(http.request("/api/notifications/snooze") { post(jsonBody(payload)) }, "Couldn't snooze", "{}")
        return snoozeKeys(http.decode(DeviceKeys.serializer(), text).deviceKeys)
    }

    /** A notification button pressed on the web: what to do. */
    suspend fun request(id: String): NotificationRequest =
        http.decode(NotificationRequest.serializer(), http.text(http.request("/api/notifications/requests/${enc(id)}"), "Couldn't fetch the request", "{}"))

    /** Tell the web whether the phone did it. */
    suspend fun finishRequest(id: String, ok: Boolean, error: String?) {
        val payload = buildJsonObject { put("ok", ok); if (!error.isNullOrBlank()) put("error", error) }
        http.text(http.request("/api/notifications/requests/${enc(id)}/result") { post(jsonBody(payload)) }, "Couldn't report the result", "{}")
    }

    /** AI summary per app + the Inbox briefing (cached on the server; never waits for the model). */
    suspend fun summaries(): InboxSummaries {
        val w = http.decode(SummariesWire.serializer(), http.text(http.request("/api/notifications/summaries"), "Failed to fetch summaries", "{}"))
        return InboxSummaries(w.overall?.toModel(), w.apps.mapValues { it.value.toModel() }, w.refreshing)
    }

    suspend fun apps(): List<NotificationApp> =
        http.decode(AppsWire.serializer(), http.text(http.request("/api/notifications/apps"), "Failed to fetch apps", "{}")).apps.map { it.toModel() }

    /** [action]: mute | important | alert | none. */
    suspend fun setRule(packageName: String, action: String, sender: String = ""): Boolean =
        http.send(http.request("/api/notifications/rules") { post(jsonBody(ruleBody(packageName, action, sender))) })

    /** Same rule change, answered with the server's wording and an undo id (restores the rule and the buckets it moved). */
    suspend fun changeRule(packageName: String, action: String): ActResult =
        actResult(http.post("/api/notifications/rules", ruleBody(packageName, action, ""), "Couldn't save the rule"))

    suspend fun deleteForApp(packageName: String): Boolean =
        http.send(http.request("/api/notifications?package=${enc(packageName)}") { delete() })

    suspend fun overview(): NotificationsOverview =
        http.decode(OverviewWire.serializer(), http.text(http.request("/api/notifications/overview"), "Failed to load notifications", "{}")).toModel()

    /** "Dana noticed": accept (the app gets the mute rule) or decline (not offered again for 60 days). */
    suspend fun answerRuleSuggestion(packageName: String, accept: Boolean): ActResult =
        actResult(http.post("/api/notifications/rule-suggestions", buildJsonObject { put("package", packageName); put("accept", accept) }, "Couldn't save that"))

    private fun ruleBody(packageName: String, action: String, sender: String) = buildJsonObject {
        put("package", packageName); put("sender", sender); put("action", action); put("apply", true)
    }

    companion object {
        /** `[{key, until_ms}]` → key to epoch millis. */
        fun snoozeKeys(element: JsonElement?): Map<String, Long> =
            (element as? kotlinx.serialization.json.JsonArray).orEmpty().mapNotNull { v ->
                (v as? JsonObject)?.let { o -> o.text("key")?.let { k -> o.long("until_ms")?.takeIf { it > 0 }?.let { k to it } } }
            }.toMap()

        /** `["key", …]` (blanks dropped). */
        fun cancelKeys(element: JsonElement?): List<String> =
            (element as? kotlinx.serialization.json.JsonArray).orEmpty().mapNotNull { v ->
                (v as? JsonPrimitive)?.takeIf { it.isString }?.content?.takeIf(String::isNotBlank)
            }

        /**
         * An action / clearing result: `{message, undo_id, device_keys, task_id}`.
         * `device_keys` is a list of keys (dismiss) or of `{key, until_ms}` (snooze).
         */
        fun actResult(o: JsonObject, fallbackMessage: String = "Done"): ActResult {
            val keys = o["device_keys"]
            return ActResult(
                message = o.text("message") ?: o.text("label") ?: fallbackMessage,
                undoId = o.text("undo_id"),
                cancelKeys = cancelKeys(keys),
                snoozeKeys = snoozeKeys(keys),
                taskId = o.text("task_id")
            )
        }
    }
}
