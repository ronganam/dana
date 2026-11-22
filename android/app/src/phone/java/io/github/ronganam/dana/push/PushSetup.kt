package io.github.ronganam.dana.push

import android.content.Context
import android.content.pm.PackageManager
import android.util.Log
import io.github.ronganam.dana.BuildConfig
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions
import com.google.firebase.messaging.FirebaseMessaging
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import org.unifiedpush.android.connector.UnifiedPush
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.coroutines.resume

/**
 * Phone: wake-ups for [PhoneEvents]. A build with your own Firebase project
 * (`BuildConfig.FIREBASE_*` from google-services.json) uses FCM; every other
 * build, the released APKs among them, uses UnifiedPush through a distributor
 * app on the phone (ntfy, picked by [Distributors.pick]): the distributor
 * hands over an endpoint (`DanaUnifiedPushService.onNewEndpoint`), which goes
 * to the server like an FCM token.
 */
object PushSetup {
    private const val TAG = "PushSetup"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val trying = AtomicBoolean(false)
    /** Getting a token can fail for a while (Play Services busy right after an install). */
    private val retryDelaysMs = listOf(15_000L, 60_000L, 5 * 60_000L, 15 * 60_000L)

    val transport: PushTransport
        get() = if (BuildConfig.FIREBASE_PROJECT_ID.isEmpty()) PushTransport.UNIFIEDPUSH else PushTransport.FCM

    /** Main process, app start: set up the transport, (re)register, catch up. */
    fun init(context: Context) {
        val app = context.applicationContext
        if (transport == PushTransport.FCM) {
            try {
                if (FirebaseApp.getApps(app).isEmpty()) {
                    FirebaseApp.initializeApp(app, FirebaseOptions.Builder()
                        .setProjectId(BuildConfig.FIREBASE_PROJECT_ID)
                        .setGcmSenderId(BuildConfig.FIREBASE_SENDER_ID)
                        .setApplicationId(BuildConfig.FIREBASE_APP_ID)
                        .setApiKey(BuildConfig.FIREBASE_API_KEY)
                        .build())
                }
            } catch (e: Exception) {
                Log.e(TAG, "Firebase init failed", e)
                return
            }
        }
        ensureRegistered(app, force = true)
    }

    /** Register unless already done (also called from the notification sync and Settings → Push). */
    fun ensureRegistered(context: Context, force: Boolean = false) {
        val app = context.applicationContext
        if (!force && PhoneEvents.isRegistered(app, transport)) return
        when (transport) {
            PushTransport.FCM -> registerFcm(app)
            PushTransport.UNIFIEDPUSH -> registerUnifiedPush(app)
        }
    }

    private fun registerFcm(app: Context) {
        if (FirebaseApp.getApps(app).isEmpty() || !trying.compareAndSet(false, true)) return
        scope.launch {
            try {
                for (wait in listOf(0L) + retryDelaysMs) {
                    delay(wait)
                    val token = token()
                    if (token != null) {
                        PhoneEvents.register(app, PushTransport.FCM, token)
                        return@launch
                    }
                }
                PhoneEvents.catchUp(app)
            } finally {
                trying.set(false)
            }
        }
    }

    /** Ask the distributor for an endpoint; it answers in `DanaUnifiedPushService.onNewEndpoint`. */
    private fun registerUnifiedPush(app: Context) {
        val saved = UnifiedPush.getSavedDistributor(app)
        val pick = Distributors.pick(UnifiedPush.getDistributors(app), saved, app.packageName)
        if (pick == null) {
            Log.i(TAG, "No UnifiedPush distributor installed: events wait for the next check")
            PhoneEvents.setProblem(app, NO_DISTRIBUTOR)
            scope.launch { PhoneEvents.catchUp(app) }
            return
        }
        if (pick != saved) UnifiedPush.saveDistributor(app, pick)
        Log.i(TAG, "Registering with UnifiedPush through $pick")
        UnifiedPush.register(app, messageForDistributor = "Dana")
    }

    /** The distributor's endpoint (new or the same again): send it to the server. */
    internal fun onEndpoint(context: Context, endpoint: String) {
        val app = context.applicationContext
        scope.launch { PhoneEvents.register(app, PushTransport.UNIFIEDPUSH, endpoint) }
    }

    internal fun onRegistrationFailed(context: Context, reason: String) {
        PhoneEvents.setProblem(context.applicationContext, "The push app couldn't register Dana ($reason). Open it to check.")
    }

    internal fun onUnregistered(context: Context) {
        val app = context.applicationContext
        PhoneEvents.forget(app)
        PhoneEvents.setProblem(app, "The push app dropped Dana's registration.")
    }

    /** What Settings → Push shows (never null on the phone; the kiosk / TV build has none). */
    fun status(context: Context): PushStatus? {
        val app = context.applicationContext
        val distributor = if (transport == PushTransport.UNIFIEDPUSH) {
            Distributors.pick(UnifiedPush.getDistributors(app), UnifiedPush.getSavedDistributor(app), app.packageName)
                ?.let { appName(app, it) }
        } else null
        val problem = PhoneEvents.problem(app)?.takeUnless { it == NO_DISTRIBUTOR && distributor != null }
        return PushStatus(transport, distributor, PhoneEvents.isRegistered(app, transport), PhoneEvents.lastPingAt(app), problem)
    }

    private fun appName(app: Context, pkg: String): String = try {
        app.packageManager.getApplicationLabel(app.packageManager.getApplicationInfo(pkg, 0)).toString()
    } catch (e: PackageManager.NameNotFoundException) {
        pkg
    }

    private const val NO_DISTRIBUTOR = "No push app (UnifiedPush distributor) is installed."

    @Suppress("DEPRECATION") // getToken(): see DanaMessagingService.onNewToken
    private suspend fun token(): String? = suspendCancellableCoroutine { cont ->
        FirebaseMessaging.getInstance().token
            .addOnSuccessListener { cont.resume(it) }
            .addOnFailureListener { e ->
                Log.w(TAG, "No FCM token yet: ${e.message}")
                cont.resume(null)
            }
    }
}
