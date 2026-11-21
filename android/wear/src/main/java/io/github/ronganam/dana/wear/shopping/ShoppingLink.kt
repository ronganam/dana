package io.github.ronganam.dana.wear.shopping

import io.github.ronganam.dana.protocol.ShopRequest
import io.github.ronganam.dana.protocol.WearPaths
import android.content.Context
import android.util.Log
import io.github.ronganam.dana.wear.data.PhoneNode
import com.google.android.gms.wearable.Wearable
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

/** How a shopping request reaches the phone. */
fun interface ShoppingLink {
    /** Hands [payload] to the phone; false when no phone could be reached. */
    suspend fun send(context: Context, payload: ShopRequest): Boolean

    companion object {
        val PATH_ANSWER = WearPaths.response(WearPaths.SHOPPING_ACTION)

        /** The phone over the Wear data layer (a debug build may stand in a fake phone: [DebugPhone]). */
        fun forProcess(context: Context): ShoppingLink = DebugPhone.link(context) ?: Phone
    }

    /** [WearPaths.SHOPPING_ACTION] to the paired phone; its answer comes back on [PATH_ANSWER]. */
    object Phone : ShoppingLink {
        override suspend fun send(context: Context, payload: ShopRequest): Boolean {
            val node = PhoneNode.await(context) ?: return false
            val bytes = payload.encode().toByteArray()
            return suspendCancellableCoroutine { cont ->
                Wearable.getMessageClient(context.applicationContext).sendMessage(node, WearPaths.SHOPPING_ACTION, bytes)
                    .addOnCompleteListener { task ->
                        if (!task.isSuccessful) {
                            Log.w("WatchShopping", "Couldn't send ${WearPaths.SHOPPING_ACTION}", task.exception)
                            PhoneNode.invalidate()
                        }
                        if (cont.isActive) cont.resume(task.isSuccessful)
                    }
            }
        }
    }
}
