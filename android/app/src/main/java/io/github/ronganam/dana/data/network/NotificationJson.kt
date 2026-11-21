package io.github.ronganam.dana.data.network

import io.github.ronganam.dana.data.models.InboxSummaries
import io.github.ronganam.dana.data.models.InboxSummary
import io.github.ronganam.dana.data.models.NotificationApp
import io.github.ronganam.dana.data.models.NotificationCounts
import io.github.ronganam.dana.data.models.PhoneNotification
import org.json.JSONArray
import org.json.JSONObject

/** JSON → models for `/api/notifications*`. */
object NotificationJson {

    private fun JSONObject.str(key: String): String? =
        optString(key).takeIf { has(key) && !isNull(key) && it.isNotEmpty() && it != "null" }

    fun notification(obj: JSONObject): PhoneNotification = PhoneNotification(
        id = obj.optString("id"),
        key = obj.str("key"),
        packageName = obj.optString("package"),
        appName = obj.str("app_name") ?: obj.optString("package"),
        title = obj.str("title"),
        text = obj.str("text"),
        sender = obj.str("sender"),
        conversation = obj.str("conversation"),
        isGroup = obj.optBoolean("is_group", false),
        postedAt = obj.str("posted_at"),
        status = obj.optString("status", "active"),
        removedReason = obj.str("removed_reason"),
        triageStatus = obj.optString("triage_status", "pending"),
        category = obj.str("category"),
        categoryEmoji = obj.optString("category_emoji", "🔔").ifEmpty { "🔔" },
        priority = obj.optString("priority", "normal").ifEmpty { "normal" },
        bucket = obj.optString("bucket", "normal").ifEmpty { "normal" },
        escalated = obj.optBoolean("escalated", false),
        suggestions = CallJson.suggestions(obj.optJSONArray("suggestions") ?: JSONArray()),
        snoozedUntil = obj.str("snoozed_until")
    )

    fun notifications(arr: JSONArray?): List<PhoneNotification> {
        if (arr == null) return emptyList()
        return (0 until arr.length()).mapNotNull { arr.optJSONObject(it)?.let(::notification) }
    }

    fun counts(obj: JSONObject?): NotificationCounts = if (obj == null) NotificationCounts() else NotificationCounts(
        needsYou = obj.optInt("needs_you"),
        normal = obj.optInt("normal"),
        dismiss = obj.optInt("dismiss"),
        active = obj.optInt("active"),
        snoozed = obj.optInt("snoozed"),
        pendingSuggestions = obj.optInt("pending_suggestions"),
        untriaged = obj.optInt("untriaged")
    )

    /** `[{key, until_ms}]` → key to epoch millis. */
    fun snoozeKeys(arr: JSONArray?): Map<String, Long> {
        if (arr == null) return emptyMap()
        return (0 until arr.length()).mapNotNull { i ->
            arr.optJSONObject(i)?.let { o -> o.str("key")?.let { it to o.optLong("until_ms") } }
        }.filter { it.second > 0 }.toMap()
    }

    fun summaries(json: JSONObject?): InboxSummaries {
        if (json == null) return InboxSummaries()
        fun one(o: JSONObject?) = o?.let {
            val summary = it.str("summary")
            val pts = it.optJSONArray("points")?.let { a -> (0 until a.length()).mapNotNull { i -> a.optString(i).takeIf { s -> s.isNotBlank() } } }
            InboxSummary(summary = summary, count = it.optInt("count"), stale = it.optBoolean("stale"),
                points = pts ?: summary?.split('\n')?.filter { s -> s.isNotBlank() }.orEmpty())
        }
        val apps = json.optJSONObject("apps")
        return InboxSummaries(
            overall = one(json.optJSONObject("overall")),
            apps = apps?.keys()?.asSequence()?.mapNotNull { k -> one(apps.optJSONObject(k))?.let { k to it } }?.toMap().orEmpty(),
            refreshing = json.optBoolean("refreshing")
        )
    }

    fun apps(arr: JSONArray?): List<NotificationApp> {
        if (arr == null) return emptyList()
        return (0 until arr.length()).mapNotNull { i ->
            arr.optJSONObject(i)?.let {
                NotificationApp(
                    packageName = it.optString("package"),
                    appName = it.str("app_name") ?: it.optString("package"),
                    total = it.optInt("total"),
                    opened = it.optInt("opened"),
                    swiped = it.optInt("swiped"),
                    suggestedDismiss = it.optInt("suggested_dismiss"),
                    rule = it.str("rule")
                )
            }
        }
    }
}
