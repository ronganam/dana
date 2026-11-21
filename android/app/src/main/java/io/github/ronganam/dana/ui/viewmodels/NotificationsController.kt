package io.github.ronganam.dana.ui.viewmodels

import io.github.ronganam.dana.SuggestionsReceiver
import android.content.Context
import io.github.ronganam.dana.data.models.CallSuggestion
import io.github.ronganam.dana.data.models.InboxSummaries
import io.github.ronganam.dana.data.models.NotificationApp
import io.github.ronganam.dana.data.models.NotificationCounts
import io.github.ronganam.dana.data.models.PhoneNotification
import io.github.ronganam.dana.data.notifications.DanaNotificationListener
import io.github.ronganam.dana.data.notifications.NotificationSettings
import io.github.ronganam.dana.data.notifications.NotificationSyncWorker
import io.github.ronganam.dana.data.notifications.Snoozer
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.json.JSONObject

/** Which slice of the Inbox is shown. */
enum class InboxView(val label: String) {
    NEEDS_YOU("Needs you"),
    ALL("Everything"),
    SUGGESTED_DISMISS("Suggested to dismiss"),
    SNOOZED("Snoozed"),
    HISTORY("Dismissed")
}

/**
 * The Inbox tab: phone notifications triaged by the server plus every pending
 * suggestion (from calls and notifications). Dismissals are optimistic and
 * undoable; dismissing in Dana also clears the notification from the shade.
 */
class NotificationsController(
    private val ctx: ControllerContext,
    private val appContext: Context,
    /** Something was created from a suggestion (refresh reminders, call suggestions). */
    private val onSuggestionsChanged: () -> Unit
) {
    private val _items = MutableStateFlow<List<PhoneNotification>>(emptyList())
    val items: StateFlow<List<PhoneNotification>> = _items.asStateFlow()

    private val _history = MutableStateFlow<List<PhoneNotification>>(emptyList())
    val history: StateFlow<List<PhoneNotification>> = _history.asStateFlow()

    private val _snoozed = MutableStateFlow<List<PhoneNotification>>(emptyList())
    /** Snoozed, soonest back first. */
    val snoozed: StateFlow<List<PhoneNotification>> = _snoozed.asStateFlow()

    private val _counts = MutableStateFlow(NotificationCounts())
    val counts: StateFlow<NotificationCounts> = _counts.asStateFlow()

    private val _suggestions = MutableStateFlow<List<CallSuggestion>>(emptyList())
    /** Pending suggestions from every source, newest first. */
    val suggestions: StateFlow<List<CallSuggestion>> = _suggestions.asStateFlow()

    private val _busy = MutableStateFlow<Set<String>>(emptySet())
    val busySuggestionIds: StateFlow<Set<String>> = _busy.asStateFlow()

    private val _loading = MutableStateFlow(false)
    val loading: StateFlow<Boolean> = _loading.asStateFlow()

    private val _loadedOnce = MutableStateFlow(false)
    val loadedOnce: StateFlow<Boolean> = _loadedOnce.asStateFlow()

    private val _view = MutableStateFlow(InboxView.NEEDS_YOU)
    val view: StateFlow<InboxView> = _view.asStateFlow()

    private val _summaries = MutableStateFlow(InboxSummaries())
    /** AI summary per app + the Inbox briefing. */
    val summaries: StateFlow<InboxSummaries> = _summaries.asStateFlow()
    private var summaryRecheck: Job? = null

    private val _apps = MutableStateFlow<List<NotificationApp>>(emptyList())
    val apps: StateFlow<List<NotificationApp>> = _apps.asStateFlow()

    private val _accessGranted = MutableStateFlow(NotificationSettings.accessGranted(appContext))
    val accessGranted: StateFlow<Boolean> = _accessGranted.asStateFlow()

    private var pollJob: Job? = null

    private val prefs = appContext.getSharedPreferences("inbox", Context.MODE_PRIVATE)

    private val _groupByApp = MutableStateFlow(prefs.getBoolean("group_by_app", false))
    /** Show notifications in per-app sections (remembered). */
    val groupByApp: StateFlow<Boolean> = _groupByApp.asStateFlow()

    fun setGroupByApp(on: Boolean) {
        _groupByApp.value = on
        prefs.edit().putBoolean("group_by_app", on).apply()
    }

    fun setView(v: InboxView) {
        _view.value = v
        if (v == InboxView.HISTORY) fetchHistory()
        if (v == InboxView.SNOOZED) fetchSnoozed()
    }

    fun refreshAccess() {
        _accessGranted.value = NotificationSettings.accessGranted(appContext)
    }

    /** Active notifications + all pending suggestions. Also pushes the outbox. */
    fun fetch() {
        refreshAccess()
        if (_accessGranted.value) NotificationSyncWorker.syncNow(appContext)
        ctx.scope.launch {
            _loading.value = true
            try {
                ctx.load("Notifications") { ctx.api.getNotifications("active") }?.let { (list, counts) ->
                    _items.value = list
                    _counts.value = counts
                    _loadedOnce.value = true
                }
                if (_view.value == InboxView.SNOOZED) fetchSnoozed()
                fetchSummaries()
                ctx.load("InboxSuggestions") { ctx.api.getSuggestions("pending", source = "all") }?.let {
                    _suggestions.value = it
                    SuggestionsReceiver.reconcile(appContext, it.map { s -> s.id }.toSet())
                }
            } finally {
                _loading.value = false
            }
        }
    }

    /** A rewrite running on the server shows up a few seconds later, not at the next 20 s poll. */
    private suspend fun fetchSummaries() {
        ctx.load("InboxSummaries") { ctx.api.getNotificationSummaries() }?.let { s ->
            _summaries.value = s
            if (s.refreshing && summaryRecheck?.isActive != true) {
                summaryRecheck = ctx.scope.launch {
                    repeat(3) {
                        delay(4_000)
                        val next = ctx.load("InboxSummaries") { ctx.api.getNotificationSummaries() } ?: return@launch
                        _summaries.value = next
                        if (!next.refreshing) return@launch
                    }
                }
            }
        }
    }

    fun fetchHistory() {
        ctx.scope.launch {
            ctx.load("NotificationHistory") { ctx.api.getNotifications("dismissed", limit = 200) }?.let { (list, _) ->
                _history.value = list
            }
        }
    }

    fun fetchSnoozed() {
        ctx.scope.launch {
            ctx.load("SnoozedNotifications") { ctx.api.getNotifications("snoozed", limit = 300) }?.let { (list, counts) ->
                _snoozed.value = list.sortedBy { it.snoozedUntil ?: "" }
                _counts.value = counts
            }
        }
    }

    // --- Snooze -------------------------------------------------------------------

    private fun PhoneNotification.target() = Snoozer.Target(id, key, appName, title)

    /** Snooze [list] until [untilMillis] (optimistic). Returns an undo action. */
    fun snooze(list: List<PhoneNotification>, untilMillis: Long): () -> Unit {
        if (list.isEmpty()) return {}
        val ids = list.map { it.id }.toSet()
        val before = _items.value
        _items.value = before.filterNot { it.id in ids }
        _counts.value = recount(_items.value, _counts.value).let { it.copy(snoozed = it.snoozed + list.size) }
        ctx.scope.launch {
            try {
                Snoozer.snooze(appContext, ctx.api, list.map { it.target() }, untilMillis)
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                _items.value = (_items.value.filterNot { it.id in ids } + list).sortedByDescending { it.postedAt }
                _counts.value = recount(_items.value, _counts.value).let { it.copy(snoozed = (it.snoozed - list.size).coerceAtLeast(0)) }
                ctx.messages.post("Couldn't snooze: ${describeError(e)}")
            }
        }
        return { wake(list) }
    }

    /** Back in the Inbox now (undo / Unsnooze). */
    fun wake(list: List<PhoneNotification>) {
        if (list.isEmpty()) return
        val ids = list.map { it.id }.toSet()
        _snoozed.value = _snoozed.value.filterNot { it.id in ids }
        _items.value = (_items.value.filterNot { it.id in ids } + list.map { it.copy(snoozedUntil = null) })
            .sortedByDescending { it.postedAt }
        _counts.value = recount(_items.value, _counts.value).let { it.copy(snoozed = (it.snoozed - list.size).coerceAtLeast(0)) }
        ctx.action("Couldn't unsnooze") {
            Snoozer.wake(appContext, ctx.api, ids.toList(), list.mapNotNull { it.key })
            true
        }
    }

    /** While the Inbox is on screen, new notifications and triage results show up by themselves. */
    fun startPolling() {
        if (pollJob?.isActive == true) return
        pollJob = ctx.scope.launch {
            while (true) {
                fetch()
                delay(20_000)
            }
        }
    }

    fun stopPolling() {
        pollJob?.cancel()
        pollJob = null
    }

    // --- Dismiss / restore ----------------------------------------------------

    /**
     * Dismiss [ids] now (optimistic). Returns an undo action for the snackbar.
     * With [dismissSuggestions] their pending suggestions go too.
     */
    fun dismiss(ids: Collection<String>, dismissSuggestions: Boolean = false): () -> Unit {
        val idSet = ids.toSet()
        if (idSet.isEmpty()) return {}
        val before = _items.value
        val removed = before.filter { it.id in idSet } + _snoozed.value.filter { it.id in idSet }
        _items.value = before.filterNot { it.id in idSet }
        _snoozed.value = _snoozed.value.filterNot { it.id in idSet }
        _counts.value = recount(_items.value, _counts.value)
        val suggestionsBefore = _suggestions.value
        if (dismissSuggestions) {
            _suggestions.value = suggestionsBefore.filterNot { it.fromNotification && it.sourceId in idSet }
        }
        idSet.forEach { id ->
            (appContext.getSystemService(Context.NOTIFICATION_SERVICE) as android.app.NotificationManager)
                .cancel("alert:$id".hashCode())
        }
        if (dismissSuggestions) reconcileSuggestionNotifications()
        ctx.scope.launch {
            try {
                val keys = ctx.api.setNotificationStatus(idSet.toList(), "dismissed", dismissSuggestions = dismissSuggestions)
                DanaNotificationListener.cancel(keys)
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                _items.value = (_items.value + removed).sortedByDescending { it.postedAt }
                _suggestions.value = suggestionsBefore
                _counts.value = recount(_items.value, _counts.value)
                ctx.messages.post("Couldn't dismiss: ${describeError(e)}")
            }
        }
        return { restore(removed) }
    }

    /** "Dismiss all" on the Suggested-to-dismiss view. */
    fun dismissAllSuggested(): Pair<Int, () -> Unit> {
        val ids = _items.value.filter { it.bucket == "dismiss" }.map { it.id }
        return ids.size to dismiss(ids)
    }

    fun restore(list: List<PhoneNotification>) {
        if (list.isEmpty()) return
        val ids = list.map { it.id }
        _items.value = (_items.value.filterNot { it.id in ids } + list.map { it.copy(status = "active") })
            .sortedByDescending { it.postedAt }
        _history.value = _history.value.filterNot { it.id in ids }
        _counts.value = recount(_items.value, _counts.value)
        ctx.action("Couldn't restore") {
            ctx.api.setNotificationStatus(ids, "active")
            true
        }
    }

    private fun recount(list: List<PhoneNotification>, old: NotificationCounts) = old.copy(
        needsYou = list.count { it.bucket == "needs_you" },
        normal = list.count { it.bucket == "normal" },
        dismiss = list.count { it.bucket == "dismiss" },
        active = list.size
    )

    // --- Rules ------------------------------------------------------------------

    /** [action]: mute | important | alert | none. Re-buckets that app's notifications on the server. */
    fun setRule(n: PhoneNotification, action: String) {
        ctx.scope.launch {
            try {
                if (ctx.api.setNotificationRule(n.packageName, action)) {
                    ctx.messages.post(
                        when (action) {
                            "mute" -> "${n.appName} will be suggested to dismiss"
                            "important" -> "${n.appName} will always need you"
                            "alert" -> "New ${n.appName} notifications will open the alert screen"
                            else -> "${n.appName} is triaged normally again"
                        }
                    )
                    fetch()
                    fetchApps()
                } else ctx.messages.post("Couldn't save the rule")
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                ctx.messages.post("Couldn't save the rule: ${describeError(e)}")
            }
        }
    }

    /** One rule for several apps (bulk action on a selection). */
    fun setRuleForApps(packages: Collection<String>, action: String) {
        val list = packages.distinct()
        if (list.isEmpty()) return
        ctx.scope.launch {
            try {
                val ok = list.count { ctx.api.setNotificationRule(it, action) }
                ctx.messages.post(
                    when (action) {
                        "mute" -> "$ok app${if (ok == 1) "" else "s"} will be suggested to dismiss"
                        "important" -> "$ok app${if (ok == 1) "" else "s"} will always need you"
                        else -> "$ok app${if (ok == 1) "" else "s"} triaged normally again"
                    }
                )
                fetch()
                fetchApps()
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                ctx.messages.post("Couldn't save the rule: ${describeError(e)}")
            }
        }
    }

    fun setAppRule(packageName: String, action: String) {
        ctx.action("Couldn't save the rule") {
            ctx.api.setNotificationRule(packageName, action).also { if (it) { fetchApps(); fetch() } }
        }
    }

    /** Stop capturing an app on this phone and forget what the server has from it. */
    fun stopCapturing(packageName: String) {
        NotificationSettings.deny(appContext, packageName)
        _items.value = _items.value.filterNot { it.packageName == packageName }
        _counts.value = recount(_items.value, _counts.value)
        ctx.action("Couldn't delete that app's notifications") {
            ctx.api.deleteNotificationsForApp(packageName).also { if (it) { fetch(); fetchApps() } }
        }
    }

    fun fetchApps() {
        ctx.scope.launch { ctx.load("NotificationApps") { ctx.api.getNotificationApps() }?.let { _apps.value = it } }
    }

    // --- Suggestions --------------------------------------------------------------

    private fun setBusy(id: String, busy: Boolean) {
        _busy.value = if (busy) _busy.value + id else _busy.value - id
    }

    fun acceptSuggestion(s: CallSuggestion, overrides: JSONObject = JSONObject()) {
        if (s.id in _busy.value) return
        setBusy(s.id, true)
        ctx.scope.launch {
            try {
                val result = ctx.api.acceptSuggestion(s.id, overrides)
                _suggestions.value = _suggestions.value.filterNot { it.id == s.id }
                dropEmbedded(setOf(s.id))
                reconcileSuggestionNotifications()
                ctx.messages.post(if (result.optString("kind") == "event") "Added to your calendar" else "Reminder created")
                onSuggestionsChanged()
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                ctx.messages.post(describeError(e))
            } finally {
                setBusy(s.id, false)
            }
        }
    }

    fun dismissSuggestion(s: CallSuggestion) = dismissSuggestions(listOf(s))

    /** Bulk dismiss (optimistic, restored on failure). */
    fun dismissSuggestions(list: List<CallSuggestion>) {
        if (list.isEmpty()) return
        val ids = list.map { it.id }.toSet()
        val before = _suggestions.value
        _suggestions.value = before.filterNot { it.id in ids }
        dropEmbedded(ids)
        reconcileSuggestionNotifications()
        ctx.scope.launch {
            try {
                ctx.api.dismissSuggestions(ids.toList())
                onSuggestionsChanged()
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                _suggestions.value = before
                ctx.messages.post("Couldn't dismiss: ${describeError(e)}")
            }
        }
    }

    private fun reconcileSuggestionNotifications() =
        SuggestionsReceiver.reconcile(appContext, _suggestions.value.map { it.id }.toSet())

    private fun dropEmbedded(ids: Set<String>) {
        _items.value = _items.value.map { n ->
            if (n.suggestions.any { it.id in ids }) n.copy(suggestions = n.suggestions.filterNot { it.id in ids }) else n
        }
    }
}
