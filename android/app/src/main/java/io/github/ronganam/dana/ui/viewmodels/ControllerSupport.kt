package io.github.ronganam.dana.ui.viewmodels

import android.util.Log
import io.github.ronganam.dana.data.network.ApiException
import io.github.ronganam.dana.data.network.ApiService
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.launch
import java.io.IOException

/** One-shot, user-visible messages (shown as a Snackbar by the host screen). */
class UserMessages {
    private val _messages = MutableSharedFlow<String>(extraBufferCapacity = 16)
    val messages: SharedFlow<String> = _messages.asSharedFlow()

    fun post(message: String) {
        _messages.tryEmit(message)
    }

    private val _undoable = MutableSharedFlow<UndoableMessage>(extraBufferCapacity = 16)
    /** Messages with an Undo action (shown by the new shell's Snackbar). */
    val undoable: SharedFlow<UndoableMessage> = _undoable.asSharedFlow()

    fun postUndoable(message: String, onUndo: () -> Unit, onTimeout: () -> Unit = {}) {
        _undoable.tryEmit(UndoableMessage(message, onUndo, onTimeout))
    }
}

/**
 * "Completed · Undo". [onUndo] runs when Undo is tapped, [onTimeout] when the
 * message goes away without it (used to delay a delete until it can no longer
 * be undone).
 */
class UndoableMessage(val text: String, val onUndo: () -> Unit, val onTimeout: () -> Unit)

/**
 * What a controller needs from its host. [api] is read on every call because
 * the instance is replaced when the server config changes.
 */
class ControllerContext(
    val scope: CoroutineScope,
    val messages: UserMessages,
    private val apiProvider: () -> ApiService
) {
    val api: ApiService get() = apiProvider()

    /**
     * Run a user-initiated mutation. [block] returns false when the server
     * rejected the request. Both rejection and failure are shown to the user
     * as "<failure>: <reason>" instead of being swallowed.
     */
    fun action(failure: String, block: suspend () -> Boolean): Job = scope.launch {
        try {
            if (!block()) messages.post("$failure: the server rejected the request")
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Log.w("ControllerAction", failure, e)
            messages.post("$failure: ${describeError(e)}")
        }
    }

    /**
     * Run a background load. Failures are logged, not shown: the
     * server-status banner already covers "unreachable", and a toast for every
     * refresh would be noise. Returns null on failure.
     */
    suspend fun <T> load(tag: String, block: suspend () -> T): T? = try {
        block()
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        Log.w(tag, "Load failed: ${describeError(e)}", e)
        null
    }
}

/** Short, user-facing reason for a failed request. */
fun describeError(e: Throwable): String = when (e) {
    is ApiException -> if (e.code == 401 || e.code == 403) "not authorized (check the API token)"
        else "server error (HTTP ${e.code})"
    is IOException -> "server unreachable — is the VPN on?"
    else -> e.message ?: e.javaClass.simpleName
}
