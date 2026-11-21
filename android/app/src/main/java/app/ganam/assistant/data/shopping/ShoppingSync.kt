package app.ganam.assistant.data.shopping

import android.content.Context
import app.ganam.assistant.DanaWidgets
import app.ganam.assistant.data.network.ApiService
import app.ganam.assistant.data.network.Me
import app.ganam.assistant.data.sync.PendingSyncWorker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * The one [ShoppingStore] of this process (files under `filesDir`), shared by
 * the Shopping screen, the home-screen widget, pushes and the watch bridge.
 * Requests go through `ApiService.fromPrefs`, so a changed server or token
 * applies to the next request.
 */
object ShoppingSync {
    @Volatile private var store: ShoppingStore? = null
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    fun get(context: Context): ShoppingStore {
        store?.let { return it }
        synchronized(this) {
            store?.let { return it }
            val app = context.applicationContext
            return ShoppingStore(
                dir = app.filesDir,
                transport = { method, path, body -> ApiService.fromPrefs(app).jsonCall(method, path, body) },
                me = { Me.cached(app)?.let { Who(it.id, it.name) } },
                scheduleRetry = { PendingSyncWorker.schedule(app) },
                onChanged = { DanaWidgets.shoppingChanged(app) }
            ).also {
                store = it
                scope.launch { it.load() }
            }
        }
    }

    /** Make a change; when it had to wait, try the queue again in the background. */
    suspend fun submit(context: Context, op: ShoppingOp): ShoppingStore.Outcome {
        val outcome = get(context).submit(op)
        if (outcome is ShoppingStore.Outcome.Queued) flushAsync(context)
        return outcome
    }

    /** Fire-and-forget [ShoppingStore.flush] (the server came back, the app came to the front). */
    fun flushAsync(context: Context) {
        val s = get(context)
        scope.launch {
            s.load()
            if (s.state.value.queued.isNotEmpty()) s.flush()
        }
    }

    /**
     * A `shopping` push (`{list_id, version}`): the list changed somewhere.
     * Read it again when what is held is older (or another list), which
     * redraws the widget and updates the watch; nothing on screen wakes up.
     */
    fun onPush(context: Context, listId: String, version: Long) {
        val s = get(context)
        scope.launch {
            s.load()
            val held = s.state.value.server
            if (held == null || held.list.id != listId || held.list.version < version) s.refresh()
        }
    }
}
