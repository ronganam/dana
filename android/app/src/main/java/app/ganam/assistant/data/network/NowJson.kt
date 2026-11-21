package app.ganam.assistant.data.network

import app.ganam.assistant.data.models.ActResult
import app.ganam.assistant.data.models.DaySlotInfo
import app.ganam.assistant.data.models.DaySlotItem
import app.ganam.assistant.data.models.NotificationsOverview
import app.ganam.assistant.data.models.NowAction
import app.ganam.assistant.data.models.NowBlock
import app.ganam.assistant.data.models.NowItem
import app.ganam.assistant.data.models.NowNotification
import app.ganam.assistant.data.models.NowNotificationButton
import app.ganam.assistant.data.models.NowNotificationCounts
import app.ganam.assistant.data.models.NowPayload
import app.ganam.assistant.data.models.NowRef
import app.ganam.assistant.data.models.OverviewApp
import app.ganam.assistant.data.models.OverviewCounts
import app.ganam.assistant.data.models.QuietEntry
import app.ganam.assistant.data.models.RuleSuggestion
import app.ganam.assistant.data.models.SearchResult
import org.json.JSONArray
import org.json.JSONObject

/**
 * JSON → models for `/api/now`, `/api/now/act`, `/api/notifications/overview`
 * and `/api/search`. Lenient on purpose: missing fields get defaults, unknown
 * block types become [NowBlock.Unknown], rows without an id are dropped.
 */
object NowJson {

    private fun JSONObject.str(key: String): String? =
        optString(key).takeIf { has(key) && !isNull(key) && it.isNotEmpty() && it != "null" }

    private inline fun <T> JSONArray?.objects(f: (JSONObject) -> T?): List<T> =
        if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it)?.let(f) }

    private fun JSONArray?.strings(): List<String> =
        if (this == null) emptyList() else (0 until length()).mapNotNull { i ->
            if (isNull(i)) null else optString(i).takeIf { it.isNotBlank() }
        }

    /** A JSON value as text that [org.json] can read back (`JSONObject("{\"v\":$text}")`). */
    private fun valueText(v: Any?): String = when (v) {
        null, JSONObject.NULL -> "null"
        is JSONObject, is JSONArray -> v.toString()
        is String -> JSONObject.quote(v)
        is Boolean, is Number -> v.toString()
        else -> JSONObject.quote(v.toString())
    }

    fun payload(o: JSONObject): NowPayload {
        val n = o.optJSONObject("notifications")
        val day = o.optJSONObject("day")
        return NowPayload(
            now = o.str("now"),
            lede = o.str("lede"),
            items = o.optJSONArray("items").objects(::item),
            quiet = o.optJSONArray("quiet").objects(::quiet),
            quietTotal = o.optInt("quiet_total", o.optJSONArray("quiet")?.length() ?: 0),
            notifications = NowNotificationCounts(
                total = n?.optInt("total") ?: 0,
                noise = n?.optInt("noise") ?: 0,
                needsYou = n?.optInt("needs_you") ?: 0
            ),
            daySlots = day?.optJSONArray("slots").objects(::slot),
            calendarOk = day?.optBoolean("calendar", true) ?: true
        )
    }

    fun action(o: JSONObject?): NowAction? {
        if (o == null) return null
        val action = o.str("action") ?: return null
        return NowAction(
            action = action,
            label = o.str("label") ?: action.replaceFirstChar { it.uppercase() },
            argsJson = o.optJSONObject("args")?.takeIf { it.length() > 0 }?.toString(),
            client = o.optBoolean("client", false)
        )
    }

    private fun ref(o: JSONObject?): NowRef? {
        if (o == null) return null
        val type = o.str("type") ?: return null
        val id = o.str("id") ?: return null
        return NowRef(type, id)
    }

    fun item(o: JSONObject): NowItem? {
        val id = o.str("id") ?: return null
        return NowItem(
            id = id,
            kind = o.str("kind") ?: id.substringBefore(':'),
            source = o.str("source").orEmpty(),
            title = o.str("title").orEmpty(),
            why = o.str("why").orEmpty(),
            primary = action(o.optJSONObject("primary")),
            secondary = o.optJSONArray("secondary").objects(::action),
            schedulable = o.optBoolean("schedulable", false),
            ref = ref(o.optJSONObject("ref")),
            notification = o.optJSONObject("notification")?.let(::notification),
            blocks = o.optJSONArray("blocks").objects(::block)
        )
    }

    private fun notification(o: JSONObject): NowNotification? {
        val id = o.str("id") ?: return null
        return NowNotification(
            id = id,
            key = o.str("key"),
            packageName = o.optString("package"),
            app = o.str("app"),
            canReply = o.optBoolean("can_reply", false),
            onPhone = o.optBoolean("on_phone", false),
            actions = o.optJSONArray("actions").objects { a ->
                a.str("title")?.let { NowNotificationButton(it, a.optBoolean("reply", false)) }
            }
        )
    }

    fun block(o: JSONObject): NowBlock {
        val type = o.optString("type")
        val label = o.str("label")
        return when (type) {
            "lines" -> NowBlock.Lines(label, o.optJSONArray("lines").objects { l ->
                l.str("text")?.let { NowBlock.Lines.Line(it, l.str("who"), l.optBoolean("hit", false)) }
            })
            "chat" -> NowBlock.Chat(label, o.optJSONArray("messages").objects { m ->
                m.str("text")?.let { NowBlock.Chat.Message(it, m.optBoolean("mine", false), m.str("who")) }
            })
            "facts" -> NowBlock.Facts(label, o.optJSONArray("items").strings())
            "text" -> NowBlock.Text(label, o.str("text").orEmpty())
            "week" -> NowBlock.Week(label, o.optJSONArray("cells").objects { c ->
                NowBlock.Week.Cell(
                    label = c.optString("label"),
                    value = c.str("value") ?: "–",
                    ok = if (!c.has("ok") || c.isNull("ok")) null else c.optBoolean("ok")
                )
            })
            "choices" -> NowBlock.Choices(label, o.optString("key"), o.optJSONArray("options").objects { c ->
                NowBlock.Choices.Option(
                    label = c.optString("label"),
                    valueJson = valueText(c.opt("value")),
                    selected = c.optBoolean("selected", false),
                    actionLabel = c.str("action_label"),
                    message = c.str("message")
                )
            })
            "checks" -> NowBlock.Checks(label, o.optString("key"), o.optJSONArray("options").objects { c ->
                NowBlock.Checks.Option(c.optString("label"), valueText(c.opt("value")), c.optBoolean("checked", false))
            })
            else -> NowBlock.Unknown(type, label)
        }
    }

    private fun quiet(o: JSONObject): QuietEntry? {
        val id = o.str("id") ?: return null
        return QuietEntry(
            id = id,
            kind = o.str("kind") ?: "suggestion",
            title = o.str("title").orEmpty(),
            source = o.str("source").orEmpty(),
            primary = action(o.optJSONObject("primary")),
            secondary = o.optJSONArray("secondary").objects(::action),
            ref = ref(o.optJSONObject("ref"))
        )
    }

    private fun slot(o: JSONObject): DaySlotInfo? {
        val key = o.str("key") ?: return null
        return DaySlotInfo(key, o.str("label") ?: key, o.optJSONArray("items").objects { i ->
            DaySlotItem(i.optString("title"), i.optString("kind"), i.str("time"), i.str("task_id"), i.str("event_id"))
        })
    }

    /**
     * An action / clearing result: `{message, undo_id, device_keys, task_id}`.
     * `device_keys` is a list of keys (dismiss) or of `{key, until_ms}` (snooze).
     */
    fun actResult(o: JSONObject, fallbackMessage: String = "Done"): ActResult {
        val keys = o.optJSONArray("device_keys")
        val cancel = mutableListOf<String>()
        val snooze = mutableMapOf<String, Long>()
        if (keys != null) for (i in 0 until keys.length()) {
            val v = keys.opt(i)
            when (v) {
                is String -> if (v.isNotBlank()) cancel += v
                is JSONObject -> v.str("key")?.let { k -> v.optLong("until_ms").takeIf { it > 0 }?.let { snooze[k] = it } }
            }
        }
        return ActResult(
            message = o.str("message") ?: o.str("label") ?: fallbackMessage,
            undoId = o.str("undo_id"),
            cancelKeys = cancel,
            snoozeKeys = snooze,
            taskId = o.str("task_id")
        )
    }

    // --- Notifications screen ------------------------------------------------------------

    fun overview(o: JSONObject): NotificationsOverview {
        val c = o.optJSONObject("counts")
        return NotificationsOverview(
            counts = OverviewCounts(
                total = c?.optInt("total") ?: 0,
                noise = c?.optInt("noise") ?: 0,
                needsYou = c?.optInt("needs_you") ?: 0,
                snoozed = c?.optInt("snoozed") ?: 0
            ),
            apps = o.optJSONArray("apps").objects(::app),
            quietApps = o.optJSONArray("quiet_apps").objects(::app),
            ruleSuggestions = o.optJSONArray("rule_suggestions").objects { r ->
                val pkg = r.str("package") ?: return@objects null
                RuleSuggestion(
                    packageName = pkg,
                    app = r.str("app") ?: pkg,
                    action = r.str("action") ?: "quiet",
                    cleared = r.optInt("cleared"),
                    opened = r.optInt("opened"),
                    reason = r.str("reason").orEmpty()
                )
            },
            summariesRefreshing = o.optBoolean("summaries_refreshing", false)
        )
    }

    private fun app(o: JSONObject): OverviewApp? {
        val pkg = o.str("package") ?: return null
        return OverviewApp(
            packageName = pkg,
            app = o.str("app") ?: pkg,
            rule = o.str("rule") ?: "normal",
            ruleAction = o.str("rule_action"),
            count = o.optInt("count"),
            needsYou = o.optInt("needs_you"),
            noise = o.optInt("noise"),
            summary = o.optJSONArray("summary").strings(),
            summarySource = o.str("summary_source"),
            latestAt = o.str("latest_at")
        )
    }

    fun searchResults(o: JSONObject): List<SearchResult> = o.optJSONArray("results").objects { r ->
        val id = r.str("id") ?: return@objects null
        SearchResult(r.optString("kind"), id, r.str("title").orEmpty(), r.str("subtitle").orEmpty(), r.str("package"), r.str("project_id"))
    }
}
