package io.github.ronganam.dana.data.network

import android.content.Context
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.serializer

/**
 * Home Assistant zones for place reminders and snoozes (`GET /api/zones`):
 * the last list the server sent, kept in the app's prefs so the alarm screen,
 * the suggestion editor and the watch have places even offline. Before the
 * first fetch only Home Assistant's own "Home".
 */
object Zones {
    private const val TAG = "Zones"
    private const val KEY = "zones_cache"

    fun cached(context: Context): List<String> {
        val raw = ServerConfig.prefs(context).getString(KEY, null)
        return raw?.let { runCatching { ApiJson.decodeFromString(ListSerializer(String.serializer()), it) }.getOrNull() }
            ?.takeIf { it.isNotEmpty() }
            ?: listOf("Home")
    }

    /** The server's zones (cached for next time); the cached ones when it can't be reached. */
    suspend fun fetch(context: Context): List<String> = try {
        withTimeout(8000) { ServerConfig.load(context).api().tasks.zones() }.map { it.name }
            .also { if (it.isNotEmpty()) ServerConfig.prefs(context).edit().putString(KEY, ApiJson.encodeToString(ListSerializer(String.serializer()), it)).apply() }
            .ifEmpty { cached(context) }
    } catch (e: Exception) {
        Log.d(TAG, "Zone fetch failed, using the last known ones: ${e.message}")
        cached(context)
    }

    /** Refresh the cache in the background. */
    fun refresh(context: Context) {
        val app = context.applicationContext
        CoroutineScope(Dispatchers.IO).launch { fetch(app) }
    }
}
