package io.github.ronganam.dana.wear.shopping

import android.content.Context
import android.util.Log
import io.github.ronganam.dana.wear.data.PhoneNode
import com.google.android.gms.wearable.Wearable
import kotlinx.coroutines.suspendCancellableCoroutine
import org.json.JSONObject
import kotlin.coroutines.resume

/** How a shopping request reaches the phone. */
fun interface ShoppingLink {
    /** Hands [payload] to the phone; false when no phone could be reached. */
    suspend fun send(context: Context, payload: JSONObject): Boolean

    companion object {
        const val PATH = "/shopping_action"
        const val PATH_ANSWER = "/response/shopping_action"

        /** The phone over the Wear data layer (a debug build may stand in a fake phone: [DebugPhone]). */
        fun forProcess(context: Context): ShoppingLink = DebugPhone.link(context) ?: Phone
    }

    /** `/shopping_action` to the paired phone; its answer comes back on [PATH_ANSWER]. */
    object Phone : ShoppingLink {
        override suspend fun send(context: Context, payload: JSONObject): Boolean {
            val node = PhoneNode.await(context) ?: return false
            val bytes = payload.toString().toByteArray()
            return suspendCancellableCoroutine { cont ->
                Wearable.getMessageClient(context.applicationContext).sendMessage(node, PATH, bytes)
                    .addOnCompleteListener { task ->
                        if (!task.isSuccessful) {
                            Log.w("WatchShopping", "Couldn't send $PATH", task.exception)
                            PhoneNode.invalidate()
                        }
                        if (cont.isActive) cont.resume(task.isSuccessful)
                    }
            }
        }
    }
}
