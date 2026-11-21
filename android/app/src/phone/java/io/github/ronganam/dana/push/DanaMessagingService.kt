package io.github.ronganam.dana.push

import android.util.Log
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeoutOrNull

/**
 * FCM wake-up ping from the server (data only: `seq`, `type`). The real event
 * is fetched from the server by [PhoneEvents.catchUp]; high-priority messages
 * give the app ~10 s, even in Doze.
 */
class DanaMessagingService : FirebaseMessagingService() {

    override fun onMessageReceived(message: RemoteMessage) {
        Log.i(TAG, "Ping ${message.data["type"]} #${message.data["seq"]} (priority ${message.priority})")
        // Runs on Firebase's worker thread; blocking it keeps the process alive.
        runBlocking { withTimeoutOrNull(9_000) { PhoneEvents.catchUp(applicationContext) } }
            ?: Log.w(TAG, "Catch-up timed out")
    }

    override fun onNewToken(token: String) {
        runBlocking { withTimeoutOrNull(9_000) { PhoneEvents.register(applicationContext, token) } }
    }

    companion object {
        private const val TAG = "DanaPush"
    }
}
