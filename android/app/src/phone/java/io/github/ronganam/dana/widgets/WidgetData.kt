package io.github.ronganam.dana.widgets

import io.github.ronganam.dana.data.network.text
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
import io.github.ronganam.dana.protocol.DanaSnapshot
import io.github.ronganam.dana.protocol.SnapshotEvent
import io.github.ronganam.dana.protocol.SnapshotPerson
import io.github.ronganam.dana.protocol.SnapshotSuggestion
import io.github.ronganam.dana.protocol.SnapshotTask
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.TimeUnit
import androidx.core.content.edit

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

        /** The stored snapshot ([DanaSnapshot]); an empty one when there is none yet. */
        fun stored(context: Context): DanaSnapshot =
            DanaSnapshot.decode(ServerConfig.prefs(context).getString(KEY, null)) ?: DanaSnapshot()

        fun load(context: Context): WidgetSnapshot {
            val o = stored(context)
            // The briefing push may be newer than the last snapshot.
            val cached = DanaCards.cachedBriefing(context)
            val today = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
            val brief = cached?.takeIf { it.date == today }
            return WidgetSnapshot(
                briefingHeadline = brief?.headline ?: o.briefingHeadline?.takeIf { it.isNotBlank() },
                briefingPoints = brief?.points ?: o.briefingPoints,
                events = o.events.map { it.time to it.title },
                tasks = o.tasks.map { Triple(it.id, it.title, it.due) },
                suggestions = o.suggestions.map { Triple(it.id, it.title, it.source) },
                suggestionCount = o.suggestionCount,
                people = o.people.map { WidgetPerson(it.id, it.name, it.initials, it.ago, it.phone, it.open, it.drifting) },
                inboxNeeds = o.inboxNeeds ?: 0, inboxActive = o.inboxActive, inboxPoints = o.inboxPoints,
                updatedAt = o.updatedAt,
            )
        }

        fun save(context: Context, snapshot: DanaSnapshot, commit: Boolean = false) {
            ServerConfig.prefs(context).edit(commit = commit) { putString(KEY, snapshot.encode()) }
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
            val briefing = async { safe { api.people.briefingToday() } }
            val tasks = async { safe { api.tasks.tasks() } }
            val sugg = async { safe { api.calls.suggestions("pending", "all") } }
            val people = async { safe { api.people.people("pinned") } }
            val inbox = async { safe { api.notifications.list("active", limit = 1).second } }
            val summaries = async { safe { api.notifications.summaries() } }
            // The shopping list goes through the same cache and queue as the Shopping screen.
            val shopping = async { safe { io.github.ronganam.dana.data.shopping.ShoppingSync.get(context).refresh() } }
            val events = async { safe { api.tasks.calendarEvents() } }

            var o = WidgetSnapshot.stored(context)
            val today = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())

            o = briefing.await()?.let { b ->
                DanaCards.storeBriefingQuietly(context, b)
                o.copy(briefingHeadline = b.headline, briefingPoints = b.points)
            } ?: if (o.briefingDate != today) o.copy(briefingHeadline = null, briefingPoints = emptyList()) else o
            o = o.copy(briefingDate = today)

            tasks.await()?.let { list ->
                val open = list.filter { it.status == "needs_action" }
                val ranked = open.sortedWith(compareBy(
                    { t -> t.dueDate?.take(10)?.let { if (it <= today) 0 else 1 } ?: 2 },
                    { t -> when (t.priority) { "urgent" -> 0; "high" -> 1; "medium" -> 2; else -> 3 } },
                    { t -> t.dueDate ?: "9999" },
                ))
                o = o.copy(tasks = ranked.take(6).map { t ->
                    val due = t.dueDate?.take(10)
                    SnapshotTask(t.id, t.title, when {
                        due == null -> ""
                        due < today -> "overdue"
                        due == today -> "today"
                        else -> due.substring(5)
                    })
                })
            }
            sugg.await()?.let { list ->
                o = o.copy(
                    suggestionCount = list.size,
                    suggestions = list.take(3).map { s ->
                        SnapshotSuggestion(s.id, s.payload.text("title").orEmpty(), if (s.sourceType == "notification") "notification" else "call")
                    }
                )
            }
            people.await()?.let { list ->
                val shown = list.filter { it.pinned }.ifEmpty { list }.take(6)
                o = o.copy(people = shown.map { p ->
                    SnapshotPerson(
                        id = p.id, name = p.name, initials = p.initials, ago = p.stats.lastContactAgo,
                        phone = p.phones.firstOrNull()?.let { "+$it" } ?: "", open = p.openTasks, drifting = p.stats.drifting
                    )
                })
            }
            inbox.await()?.let { c -> o = o.copy(inboxNeeds = c.needsYou, inboxActive = c.active) }
            summaries.await()?.let { s -> o = o.copy(inboxPoints = s.overall?.points.orEmpty().take(3)) }
            shopping.await()
            o = io.github.ronganam.dana.data.shopping.ShoppingSnapshot.apply(o, io.github.ronganam.dana.data.shopping.ShoppingSync.get(context).shown())
            events.await()?.let { list ->
                o = o.copy(events = todaysEvents(list).take(4).map { SnapshotEvent(it.first, it.second) })
            }
            o = o.copy(updatedAt = System.currentTimeMillis())
            WidgetSnapshot.save(context, o)
            io.github.ronganam.dana.data.wear.WearCards.sendSnapshot(context, o)
        }

        /** Today's events that haven't ended, as (HH:mm | "all day", title), in the phone's zone. */
        fun todaysEvents(events: List<io.github.ronganam.dana.data.models.CalendarEvent>): List<Pair<String, String>> {
            val zone = java.time.ZoneId.systemDefault()
            val now = java.time.ZonedDateTime.now(zone)
            val today = now.toLocalDate()
            fun parse(s: String): java.time.ZonedDateTime? = runCatching {
                java.time.OffsetDateTime.parse(s.replace("Z", "+00:00")).atZoneSameInstant(zone)
            }.getOrNull()
            return events.mapNotNull { e ->
                val start = e.start.orEmpty()
                val title = e.title
                if (start.length <= 10) {
                    if (start == today.toString()) "all day" to title else null
                } else {
                    val s = parse(start) ?: return@mapNotNull null
                    val end = parse(e.end.orEmpty()) ?: s
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
