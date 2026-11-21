package io.github.ronganam.dana.ui.viewmodels

import io.github.ronganam.dana.data.chat.ThreadModel
import io.github.ronganam.dana.data.chat.UndoAction
import io.github.ronganam.dana.data.chat.UndoItem
import io.github.ronganam.dana.data.network.ApiException
import io.github.ronganam.dana.data.network.UndoOutcome
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * The Undo row under Dana's latest reply: the undos the run reported, each a
 * button until pressed (`POST /api/undo/{id}`), then what happened —
 * "Undone.", too late (404, after 15 min), or the server's reason.
 */
internal class ChatUndos(private val ctx: ControllerContext, private val onUndone: () -> Unit) {
    private val _items = MutableStateFlow<List<UndoItem>>(emptyList())
    val items: StateFlow<List<UndoItem>> = _items.asStateFlow()

    /** A new turn starts: the previous turn's row (and its outcomes) goes. */
    fun clear() {
        _items.value = emptyList()
    }

    /** The undos a `tool_end` reported while the run streams. */
    fun add(more: List<UndoAction>) {
        if (more.isNotEmpty()) _items.update { ThreadModel.addUndos(it, more) }
    }

    /** The latest run's still-valid undos as the server lists them. */
    fun reconcile(server: List<UndoAction>, lastUserAt: Long?) {
        _items.update { ThreadModel.reconcileUndos(it, server, lastUserAt) }
    }

    fun press(id: String) {
        val item = _items.value.firstOrNull { it.id == id } ?: return
        if (item.status != UndoItem.Status.READY && item.status != UndoItem.Status.FAILED) return
        set(item) { it.copy(status = UndoItem.Status.BUSY, error = null) }
        ctx.scope.launch {
            val at = System.currentTimeMillis()
            try {
                val outcome = ctx.api.now.undo(id)
                set(item) {
                    it.copy(status = if (outcome == UndoOutcome.DONE) UndoItem.Status.DONE else UndoItem.Status.TOO_LATE, at = at)
                }
                if (outcome == UndoOutcome.DONE) onUndone()
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiException) {
                set(item) { it.copy(status = UndoItem.Status.FAILED, error = e.message?.removePrefix("Undo failed: "), at = at) }
            } catch (e: Exception) {
                set(item) { it.copy(status = UndoItem.Status.READY) }
                ctx.messages.post("Couldn't undo: ${describeError(e)}")
            }
        }
    }

    private fun set(item: UndoItem, transform: (UndoItem) -> UndoItem) {
        // Several presses can finish at once: update atomically.
        _items.update { list ->
            if (list.any { it.id == item.id }) list.map { if (it.id == item.id) transform(it) else it } else list + transform(item)
        }
    }
}
