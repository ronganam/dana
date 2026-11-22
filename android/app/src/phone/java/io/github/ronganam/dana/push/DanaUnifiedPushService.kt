package io.github.ronganam.dana.push

import androidx.core.net.toUri
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.unifiedpush.android.connector.FailedReason
import org.unifiedpush.android.connector.PushService
import org.unifiedpush.android.connector.data.PushEndpoint
import org.unifiedpush.android.connector.data.PushMessage

/**
 * UnifiedPush events from the distributor app (ntfy): a new endpoint goes to
 * the server ([PushSetup.onEndpoint]); a message is the server's ping (the
 * same `{"seq", "type"}` as an FCM data message), handled by [PushPing].
 * The connector may call these on the main thread, so nothing here blocks.
 */
class DanaUnifiedPushService : PushService() {

    override fun onNewEndpoint(endpoint: PushEndpoint, instance: String) {
        // Only the host is logged: the endpoint is what anyone could push to.
        Log.i(TAG, "New endpoint on ${endpoint.url.toUri().host}" + if (endpoint.temporary) " (temporary)" else "")
        PushSetup.onEndpoint(applicationContext, endpoint.url)
    }

    override fun onMessage(message: PushMessage, instance: String) {
        Log.i(TAG, "Ping ${message.content.decodeToString().take(80)}")
        val app = applicationContext
        scope.launch { if (!PushPing.of(app).run()) Log.w(TAG, "Catch-up timed out") }
    }

    override fun onRegistrationFailed(reason: FailedReason, instance: String) {
        Log.w(TAG, "Registration failed: $reason")
        PushSetup.onRegistrationFailed(applicationContext, reason.name)
    }

    override fun onUnregistered(instance: String) {
        Log.w(TAG, "Unregistered by the distributor")
        PushSetup.onUnregistered(applicationContext)
    }

    companion object {
        private const val TAG = "DanaUnifiedPush"
        private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    }
}
