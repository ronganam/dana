package io.github.ronganam.dana.ui.viewmodels

import android.content.Context
import io.github.ronganam.dana.data.models.ActResult
import io.github.ronganam.dana.data.models.NotificationsOverview
import io.github.ronganam.dana.data.models.OverviewApp
import io.github.ronganam.dana.data.models.PhoneNotification
import io.github.ronganam.dana.data.models.RuleSuggestion
import io.github.ronganam.dana.data.network.UndoOutcome
import io.github.ronganam.dana.data.network.ApiService
import io.github.ronganam.dana.data.notifications.DanaNotificationListener
import io.github.ronganam.dana.data.notifications.NotificationSettings
import io.github.ronganam.dana.data.notifications.NotificationSyncWorker
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.time.LocalTime
import java.time.format.DateTimeFormatter

/** The three ways an app can behave, in the screen's words (server `rule`). */
enum class AppRule(val word: String, val label: String, val serverAction: String, val sentence: String) {
    ALERT("alert", "Interrupt me", "alert", "Every notification from this app opens the full-screen alert."),
    NORMAL("normal", "Let Dana decide", "none", "Dana works out which ones need you and which are noise."),
    QUIET("quiet", "Keep quiet", "mute", "Never shown as needing you and never interrupts. Still searchable.");

    companion object {
        fun of(word: String?): AppRule = values().firstOrNull { it.word == word } ?: NORMAL
    }
}

/**
 * The Notifications screen (`GET /api/notifications/overview` + the app's
 * notifications from `GET /api/notifications?package=`): apps with a summary,
 * the noise, "Dana noticed" rule suggestions and per-app rules.
 *
 * Every change is optimistic and acts at once; clearing returns shade keys
 * (cancelled here, as the old Inbox does) and an `undo_id` for the snackbar.
 * Snoozing reuses [NotificationsController.snooze] (server, then Android's own
 * snooze, return reminders). Undo restores in Dana only, as on the server.
 */
class NotificationOverviewController(
    private val ctx: ControllerContext,
    private val appContext: Context,
    private val inbox: NotificationsController,
    /** Notifications changed (Now's counts, the old Inbox). */
    private val onChanged: () -> Unit
) {
    private val _overview = MutableStateFlow<NotificationsOverview?>(null)
    val overview: StateFlow<NotificationsOverview?> = _overview.asStateFlow()

    private val _loading = MutableStateFlow(false)
    val loading: StateFlow<Boolean> = _loading.asStateFlow()

    private val _failed = MutableStateFlow(false)
    val failed: StateFlow<Boolean> = _failed.asStateFlow()

    private val _appItems = MutableStateFlow<Map<String, List<PhoneNotification>>>(emptyMap())
    /** Waiting notifications per package (loaded when an app is opened). */
    val appItems: StateFlow<Map<String, List<PhoneNotification>>> = _appItems.asStateFlow()

    private val _accessGranted = MutableStateFlow(NotificationSettings.accessGranted(appContext))
    /** Notification access on this phone (without it nothing new arrives). */
    val accessGranted: StateFlow<Boolean> = _accessGranted.asStateFlow()

    private var pollJob: Job? = null
    private var recheck: Job? = null

    /** A one-line message from the screen (an app that wouldn't open, a reply that failed). */
    fun notice(text: String) = ctx.messages.post(text)

    fun refreshAccess() {
        _accessGranted.value = NotificationSettings.accessGranted(appContext)
    }

    fun fetch() {
        ctx.scope.launch {
            _loading.value = true
            try {
                val o = ctx.load("NotificationsOverview") { ctx.api.notifications.overview() }
                if (o != null) {
                    _overview.value = o
                    _failed.value = false
                    if (o.summariesRefreshing) recheckSummaries()
                } else _failed.value = true
            } finally {
                _loading.value = false
            }
        }
    }

    /** Push what the phone has (sync), then fetch. */
    fun syncAndFetch() {
        refreshAccess()
        if (_accessGranted.value) NotificationSyncWorker.syncNow(appContext)
        fetch()
    }

    /** The server is rewriting summaries: look again a few seconds later, not at the next poll. */
    private fun recheckSummaries() {
        if (recheck?.isActive == true) return
        recheck = ctx.scope.launch {
            repeat(3) {
                delay(4_000)
                val o = ctx.load("NotificationsOverview") { ctx.api.notifications.overview() } ?: return@launch
                _overview.value = o
                if (!o.summariesRefreshing) return@launch
            }
        }
    }

    fun fetchApp(packageName: String) {
        ctx.scope.launch {
            ctx.load("AppNotifications") { ctx.api.notifications.forApp(packageName) }?.let {
                _appItems.value = _appItems.value + (packageName to it)
            }
        }
    }

    fun startPolling() {
        if (pollJob?.isActive == true) return
        pollJob = ctx.scope.launch {
            while (true) {
                syncAndFetch()
                delay(20_000)
            }
        }
    }

    fun stopPolling() {
        pollJob?.cancel()
        pollJob = null
    }

    /** The app as the screen shows it (apps first, then quiet apps). */
    fun app(packageName: String?): OverviewApp? = _overview.value?.let { o ->
        (o.apps + o.quietApps).firstOrNull { it.packageName == packageName }
    }

    // --- Clearing -------------------------------------------------------------------------

    /** "Clear the noise": the dismiss bucket + everything from quiet apps. */
    fun clearNoise() {
        val o = _overview.value
        if (o == null) {
            // Not loaded yet (Now's "Clear noise" right after start): let the server count.
            clear("Couldn't clear the noise", { "${it.message} · on the phone too" }) { ctx.api.notifications.clear(bucket = "dismiss") }
            return
        }
        val n = o.counts.noise
        if (n == 0) {
            ctx.messages.post("No noise to clear")
            return
        }
        val quietPkgs = o.quietApps.map { it.packageName }.toSet()
        _overview.value = OverviewMath.withoutNoise(o)
        _appItems.value = _appItems.value.mapValues { (pkg, list) -> if (pkg in quietPkgs) emptyList() else list.filterNot { it.bucket == "dismiss" } }
        clear("Couldn't clear the noise", { "Cleared $n · on the phone too" }) { ctx.api.notifications.clear(bucket = "dismiss") }
    }

    /** "Clear" on an app: everything waiting from it. */
    fun clearApp(app: OverviewApp) {
        val o = _overview.value ?: return
        if (app.count == 0) return
        _overview.value = OverviewMath.withoutApp(o, app.packageName)
        _appItems.value = _appItems.value + (app.packageName to emptyList())
        clear("Couldn't clear ${app.app}", { "Cleared ${app.count} from ${app.app}" }) { ctx.api.notifications.clear(packageName = app.packageName) }
    }

    /** "Clear" on one notification. */
    fun clearOne(n: PhoneNotification) {
        dropLocally(n)
        clear("Couldn't clear it", { "Cleared" }) { ctx.api.notifications.clear(ids = listOf(n.id)) }
        cancelAlert(n.id)
    }

    /** "In 1 h" on one notification (the old Inbox's snooze: server, Android's own snooze, return reminder). */
    fun snoozeOne(n: PhoneNotification, minutes: Long = 60) {
        dropLocally(n)
        val until = System.currentTimeMillis() + minutes * 60_000
        val undo = inbox.snooze(listOf(n), until)
        val at = LocalTime.now().plusMinutes(minutes).format(DateTimeFormatter.ofPattern("HH:mm"))
        ctx.messages.postUndoable("Snoozed · back at $at", onUndo = {
            undo()
            refreshSoon()
        })
        refreshSoon()
    }

    /** A reply went out through the shade: it's handled, so clear it (no Undo — the message is sent). */
    fun replied(n: PhoneNotification) {
        dropLocally(n)
        ctx.scope.launch {
            try {
                DanaNotificationListener.cancel(ctx.api.notifications.clear(ids = listOf(n.id)).cancelKeys)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // Sent anyway; the next refresh shows what's left.
            }
            ctx.messages.post("Sent")
            onChanged()
            fetch()
        }
    }

    private fun dropLocally(n: PhoneNotification) {
        _overview.value = _overview.value?.let { OverviewMath.withoutOne(it, n) }
        _appItems.value = _appItems.value.mapValues { (_, list) -> list.filterNot { it.id == n.id } }
    }

    private fun clear(failure: String, message: (ActResult) -> String, call: suspend () -> ActResult) {
        ctx.scope.launch {
            try {
                val r = call()
                DanaNotificationListener.cancel(r.cancelKeys)
                val undoId = r.undoId
                if (undoId != null) ctx.messages.postUndoable(message(r), onUndo = { undo(undoId) })
                else ctx.messages.post(message(r))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                ctx.messages.post("$failure: ${describeError(e)}")
            }
            onChanged()
            fetch()
            refetchOpenApps()
        }
    }

    private fun undo(undoId: String) {
        ctx.scope.launch {
            try {
                if (ctx.api.now.undo(undoId) == UndoOutcome.TOO_LATE) ctx.messages.post("Too late to undo that")
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                ctx.messages.post("Couldn't undo: ${e.message ?: describeError(e)}")
            }
            onChanged()
            fetch()
            refetchOpenApps()
        }
    }

    private fun refreshSoon() {
        ctx.scope.launch {
            delay(800)
            onChanged()
            fetch()
            refetchOpenApps()
        }
    }

    private fun refetchOpenApps() {
        _appItems.value.keys.forEach { fetchApp(it) }
    }

    private fun cancelAlert(notificationId: String) {
        (appContext.getSystemService(Context.NOTIFICATION_SERVICE) as android.app.NotificationManager)
            .cancel("alert:$notificationId".hashCode())
    }

    // --- Rules -----------------------------------------------------------------------------

    /** Interrupt me / Let Dana decide / Keep quiet, at once, with Undo (the server's undo id: rule and buckets back). */
    fun setRule(app: OverviewApp, rule: AppRule) {
        if (AppRule.of(app.rule) == rule) return
        val before = _overview.value ?: return
        _overview.value = OverviewMath.withRule(before, app.packageName, rule)
        ctx.scope.launch {
            try {
                val text = when (rule) {
                    AppRule.ALERT -> "${app.app} will interrupt you"
                    AppRule.NORMAL -> "Dana decides for ${app.app}"
                    AppRule.QUIET -> "${app.app} will stay quiet"
                }
                val r = ctx.api.notifications.changeRule(app.packageName, rule.serverAction)
                val message = r.message.takeIf { it.isNotBlank() && it != "Done" } ?: text
                val undoId = r.undoId
                if (undoId != null) ctx.messages.postUndoable(message, onUndo = { undo(undoId) }) else ctx.messages.post(message)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _overview.value = before
                ctx.messages.post("Couldn't save the rule: ${e.message ?: describeError(e)}")
            }
            onChanged()
            fetch()
        }
    }

    /** "Dana noticed": Keep it quiet (accept) or No (decline, not offered again for 60 days). */
    fun answer(s: RuleSuggestion, accept: Boolean) {
        val before = _overview.value ?: return
        _overview.value = before.copy(ruleSuggestions = before.ruleSuggestions.filterNot { it.packageName == s.packageName }).let {
            if (accept) OverviewMath.withRule(it, s.packageName, AppRule.QUIET) else it
        }
        ctx.scope.launch {
            try {
                val r = ctx.api.notifications.answerRuleSuggestion(s.packageName, accept)
                val undoId = r.undoId
                if (undoId != null) ctx.messages.postUndoable(r.message, onUndo = { undo(undoId) }) else ctx.messages.post(r.message)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _overview.value = before
                ctx.messages.post("Couldn't save that: ${e.message ?: describeError(e)}")
            }
            onChanged()
            fetch()
        }
    }
}

/** Optimistic arithmetic on an overview (pure, tested). */
object OverviewMath {

    private fun recount(o: NotificationsOverview, apps: List<OverviewApp>, quiet: List<OverviewApp>) = o.copy(
        apps = apps,
        quietApps = quiet,
        counts = o.counts.copy(
            total = (apps + quiet).sumOf { it.count },
            noise = (apps + quiet).sumOf { it.noise },
            needsYou = (apps + quiet).sumOf { it.needsYou }
        )
    )

    fun withoutNoise(o: NotificationsOverview): NotificationsOverview = recount(
        o,
        o.apps.map { it.copy(count = (it.count - it.noise).coerceAtLeast(0), noise = 0) }.filter { it.count > 0 },
        o.quietApps.map { it.copy(count = 0, noise = 0, needsYou = 0) }
    )

    fun withoutApp(o: NotificationsOverview, pkg: String): NotificationsOverview = recount(
        o,
        o.apps.filterNot { it.packageName == pkg },
        o.quietApps.map { if (it.packageName == pkg) it.copy(count = 0, noise = 0, needsYou = 0) else it }
    )

    fun withoutOne(o: NotificationsOverview, n: PhoneNotification): NotificationsOverview {
        fun shrink(a: OverviewApp): OverviewApp {
            if (a.packageName != n.packageName) return a
            val quiet = a.rule == "quiet"
            val noisy = quiet || n.bucket == "dismiss"
            return a.copy(
                count = (a.count - 1).coerceAtLeast(0),
                noise = if (noisy) (a.noise - 1).coerceAtLeast(0) else a.noise,
                needsYou = if (!noisy && n.bucket == "needs_you") (a.needsYou - 1).coerceAtLeast(0) else a.needsYou
            )
        }
        return recount(o, o.apps.map(::shrink).filter { it.count > 0 }, o.quietApps.map(::shrink))
    }

    /** One app with a new rule (quiet makes everything of it noise). */
    fun ruled(a: OverviewApp, rule: AppRule): OverviewApp = when (rule) {
        AppRule.QUIET -> a.copy(rule = "quiet", ruleAction = "mute", noise = a.count, needsYou = 0)
        AppRule.ALERT -> a.copy(rule = "alert", ruleAction = "alert")
        AppRule.NORMAL -> a.copy(rule = "normal", ruleAction = null)
    }

    /** The app moves between "apps" and "quiet apps" with its new rule; quiet makes everything of it noise. */
    fun withRule(o: NotificationsOverview, pkg: String, rule: AppRule): NotificationsOverview {
        val all = o.apps + o.quietApps
        val a = all.firstOrNull { it.packageName == pkg } ?: return o
        val updated = ruled(a, rule)
        val apps = o.apps.filterNot { it.packageName == pkg }.toMutableList()
        val quiet = o.quietApps.filterNot { it.packageName == pkg }.toMutableList()
        if (rule == AppRule.QUIET) quiet.add(0, updated)
        else if (updated.count > 0 || a.rule != "quiet") apps.add(o.apps.indexOfFirst { it.packageName == pkg }.takeIf { it >= 0 } ?: apps.size, updated)
        return recount(o, apps, quiet)
    }
}
