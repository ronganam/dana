package io.github.ronganam.dana.wear.data

import android.content.Context
import android.util.Log
import com.google.android.gms.wearable.Wearable
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

/**
 * The paired phone's Data Layer node id, cached for the process.
 *
 * Looking it up (`NodeClient.connectedNodes`) is a round trip to Play
 * Services that used to sit in front of every voice turn, before the mic
 * even opened. It is now prefetched when the assistant service / activity
 * comes up and reused; a failed send calls [invalidate].
 */
object PhoneNode {
    private const val TAG = "PhoneNode"

    @Volatile private var cached: String? = null
    private val waiters = mutableListOf<(String?) -> Unit>()
    private var lookingUp = false

    val cachedId: String? get() = cached

    /** Refreshes the cached id in the background (keeps the old one meanwhile). */
    fun prefetch(context: Context) = lookup(context.applicationContext, null)

    /** Calls [action] with the phone's node id, or null when no phone is connected. */
    fun get(context: Context, action: (String?) -> Unit) {
        val id = cached
        if (id != null) action(id) else lookup(context.applicationContext, action)
    }

    suspend fun await(context: Context): String? {
        cached?.let { return it }
        return suspendCancellableCoroutine { cont -> get(context) { if (cont.isActive) cont.resume(it) } }
    }

    fun invalidate() {
        cached = null
    }

    private fun lookup(context: Context, action: ((String?) -> Unit)?) {
        synchronized(waiters) {
            if (action != null) waiters += action
            if (lookingUp) return
            lookingUp = true
        }
        Wearable.getNodeClient(context).connectedNodes
            .addOnCompleteListener { task ->
                val nodes = if (task.isSuccessful) task.result.orEmpty() else emptyList()
                if (!task.isSuccessful) Log.w(TAG, "connectedNodes failed", task.exception)
                val id = (nodes.firstOrNull { it.isNearby } ?: nodes.firstOrNull())?.id
                cached = id
                val pending = synchronized(waiters) {
                    lookingUp = false
                    waiters.toList().also { waiters.clear() }
                }
                pending.forEach { it(id) }
            }
    }
}
