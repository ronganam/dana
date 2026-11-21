package io.github.ronganam.dana.widgets

import io.github.ronganam.dana.data.network.ServerConfig
import android.content.Context
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.Constraints
import androidx.glance.appwidget.updateAll
import io.github.ronganam.dana.DanaCards
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * One snapshot for all home-screen widgets, kept in prefs (`widget_snapshot`)
 * so widgets render instantly and offline. The Shopping widget draws from
 * `ShoppingSync` itself; its fields here (`data/shopping/ShoppingSnapshot`)
 * are what the watch gets. [WidgetRefreshWorker] rebuilds it
 * every 30 minutes, when the app opens, after a widget action and when a
 * briefing push arrives.
 */
data class WidgetSnapshot(
    val briefingHeadline: String?, val briefingPoints: List<String>,
    val events: List<Pair<String, String>>,            // time, title — today's remaining
    val tasks: List<Triple<String, String, String>>,    // id, title, due label
    val suggestions: List<Triple<String, String, String>>, // id, title, source
    val suggestionCount: Int,
    val people: List<WidgetPerson>,
    val inboxNeeds: Int, val inboxActive: Int, val inboxPoints: List<String>,
    val updatedAt: Long,
) {
    companion object {
        private const val KEY = "widget_snapshot"

        fun load(context: Context): WidgetSnapshot {
            val raw = ServerConfig.prefs(context).getString(KEY, null)
            val o = runCatching { JSONObject(raw ?: "{}") }.getOrDefault(JSONObject())
            fun arr(k: String) = o.optJSONArray(k) ?: JSONArray()
            fun strs(a: JSONArray) = (0 until a.length()).map { a.optString(it) }
            fun objs(a: JSONArray) = (0 until a.length()).mapNotNull { a.optJSONObject(it) }
            // The briefing push may be newer than the last snapshot.
            val cached = DanaCards.cachedBriefing(context)
            val today = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
            val brief = cached?.takeIf { it.date == today }
            return WidgetSnapshot(
                briefingHeadline = brief?.headline ?: o.optString("briefing_headline").takeIf { it.isNotBlank() },
                briefingPoints = brief?.points ?: strs(arr("briefing_points")),
                events = objs(arr("events")).map { it.optString("time") to it.optString("title") },
                tasks = objs(arr("tasks")).map { Triple(it.optString("id"), it.optString("title"), it.optString("due")) },
                suggestions = objs(arr("suggestions")).map { Triple(it.optString("id"), it.optString("title"), it.optString("source")) },
                suggestionCount = o.optInt("suggestion_count"),
                people = objs(arr("people")).map { WidgetPerson(it.optString("id"), it.optString("name"), it.optString("initials"), it.optString("ago"), it.optString("phone"), it.optInt("open"), it.optBoolean("drifting")) },
                inboxNeeds = o.optInt("inbox_needs"), inboxActive = o.optInt("inbox_active"), inboxPoints = strs(arr("inbox_points")),
                updatedAt = o.optLong("updated_at"),
            )
        }

        fun save(context: Context, o: JSONObject) {
            ServerConfig.prefs(context).edit().putString(KEY, o.toString()).apply()
        }
    }
}

data class WidgetPerson(val id: String, val name: String, val initials: String, val ago: String, val phone: String, val open: Int, val drifting: Boolean)

class WidgetRefreshWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val ok = runCatching { build(applicationContext) }.onFailure { Log.w(TAG, "Widget refresh failed", it) }.isSuccess
        DanaWidgetsImpl.updateAll(applicationContext)
        return if (ok) Result.success() else Result.retry()
    }

    companion object {
        private const val TAG = "WidgetRefresh"

        private suspend fun <T> safe(block: suspend () -> T): T? = withTimeoutOrNull(12_000) { runCatching { block() }.getOrNull() }

        suspend fun build(context: Context) = coroutineScope {
            val api = ServerConfig.load(context).api()
            val briefing = async { safe { api.getBriefingToday() } }
            val tasks = async { safe { api.getTasks() } }
            val sugg = async { safe { api.getSuggestions("pending", "all") } }
            val people = async { safe { api.getPeople("pinned") } }
            val inbox = async { safe { api.getNotifications("active", limit = 1).second } }
            val summaries = async { safe { api.getNotificationSummaries() } }
            // The shopping list goes through the same cache and queue as the Shopping screen.
            val shopping = async { safe { io.github.ronganam.dana.data.shopping.ShoppingSync.get(context).refresh() } }
            val events = async { safe { api.getCalendarEvents() } }

            val previous = runCatching {
                JSONObject(ServerConfig.prefs(context).getString("widget_snapshot", "{}") ?: "{}")
            }.getOrDefault(JSONObject())
            val o = JSONObject(previous.toString())
            val today = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())

            briefing.await()?.let { b ->
                DanaCards.storeBriefingQuietly(context, b)
                o.put("briefing_headline", b.headline).put("briefing_points", JSONArray(b.points))
            } ?: run { if (o.optString("briefing_date") != today) { o.remove("briefing_headline"); o.remove("briefing_points") } }
            o.put("briefing_date", today)

            tasks.await()?.let { list ->
                val open = list.filter { it.status == "needs_action" }
                val ranked = open.sortedWith(compareBy(
                    { t -> t.dueDate?.take(10)?.let { if (it <= today) 0 else 1 } ?: 2 },
                    { t -> when (t.priority) { "urgent" -> 0; "high" -> 1; "medium" -> 2; else -> 3 } },
                    { t -> t.dueDate ?: "9999" },
                ))
                o.put("tasks", JSONArray(ranked.take(6).map { t ->
                    val due = t.dueDate?.take(10)
                    JSONObject().put("id", t.id).put("title", t.title).put("due", when {
                        due == null -> ""
                        due < today -> "overdue"
                        due == today -> "today"
                        else -> due.substring(5)
                    })
                }))
            }
            sugg.await()?.let { list ->
                o.put("suggestion_count", list.size)
                o.put("suggestions", JSONArray(list.take(3).map { s ->
                    JSONObject().put("id", s.id).put("title", s.payload.optString("title"))
                        .put("source", if (s.sourceType == "notification") "notification" else "call")
                }))
            }
            people.await()?.let { list ->
                val shown = list.filter { it.pinned }.ifEmpty { list }.take(6)
                o.put("people", JSONArray(shown.map { p ->
                    JSONObject().put("id", p.id).put("name", p.name).put("initials", p.initials)
                        .put("ago", p.stats.lastContactAgo).put("phone", p.phones.firstOrNull()?.let { "+$it" } ?: "")
                        .put("open", p.openTasks).put("drifting", p.stats.drifting)
                }))
            }
            inbox.await()?.let { c -> o.put("inbox_needs", c.needsYou).put("inbox_active", c.active) }
            summaries.await()?.let { s -> o.put("inbox_points", JSONArray(s.overall?.points.orEmpty().take(3))) }
            shopping.await()
            io.github.ronganam.dana.data.shopping.ShoppingSnapshot.merge(
                o, io.github.ronganam.dana.data.shopping.ShoppingSnapshot.fields(io.github.ronganam.dana.data.shopping.ShoppingSync.get(context).shown())
            )
            events.await()?.let { raw ->
                val arr = runCatching { JSONArray(raw) }.getOrElse { runCatching { JSONObject(raw).optJSONArray("events") }.getOrNull() ?: JSONArray() }
                o.put("events", JSONArray(todaysEvents(arr).take(4).map { JSONObject().put("time", it.first).put("title", it.second) }))
            }
            o.put("updated_at", System.currentTimeMillis())
            WidgetSnapshot.save(context, o)
            io.github.ronganam.dana.data.wear.WearCards.sendSnapshot(context, o)
        }

        /** Today's events that haven't ended, as (HH:mm | "all day", title), in the phone's zone. */
        fun todaysEvents(arr: JSONArray): List<Pair<String, String>> {
            val zone = java.time.ZoneId.systemDefault()
            val now = java.time.ZonedDateTime.now(zone)
            val today = now.toLocalDate()
            fun parse(s: String): java.time.ZonedDateTime? = runCatching {
                java.time.OffsetDateTime.parse(s.replace("Z", "+00:00")).atZoneSameInstant(zone)
            }.getOrNull()
            return (0 until arr.length()).mapNotNull { arr.optJSONObject(it) }.mapNotNull { e ->
                val start = e.optString("start")
                val title = e.optString("summary").ifBlank { e.optString("title") }
                if (start.length <= 10) {
                    if (start == today.toString()) "all day" to title else null
                } else {
                    val s = parse(start) ?: return@mapNotNull null
                    val end = parse(e.optString("end")) ?: s
                    if (s.toLocalDate() != today || end.isBefore(now)) null
                    else "%02d:%02d".format(s.hour, s.minute) to title
                }
            }.sortedBy { if (it.first == "all day") "" else it.first }
        }

        fun schedule(context: Context) {
            val wm = WorkManager.getInstance(context)
            wm.enqueueUniquePeriodicWork(
                "dana_widgets", ExistingPeriodicWorkPolicy.KEEP,
                PeriodicWorkRequestBuilder<WidgetRefreshWorker>(30, TimeUnit.MINUTES)
                    .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                    .build()
            )
        }

        fun refreshNow(context: Context) {
            WorkManager.getInstance(context).enqueueUniqueWork(
                "dana_widgets_now", ExistingWorkPolicy.REPLACE,
                OneTimeWorkRequestBuilder<WidgetRefreshWorker>().build()
            )
        }
    }
}

/** What [io.github.ronganam.dana.DanaWidgets] forwards to in the phone flavor. */
object DanaWidgetsImpl {
    suspend fun updateAll(context: Context) {
        runCatching {
            TodayWidget().updateAll(context)
            WaitingWidget().updateAll(context)
            PeopleWidget().updateAll(context)
            InboxWidget().updateAll(context)
            ShoppingWidget().updateAll(context)
        }.onFailure { Log.w("DanaWidgets", "Widget update failed", it) }
    }
}
