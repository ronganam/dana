package io.github.ronganam.dana.ui.viewmodels

import android.content.Context
import android.util.Log
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import io.github.ronganam.dana.data.network.ApiException
import io.github.ronganam.dana.data.network.Me
import io.github.ronganam.dana.data.shopping.ShoppingItem
import io.github.ronganam.dana.data.shopping.ShoppingAnswer
import io.github.ronganam.dana.data.shopping.ShoppingEvent
import io.github.ronganam.dana.data.shopping.WriteAnswer
import io.github.ronganam.dana.data.shopping.ShoppingOp
import io.github.ronganam.dana.data.shopping.ShoppingOps
import io.github.ronganam.dana.data.shopping.ShoppingScreen
import io.github.ronganam.dana.data.shopping.ShoppingShortcut
import io.github.ronganam.dana.data.shopping.ShoppingStore
import io.github.ronganam.dana.data.shopping.ShoppingSync
import io.github.ronganam.dana.data.shopping.ShoppingText
import io.github.ronganam.dana.data.shopping.ShoppingView
import io.github.ronganam.dana.data.shopping.Usual
import io.github.ronganam.dana.data.shopping.Who
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.io.IOException

/**
 * The Shopping screen (server repo `docs/shopping.md`; phone side
 * `docs/shopping.md`). The list itself lives in [ShoppingSync] (the disk
 * cache, changes on their way, the offline queue), shared with the widget
 * and the watch; this controller holds what only the screen needs: the list
 * looked at read-only, ticks lingering in place, the editor, the add field,
 * suggestions, invitations, the Share sheet and the live stream.
 *
 * Every change shows at once and offers Undo: the server's undo when it
 * answered, else (it waits to sync) taking it out of the queue.
 */
class ShoppingController(private val ctx: ControllerContext, private val appContext: Context) {
    private val store: ShoppingStore = ShoppingSync.get(appContext)

    private val _enabled = MutableStateFlow(Me.cached(appContext)?.has(ADDON) == true)
    /** The user has the `shopping` integration (`/api/me`): the place, its count and the shortcut. */
    val enabled: StateFlow<Boolean> = _enabled.asStateFlow()

    private var me: Who? = Me.cached(appContext)?.let { Who(it.id, it.name) }
    fun me(): Who? = me

    private val _viewing = MutableStateFlow<ShoppingScreen?>(null)
    /** A list looked at with `?list=` (read-only; it never becomes the active list). Null = the active list. */
    val viewing: StateFlow<ShoppingScreen?> = _viewing.asStateFlow()

    /** What the screen shows: the list looked at, else the active list with every pending change on top. */
    val screen: StateFlow<ShoppingScreen?> = combine(store.state, _viewing) { _, v -> v ?: store.shown() }
        .stateIn(ctx.scope, SharingStarted.Eagerly, store.shown())

    /** Changes made offline that wait to sync. */
    val waiting: StateFlow<Int> = store.state.map { it.queued.size }.stateIn(ctx.scope, SharingStarted.Eagerly, 0)

    private val _failed = MutableStateFlow(false)
    /** The last read didn't reach the server (the screen shows the kept list). */
    val failed: StateFlow<Boolean> = _failed.asStateFlow()

    private val _linger = MutableStateFlow<Map<String, String?>>(emptyMap())
    /** Ticked rows staying in their aisle for a moment ([LINGER_MS]), with the row they stay before. */
    val linger: StateFlow<Map<String, String?>> = _linger.asStateFlow()

    private val _boughtOpen = MutableStateFlow(false)
    val boughtOpen: StateFlow<Boolean> = _boughtOpen.asStateFlow()

    /** The editor's draft (and a refusal said inside it). Kept here so a fold keeps it. */
    data class Editor(val draft: ShoppingView.Draft, val error: String? = null)
    private val _editor = MutableStateFlow<Editor?>(null)
    val editor: StateFlow<Editor?> = _editor.asStateFlow()

    /** The add field (survives fold / unfold). */
    var addText by mutableStateOf(TextFieldValue(""))
        private set
    private val _addFocused = MutableStateFlow(false)
    val addFocused: StateFlow<Boolean> = _addFocused.asStateFlow()
    private val _suggestions = MutableStateFlow<List<Usual>>(emptyList())
    /** Names from the history matching what is typed (most bought first). */
    val suggestions: StateFlow<List<Usual>> = _suggestions.asStateFlow()

    /** Per invitation: "ask" (move your items?) or "busy". */
    private val _inviteUi = MutableStateFlow<Map<String, String>>(emptyMap())
    val inviteUi: StateFlow<Map<String, String>> = _inviteUi.asStateFlow()

    /** The Share sheet: open with its model (null while loading), an error, busy. */
    data class Share(val model: ShoppingView.ShareModel?, val error: String? = null, val busy: Boolean = false)
    private val _share = MutableStateFlow<Share?>(null)
    val share: StateFlow<Share?> = _share.asStateFlow()
    private val _shareable = MutableStateFlow<Pair<String, Boolean>?>(null)
    /** Whether the owner has anyone to share the list with (Share shows only then), per list id. */
    val shareable: StateFlow<Pair<String, Boolean>?> = _shareable.asStateFlow()

    private var liveJob: Job? = null
    private var suggestJob: Job? = null

    init {
        ctx.scope.launch { store.notices.collect { ctx.messages.post(it) } }
    }

    private val listId: String get() = screen.value?.list?.id.orEmpty()

    // ---------- loading ----------

    /** `/api/me`: whether the place exists, and who "you" are on shared lists. */
    fun fetchMe() {
        ctx.scope.launch {
            val before = Me.cached(appContext)?.id
            val info = Me.fetch(appContext, ctx.api) ?: return@launch
            // Someone else now (another token or server): the kept list and its queue were theirs.
            if (before != null && before != info.id) forgetAccount()
            me = Who(info.id, info.name)
            setEnabled(info.has(ADDON))
            if (info.has(ADDON)) refresh()
        }
    }

    /** The account changed: forget the previous user's list, queue and screen state. */
    suspend fun forgetAccount() {
        store.forget()
        _viewing.value = null
        _editor.value = null
        _share.value = null
        _shareable.value = null
        _inviteUi.value = emptyMap()
        _linger.value = emptyMap()
    }

    /** Settings changed the server or the token: start over for whoever that is. */
    fun accountChanged() {
        ctx.scope.launch {
            forgetAccount()
            fetchMe()
        }
    }

    private fun setEnabled(on: Boolean) {
        _enabled.value = on
        ShoppingShortcut.update(appContext, on)
    }

    /** Read the screen again (the list looked at, else the active list). */
    fun refresh() {
        ctx.scope.launch {
            val looking = _viewing.value
            if (looking != null) {
                val answer = ctx.api.shopping.screen(looking.list.id)
                when {
                    answer.ok -> answer.value?.let { _viewing.value = it }
                    answer.code == 404 -> backToActive()
                }
                return@launch
            }
            val (code, body) = store.refresh()
            _failed.value = code == -1
            if (code == 403 && body?.error == "not_connected") {
                Me.drop(appContext, ADDON)
                setEnabled(false)
            }
            if (code in 200..299) checkShareable()
        }
    }

    /** Waiting changes go out as soon as the server answers again. */
    fun flush() = ShoppingSync.flushAsync(appContext)

    // ---------- the live stream ----------

    /** While the place is visible: follow `/api/shopping/events` and read the list again when it changes. */
    fun startLive() {
        if (liveJob?.isActive == true) return
        refresh()
        liveJob = ctx.scope.launch {
            var backoff = 1_000L
            while (isActive) {
                val following = _viewing.value?.list?.id
                try {
                    ctx.api.shopping.events(following, ::onEvent)
                    backoff = 1_000L
                } catch (e: CancellationException) {
                    throw e
                } catch (e: ApiException) {
                    if (e.code == 404 && following != null) backToActive()
                    if (e.code == 403) { refresh(); return@launch }
                } catch (e: Exception) {
                    Log.d(TAG, "Shopping stream dropped: ${e.message}")
                }
                delay(backoff)
                // Short: the stream is only open while the list shows.
                backoff = (backoff * 2).coerceAtMost(10_000L)
            }
        }
    }

    fun stopLive() {
        liveJob?.cancel()
        liveJob = null
    }

    /** Reconnect at once (the server answers again): a fresh stream, a fresh read, no backoff left over. */
    fun restartLive() {
        if (liveJob?.isActive == true) { stopLive(); startLive() }
    }

    private fun onEvent(event: ShoppingEvent) {
        when (event.type) {
            "changed" -> {
                val held = _viewing.value ?: store.state.value.server
                val version = event.version
                when (ShoppingOps.onChanged(event.listId, version, held, if (store.busy) 1 else 0)) {
                    ShoppingOps.Reload.NOW -> refresh()
                    ShoppingOps.Reload.LATER -> ctx.scope.launch {
                        // After the own writes land: their answers may already carry this version.
                        while (store.busy) delay(150)
                        val now = _viewing.value ?: store.state.value.server
                        if (now == null || now.list.version < version) refresh()
                    }
                    ShoppingOps.Reload.NO -> Unit
                }
            }
            "lists" -> refresh()
            "end" -> ctx.scope.launch { backToActive() }
        }
    }

    // ---------- adding ----------

    fun onAddText(value: TextFieldValue) {
        val text = ShoppingText.pasteAsLine(value.text)
        addText = if (text == value.text) value else TextFieldValue(text, TextRange(text.length))
        suggest(text)
    }

    fun setAddFocused(focused: Boolean) { _addFocused.value = focused }

    private fun suggest(text: String) {
        suggestJob?.cancel()
        val q = text.substringAfterLast(',').trim()
        if (q.isEmpty() || screen.value?.list?.canEdit != true) { _suggestions.value = emptyList(); return }
        suggestJob = ctx.scope.launch {
            delay(180)
            val answer = ctx.api.shopping.suggestions(q, listParam())
            _suggestions.value = if (answer.ok && answer.value != null) answer.value
            else {
                // Offline: the usuals the kept list knows.
                val key = ShoppingText.nameKey(q)
                screen.value?.usuals.orEmpty().filter { ShoppingText.nameKey(it.name).contains(key) }
            }
        }
    }

    /** Enter / the keyboard's action key: add what was typed (split and parsed like the server does). */
    fun addTyped() {
        val text = addText.text
        val parts = ShoppingText.parse(text)
        if (parts.isEmpty()) return
        if (parts.size > ShoppingText.MAX_ITEMS) {
            ctx.messages.post("That's ${parts.size} items — at most ${ShoppingText.MAX_ITEMS} at a time.")
            return
        }
        val op = ShoppingOps.addTyped(listId, text) ?: return
        addText = TextFieldValue("")
        _suggestions.value = emptyList()
        submitAdd(op, restore = text)
    }

    /** A usual or a suggestion: added as that name, never parsed for a count. */
    fun addName(name: String) {
        if (addText.text.substringAfterLast(',').isNotBlank()) addText = TextFieldValue(addText.text.substringBeforeLast(',', "").let { if (it.isBlank()) "" else "$it, " })
        _suggestions.value = emptyList()
        submitAdd(ShoppingOps.addName(listId, name), restore = null)
    }

    private fun submitAdd(op: ShoppingOp.Add, restore: String?) {
        ctx.scope.launch {
            when (val o = ShoppingSync.submit(appContext, op)) {
                is ShoppingStore.Outcome.Sent -> {
                    val words = ShoppingView.addResultText(o.answer?.results.orEmpty())
                    announce(words.text.ifEmpty { ShoppingView.queuedLabel(op).removeSuffix(" · will sync") }, o.answer?.undo?.id, op)
                }
                ShoppingStore.Outcome.Queued -> announceQueued(op)
                is ShoppingStore.Outcome.Rejected -> {
                    ctx.messages.post(ShoppingView.errorText(o.status, o.body))
                    if (restore != null && addText.text.isEmpty()) addText = TextFieldValue(restore, TextRange(restore.length))
                    afterRefusal(o)
                }
            }
        }
    }

    // ---------- ticking, removing, clearing ----------

    /** A tap on a row: tick it (it stays a moment, then goes to Bought), or put it back. */
    fun tap(row: ShoppingView.Row) {
        val s = screen.value ?: return
        if (!s.list.canEdit) return
        when {
            row.lingering -> {
                _linger.value = _linger.value - row.id
                change(ShoppingOp.Check(ShoppingOp.newId(), s.list.id, row.id, false, row.name))
            }
            !row.checked -> {
                _linger.value = _linger.value + (row.id to ShoppingView.anchorAfter(s, row.id))
                change(ShoppingOp.Check(ShoppingOp.newId(), s.list.id, row.id, true, row.name))
                ctx.scope.launch {
                    delay(LINGER_MS)
                    _linger.value = _linger.value - row.id
                }
            }
            else -> change(ShoppingOp.Check(ShoppingOp.newId(), s.list.id, row.id, false, row.name))
        }
    }

    fun remove(itemId: String) {
        val item = screen.value?.items?.firstOrNull { it.id == itemId } ?: return
        if (_editor.value?.draft?.itemId == itemId) _editor.value = null
        change(ShoppingOp.Remove(ShoppingOp.newId(), listId, itemId, item.name))
    }

    fun clearBought() {
        if (screen.value?.items?.none { it.checked } != false) return
        _boughtOpen.value = false
        change(ShoppingOp.ClearBought(ShoppingOp.newId(), listId))
    }

    /** "Don't suggest again". */
    fun forget(name: String) {
        _suggestions.value = _suggestions.value.filter { it.name != name }
        change(ShoppingOp.Forget(ShoppingOp.newId(), listId, name))
    }

    fun toggleBought() { _boughtOpen.value = !_boughtOpen.value }

    /** Send [op] (shown at once) and say what happened, with Undo. */
    private fun change(op: ShoppingOp, onRefused: (ShoppingStore.Outcome.Rejected) -> Boolean = { false }) {
        ctx.scope.launch {
            when (val o = ShoppingSync.submit(appContext, op)) {
                is ShoppingStore.Outcome.Sent -> {
                    val undo = o.answer?.undo
                    announce(undo?.let { ShoppingView.undoLabel(it.label) } ?: ShoppingView.queuedLabel(op).removeSuffix(" · will sync"), undo?.id, op)
                }
                ShoppingStore.Outcome.Queued -> announceQueued(op)
                is ShoppingStore.Outcome.Rejected -> {
                    if (op is ShoppingOp.Check) _linger.value = _linger.value - op.itemId
                    if (!onRefused(o)) ctx.messages.post(ShoppingView.errorText(o.status, o.body))
                    afterRefusal(o)
                }
            }
        }
    }

    private fun afterRefusal(o: ShoppingStore.Outcome.Rejected) {
        if (o.status == 404 || o.status == 409 || o.status == 403) refresh()
    }

    private fun announce(text: String, undoId: String?, op: ShoppingOp) {
        if (undoId == null) {
            if (op !is ShoppingOp.Check) ctx.messages.post(text)
            return
        }
        ctx.messages.postUndoable(text, onUndo = { undo(undoId) })
    }

    private fun announceQueued(op: ShoppingOp) {
        ctx.messages.postUndoable(ShoppingView.queuedLabel(op), onUndo = {
            ctx.scope.launch {
                when (val c = store.cancel(op.id)) {
                    ShoppingStore.Cancel.Dropped -> Unit
                    is ShoppingStore.Cancel.Sent -> undo(c.undoId)
                    ShoppingStore.Cancel.Gone -> ctx.messages.post("Too late to undo that")
                }
            }
        })
    }

    /** `POST /api/undo/{id}`, then the list is read again. */
    fun undo(undoId: String) {
        ctx.scope.launch {
            val answer = ctx.api.shopping.undo(undoId)
            val (ok, text) = ShoppingView.undoText(answer.code, answer.refusal)
            if (!ok) ctx.messages.post(if (answer.code == -1) "Couldn't undo: the server can't be reached." else text)
            refresh()
        }
    }

    // ---------- the editor ----------

    fun openEditor(itemId: String) {
        val item = screen.value?.items?.firstOrNull { it.id == itemId } ?: return
        if (screen.value?.list?.canEdit != true) return
        _editor.value = Editor(ShoppingView.draft(item))
    }

    fun editDraft(draft: ShoppingView.Draft) {
        _editor.value = _editor.value?.copy(draft = draft, error = null)
    }

    fun stepQuantity(delta: Int) {
        val e = _editor.value ?: return
        editDraft(e.draft.copy(quantity = ShoppingView.stepQuantity(e.draft.quantity, delta)))
    }

    /**
     * Done / a tap outside / Back: save what changed. A refusal reopens the
     * editor and says why; closing it ([closing]) while that refusal still
     * stands (nothing edited since) drops the change instead of sending it again.
     */
    fun saveEditor(closing: Boolean = false) {
        val e = _editor.value ?: return
        if (closing && e.error != null) { _editor.value = null; return }
        val item: ShoppingItem = screen.value?.items?.firstOrNull { it.id == e.draft.itemId } ?: run { _editor.value = null; return }
        val changes = ShoppingView.editChanges(item, e.draft).getOrElse {
            _editor.value = e.copy(error = it.message)
            return
        }
        _editor.value = null
        if (changes.isEmpty()) return
        change(ShoppingOp.Edit(ShoppingOp.newId(), listId, item.id, changes, changes["name"] ?: item.name)) { refused ->
            if (refused.status == 409 || refused.status == 400) {
                _editor.value = Editor(e.draft, ShoppingView.errorText(refused.status, refused.body))
                true
            } else false
        }
    }

    fun closeEditor() { _editor.value = null }

    // ---------- invitations and lists ----------

    fun useInvite(inviteListId: String) {
        val s = screen.value ?: return
        val invite = s.invites.firstOrNull { it.listId == inviteListId } ?: return
        if (ShoppingView.invite(invite, s).askMove) _inviteUi.value = _inviteUi.value + (inviteListId to "ask")
        else answerInvite(inviteListId, "use", false)
    }

    fun answerInvite(inviteListId: String, action: String, moveItems: Boolean) {
        _inviteUi.value = _inviteUi.value + (inviteListId to "busy")
        ctx.scope.launch {
            screenWrite { ctx.api.shopping.answerInvite(inviteListId, action, moveItems) }
            _inviteUi.value = _inviteUi.value - inviteListId
        }
    }

    /** "Look at it" (a read-only invitation, a view-only list in the menu): shown with `?list=`. */
    fun lookAt(id: String) {
        ctx.scope.launch {
            val answer = ctx.api.shopping.screen(id)
            val s = answer.value
            if (s == null) { ctx.messages.post(ShoppingView.errorText(answer.code, answer.refusal)); return@launch }
            _viewing.value = s
            _editor.value = null
            restartLive()
        }
    }

    fun backToActive() {
        if (_viewing.value == null) return
        _viewing.value = null
        restartLive()
        refresh()
    }

    /** The menu's lists: your own or an `edit` list becomes the active one; a `read` list is looked at. */
    fun switchTo(id: String, access: String) {
        if (access == "read") { lookAt(id); return }
        if (_viewing.value == null && id == screen.value?.list?.id) return
        ctx.scope.launch {
            _viewing.value = null
            screenWrite { ctx.api.shopping.setActive(id) }
            restartLive()
        }
    }

    /** A write answered with a screen (invitations, switching lists): keep it and offer its Undo. */
    private suspend fun screenWrite(write: suspend () -> ShoppingAnswer<WriteAnswer>) {
        val result = try {
            write()
        } catch (e: CancellationException) {
            throw e
        } catch (e: IOException) {
            ctx.messages.post("You're offline — nothing was changed.")
            return
        }
        if (!result.ok) {
            ctx.messages.post(ShoppingView.errorText(result.code, result.refusal))
            refresh()
            return
        }
        val answer = result.value
        if (answer != null) {
            store.accept(answer.screen, result.raw)
            answer.undo?.let { u -> ctx.messages.postUndoable(ShoppingView.undoLabel(u.label), onUndo = { undo(u.id) }) }
        }
        refresh()
    }

    // ---------- sharing ----------

    private fun checkShareable() {
        val s = store.state.value.server ?: return
        if (!s.list.isOwner || _shareable.value?.first == s.list.id) return
        ctx.scope.launch {
            val answer = ctx.api.shopping.share(s.list.id)
            if (answer.ok && answer.value != null) {
                val model = ShoppingView.share(answer.value)
                _shareable.value = s.list.id to (model.people.isNotEmpty() || model.others.isNotEmpty())
            }
        }
    }

    fun openShare() {
        val id = screen.value?.list?.takeIf { it.isOwner }?.id ?: return
        _share.value = Share(null)
        ctx.scope.launch {
            val answer = ctx.api.shopping.share(id)
            _share.value = if (answer.ok && answer.value != null) Share(ShoppingView.share(answer.value))
            else Share(null, ShoppingView.errorText(answer.code, answer.refusal))
        }
    }

    /** Can edit / View only / Remove (`none`) / Add (`edit`). */
    fun setShare(userId: String, level: String) {
        val id = screen.value?.list?.id ?: return
        val current = _share.value ?: return
        _share.value = current.copy(busy = true, error = null)
        ctx.scope.launch {
            val answer = ctx.api.shopping.setShare(id, userId, level)
            _share.value = if (answer.ok && answer.value != null) Share(ShoppingView.share(answer.value))
            else current.copy(busy = false, error = ShoppingView.errorText(answer.code, answer.refusal))
            refresh()
        }
    }

    fun closeShare() { _share.value = null }

    // ---------- helpers ----------

    /** The list a suggestion is for: the one looked at, else the active one ([listId]); null = the server's active list. */
    private fun listParam(): String? = _viewing.value?.list?.id ?: listId.takeIf { it.isNotEmpty() }

    companion object {
        private const val TAG = "Shopping"
        const val ADDON = "shopping"
        /** A ticked row stays in its aisle this long (a second tap puts it back), then moves to Bought. */
        const val LINGER_MS = 900L
    }
}
