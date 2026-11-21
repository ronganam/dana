package io.github.ronganam.dana.ui.viewmodels

import android.content.Context
import android.util.Log
import androidx.annotation.PluralsRes
import androidx.annotation.StringRes
import io.github.ronganam.dana.R
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
 * User-visible words from the app's string resources, so code that is not a
 * composable can be driven off-device (JVM tests read `values/strings.xml`).
 */
interface Texts {
    fun get(@StringRes id: Int, vararg args: Any): String
    fun plural(@PluralsRes id: Int, count: Int, vararg args: Any): String

    /** Short, user-facing reason for a failed request. */
    fun reason(e: Throwable): String = when (e) {
        is ApiException -> if (e.code == 401 || e.code == 403) get(R.string.error_not_authorized) else get(R.string.error_http, e.code)
        is IOException -> get(R.string.error_unreachable)
        else -> e.message ?: e.javaClass.simpleName
    }

    companion object {
        fun of(context: Context): Texts {
            val app = context.applicationContext ?: context
            return object : Texts {
                override fun get(id: Int, vararg args: Any): String = app.getString(id, *args)
                override fun plural(id: Int, count: Int, vararg args: Any): String = app.resources.getQuantityString(id, count, *args)
            }
        }
    }
}

/**
 * What a controller needs from its host. [api] is read on every call because
 * the instance is replaced when the server config changes.
 */
class ControllerContext(
    val scope: CoroutineScope,
    val messages: UserMessages,
    val texts: Texts,
    private val apiProvider: () -> ApiService
) {
    val api: ApiService get() = apiProvider()

    /** Show the string [id] (formatted with [args]) as a Snackbar. */
    fun say(@StringRes id: Int, vararg args: Any) = messages.post(texts.get(id, *args))

    /** [action] with its failure line from the string [failure]. */
    fun action(@StringRes failure: Int, block: suspend () -> Boolean): Job = action(texts.get(failure), block)

    /**
     * Run a user-initiated mutation. [block] returns false when the server
     * rejected the request. Both rejection and failure are shown to the user
     * as "<failure>: <reason>" instead of being swallowed.
     */
    fun action(failure: String, block: suspend () -> Boolean): Job = scope.launch {
        try {
            if (!block()) messages.post(texts.get(R.string.error_rejected, failure))
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Log.w("ControllerAction", failure, e)
            messages.post(texts.get(R.string.error_with_reason, failure, texts.reason(e)))
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
        Log.w(tag, "Load failed: $e", e)
        null
    }
}
