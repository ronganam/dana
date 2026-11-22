package io.github.ronganam.dana.push

import android.util.Log
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeoutOrNull

/**
 * FCM wake-up ping from the server (data only: `seq`, `type`), in a build
 * with your own Firebase project. [PushPing] fetches the real event; a
 * high-priority message gives the app ~10 s, even in Doze.
 */
class DanaMessagingService : FirebaseMessagingService() {

    override fun onMessageReceived(message: RemoteMessage) {
        Log.i(TAG, "Ping ${message.data["type"]} #${message.data["seq"]} (priority ${message.priority})")
        // Runs on Firebase's worker thread; blocking it keeps the process alive.
        if (!runBlocking { PushPing.of(applicationContext).run() }) Log.w(TAG, "Catch-up timed out")
    }

    // firebase-messaging 26 deprecates the token callbacks for register() / onRegistered();
    // moving there changes how the server learns the token, so it waits for a test on a device.
    @Deprecated("Firebase's token API; see the note above")
    override fun onNewToken(token: String) {
        runBlocking { withTimeoutOrNull(PushPing.TIMEOUT_MS) { PhoneEvents.register(applicationContext, PushTransport.FCM, token) } }
    }

    companion object {
        private const val TAG = "DanaPush"
    }
}
