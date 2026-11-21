package app.ganam.assistant.push

import android.content.Context
import android.util.Log
import app.ganam.assistant.BuildConfig
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions
import com.google.firebase.messaging.FirebaseMessaging
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.coroutines.resume

/** Phone: Firebase Cloud Messaging wake-ups for [PhoneEvents]. */
object PushSetup {
    private const val TAG = "PushSetup"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val trying = AtomicBoolean(false)
    /** Getting a token can fail for a while (Play Services busy right after an install). */
    private val retryDelaysMs = listOf(15_000L, 60_000L, 5 * 60_000L, 15 * 60_000L)

    /** Main process, app start: init Firebase, (re)register the token, catch up. */
    fun init(context: Context) {
        val app = context.applicationContext
        if (BuildConfig.FIREBASE_PROJECT_ID.isEmpty()) {
            Log.i(TAG, "Built without google-services.json: no direct push")
            return
        }
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
        ensureRegistered(app, force = true)
    }

    /** Register unless already done (also called from the notification sync). */
    fun ensureRegistered(context: Context, force: Boolean = false) {
        val app = context.applicationContext
        if (!force && PhoneEvents.isRegistered(app)) return
        if (FirebaseApp.getApps(app).isEmpty() || !trying.compareAndSet(false, true)) return
        scope.launch {
            try {
                for (wait in listOf(0L) + retryDelaysMs) {
                    delay(wait)
                    val token = token()
                    if (token != null) {
                        PhoneEvents.register(app, token)
                        return@launch
                    }
                }
                PhoneEvents.catchUp(app)
            } finally {
                trying.set(false)
            }
        }
    }

    private suspend fun token(): String? = suspendCancellableCoroutine { cont ->
        FirebaseMessaging.getInstance().token
            .addOnSuccessListener { cont.resume(it) }
            .addOnFailureListener { e ->
                Log.w(TAG, "No FCM token yet: ${e.message}")
                cont.resume(null)
            }
    }
}
