package io.github.ronganam.dana.data.shopping

import io.github.ronganam.dana.data.network.ServerConfig
import android.content.Context
import io.github.ronganam.dana.DanaWidgets
import io.github.ronganam.dana.data.network.Me
import io.github.ronganam.dana.data.sync.PendingSyncWorker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * The one [ShoppingStore] of this process (files under `filesDir`), shared by
 * the Shopping screen, the home-screen widget, pushes and the watch bridge.
 * Requests go through `ServerConfig.load(…).api()`, so a changed server or token
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
                transport = { method, path, body -> ServerConfig.load(app).api().jsonCall(method, path, body) },
                me = { Me.cached(app)?.let { Who(it.id, it.name) } },
                scheduleRetry = { PendingSyncWorker.schedule(app) },
                onChanged = { DanaWidgets.shoppingChanged(app) }
            ).also {
                store = it
                scope.launch { it.load() }
            }
        }
    }

    /** The server's usual items matching [query] on [listId], or null when it can't answer. */
    suspend fun suggestions(context: Context, query: String, listId: String): List<Usual>? {
        fun enc(v: String) = java.net.URLEncoder.encode(v, "UTF-8").replace("+", "%20")
        val path = "/api/shopping/suggestions?q=${enc(query)}&list=${enc(listId)}"
        return runCatching {
            val (code, body) = ServerConfig.load(context).api().jsonCall("GET", path, null)
            if (code in 200..299) ShoppingJson.usuals(org.json.JSONObject(body)) else null
        }.getOrNull()
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
