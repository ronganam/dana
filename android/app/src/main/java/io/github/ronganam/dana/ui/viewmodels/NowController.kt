package io.github.ronganam.dana.ui.viewmodels

import android.content.Context
import android.util.Log
import io.github.ronganam.dana.ReminderReceiver
import io.github.ronganam.dana.data.models.ActResult
import io.github.ronganam.dana.data.models.NowAction
import io.github.ronganam.dana.data.models.NowItem
import io.github.ronganam.dana.data.models.NowPayload
import io.github.ronganam.dana.data.models.QuietEntry
import io.github.ronganam.dana.data.network.UndoOutcome
import io.github.ronganam.dana.data.network.ApiService
import io.github.ronganam.dana.data.network.NowApi
import io.github.ronganam.dana.data.notifications.DanaNotificationListener
import io.github.ronganam.dana.data.notifications.Snoozer
import io.github.ronganam.dana.ui.screens.now.ItemDraft
import io.github.ronganam.dana.ui.screens.now.NowModel
import io.github.ronganam.dana.ui.screens.now.PendingRemovals
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.io.File

/**
 * The Now queue (`GET /api/now`): one short list of things that need a
 * decision, quiet suggestions, the notification counts and the lede.
 *
 * - The last answer is kept in memory and on disk (`filesDir/now_cache.json`),
 *   so the screen is never blank, also when the server is away.
 * - Acting is optimistic: the row disappears at once ([PendingRemovals]), the
 *   action goes to `POST /api/now/act`, the reply's `message` shows with Undo
 *   when there is an `undo_id`, and the queue is fetched again. A failure
 *   brings the row back and says why.
 * - Notification side effects happen here too: dismissed keys are cancelled in
 *   the shade, snoozed ones snoozed there (and back on Undo).
 */
class NowController(
    private val ctx: ControllerContext,
    private val appContext: Context,
    /** Something changed elsewhere because of an action (reminders, suggestions, notifications). */
    private val onChanged: (kind: String) -> Unit
) {
    private val _payload = MutableStateFlow<NowPayload?>(null)
    /** The last answer from the server (or the disk cache until the first one). */
    val payload: StateFlow<NowPayload?> = _payload.asStateFlow()

    private val _fresh = MutableStateFlow(false)
    /** True once an answer came from the server in this process (false = showing the cache). */
    val fresh: StateFlow<Boolean> = _fresh.asStateFlow()

    private val _loading = MutableStateFlow(false)
    val loading: StateFlow<Boolean> = _loading.asStateFlow()

    private val _failed = MutableStateFlow(false)
    /** The last refresh failed (server unreachable or erroring). */
    val failed: StateFlow<Boolean> = _failed.asStateFlow()

    private val pending = PendingRemovals()
    private val _hidden = MutableStateFlow<Set<String>>(emptySet())
    /** Rows (and quiet entries) acted on and hidden until the server agrees. */
    val hidden: StateFlow<Set<String>> = _hidden.asStateFlow()

    private val _drafts = MutableStateFlow<Map<String, ItemDraft>>(emptyMap())
    /** The owner's choice / check picks per card (kept across fold/unfold). */
    val drafts: StateFlow<Map<String, ItemDraft>> = _drafts.asStateFlow()

    private val cacheFile = File(appContext.filesDir, "now_cache.json")
    private var refreshJob: Job? = null
    private var pollJob: Job? = null
    private var refreshAgain = false

    init {
        ctx.scope.launch {
            val cached = withContext(Dispatchers.IO) {
                runCatching { if (cacheFile.exists()) NowApi.parse(cacheFile.readText()) else null }.getOrNull()
            }
            if (cached != null && _payload.value == null) _payload.value = cached
        }
    }

    // --- Loading ------------------------------------------------------------------------

    /** Fetch the queue. A refresh asked for while one runs runs again after it. */
    fun refresh() {
        if (refreshJob?.isActive == true) {
            refreshAgain = true
            return
        }
        refreshJob = ctx.scope.launch {
            do {
                refreshAgain = false
                val token = pending.refreshStarted()
                _loading.value = true
                try {
                    val raw = ctx.api.now.nowText()
                    val parsed = NowApi.parse(raw)
                    _payload.value = parsed
                    _fresh.value = true
                    _failed.value = false
                    pending.refreshed(token)
                    publishHidden()
                    withContext(Dispatchers.IO) { runCatching { cacheFile.writeText(raw) } }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    Log.w("Now", "Load failed: ${describeError(e)}")
                    _failed.value = true
                } finally {
                    _loading.value = false
                }
            } while (refreshAgain)
        }
    }

    /** While Now is on screen: refresh every 30 s (the server answers from its DB, no model call). */
    fun startPolling() {
        if (pollJob?.isActive == true) return
        pollJob = ctx.scope.launch {
            while (true) {
                refresh()
                delay(30_000)
            }
        }
    }

    fun stopPolling() {
        pollJob?.cancel()
        pollJob = null
    }

    /** The rows the screen shows right now (the last answer minus the ones acted on). */
    fun visibleItems(): List<NowItem> = pending.visible(_payload.value?.items.orEmpty())

    /** A one-line message from the screen (a reply that couldn't be sent, an app that wouldn't open). */
    fun notice(text: String) = ctx.messages.post(text)

    private fun publishHidden() {
        _hidden.value = pending.ids
    }

    // --- Drafts (choices / checks) ----------------------------------------------------------

    fun pickChoice(itemId: String, key: String, index: Int) {
        val d = _drafts.value[itemId] ?: ItemDraft()
        _drafts.value = _drafts.value + (itemId to d.copy(choices = d.choices + (key to index)))
    }

    fun toggleCheck(item: NowItem, key: String, index: Int) {
        val d = _drafts.value[item.id] ?: ItemDraft()
        val block = NowModel.checkBlocks(item).firstOrNull { it.key == key } ?: return
        val current = NowModel.checkedIndexes(block, d)
        val next = if (index in current) current - index else current + index
        _drafts.value = _drafts.value + (item.id to d.copy(checks = d.checks + (key to next)))
    }

    // --- Acting -------------------------------------------------------------------------------

    /**
     * Do [action] on [item] (a server action; client ones are the screen's job).
     * [isPrimary]: the choices / checks the owner picked go with it.
     * [onFailed]: the row came back (so the screen can re-select it).
     */
    fun act(item: NowItem, action: NowAction, isPrimary: Boolean, onFailed: () -> Unit = {}) {
        val args = NowModel.argsFor(item, action, _drafts.value[item.id], isPrimary)
        send(item.id, action.action, args, item, onFailed)
    }

    /** Dropped on a day-strip slot: `schedule {slot}`. */
    fun schedule(item: NowItem, slotKey: String, onFailed: () -> Unit = {}) {
        send(item.id, "schedule", JsonObject(mapOf("slot" to JsonPrimitive(slotKey))), item, onFailed)
    }

    /** "Add" / "×" on a quiet suggestion. */
    fun actQuiet(entry: QuietEntry, action: NowAction) {
        send(entry.id, action.action, action.args, null) {}
    }

    /** "Dismiss all" quiet suggestions. */
    fun dismissAllQuiet() {
        val ids = _payload.value?.quiet?.map { it.id }.orEmpty()
        ids.forEach { pending.add(it) }
        publishHidden()
        ctx.scope.launch {
            try {
                val r = ctx.api.now.act("quiet:all", "dismiss")
                ids.forEach { pending.finished(it) }
                announce(r) { ids.forEach(pending::restore); publishHidden() }
                onChanged("suggestion")
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                ids.forEach(pending::restore)
                publishHidden()
                ctx.messages.post("Couldn't dismiss them: ${reason(e)}")
            }
            refresh()
        }
    }

    /**
     * A reply was sent from the phone (notification item): take the row out
     * and dismiss it in Dana (and the shade), without an Undo — a sent message
     * can't be unsent.
     */
    fun replied(item: NowItem, who: String?) {
        pending.add(item.id)
        publishHidden()
        ctx.scope.launch {
            try {
                val r = ctx.api.now.act(item.id, "dismiss")
                pending.finished(item.id)
                applyOnPhone(item, "dismiss", r)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                pending.finished(item.id)  // sent anyway; the next refresh decides
            }
            ctx.messages.post(if (who.isNullOrBlank()) "Sent" else "Sent to $who")
            onChanged("notification")
            refresh()
        }
    }

    private fun send(id: String, action: String, args: JsonObject, item: NowItem?, onFailed: () -> Unit) {
        pending.add(id)
        publishHidden()
        ctx.scope.launch {
            try {
                val r = ctx.api.now.act(id, action, args)
                pending.finished(id)
                if (item != null) applyOnPhone(item, action, r)
                announce(r) {
                    pending.restore(id)
                    publishHidden()
                    if (item != null) undoOnPhone(item, action, r)
                }
                onChanged(item?.kind ?: "suggestion")
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                pending.restore(id)
                publishHidden()
                onFailed()
                ctx.messages.post(reason(e))
            }
            refresh()
        }
    }

    /** The confirmation, with Undo when the server can undo it. */
    private fun announce(r: ActResult, beforeRefresh: () -> Unit) {
        val undoId = r.undoId
        if (undoId == null) {
            ctx.messages.post(r.message)
            return
        }
        ctx.messages.postUndoable(r.message, onUndo = {
            ctx.scope.launch {
                try {
                    when (ctx.api.now.undo(undoId)) {
                        UndoOutcome.DONE -> beforeRefresh()
                        UndoOutcome.TOO_LATE -> ctx.messages.post("Too late to undo that")
                    }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    ctx.messages.post(reason(e))
                }
                onChanged("undo")
                refresh()
            }
        })
    }

    /** Shade and alarm side effects of a done action. */
    private fun applyOnPhone(item: NowItem, action: String, r: ActResult) {
        if (r.cancelKeys.isNotEmpty()) DanaNotificationListener.cancel(r.cancelKeys)
        val n = item.notification
        if (n != null && action == "dismiss") cancelAlert(n.id)
        if (n != null && r.snoozeKeys.isNotEmpty()) {
            val until = r.snoozeKeys.values.max()
            Snoozer.applyOnPhone(appContext, listOf(Snoozer.Target(n.id, n.key, n.app ?: "Notification", item.title)), r.snoozeKeys, until)
        }
        // A reminder that was done / moved: its alarm notification goes (the server's push does it too).
        if (item.id.startsWith("task:") && action in setOf("done", "snooze", "schedule")) {
            ReminderReceiver.clear(appContext, item.id.removePrefix("task:"))
        }
    }

    private fun undoOnPhone(item: NowItem, action: String, r: ActResult) {
        val n = item.notification ?: return
        if (r.snoozeKeys.isNotEmpty() || action == "snooze") Snoozer.undoOnPhone(appContext, listOf(n.id), r.snoozeKeys.keys)
    }

    private fun cancelAlert(notificationId: String) {
        (appContext.getSystemService(Context.NOTIFICATION_SERVICE) as android.app.NotificationManager)
            .cancel("alert:$notificationId".hashCode())
    }

    private fun reason(e: Exception): String =
        if (e is io.github.ronganam.dana.data.network.ApiException && e.code != 401 && e.code != 403) e.message ?: describeError(e)
        else describeError(e)
}
