package io.github.ronganam.dana.data.signals

import android.Manifest
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.SystemClock
import android.util.Log
import com.google.android.gms.location.ActivityRecognition
import com.google.android.gms.location.ActivityTransition
import com.google.android.gms.location.ActivityTransitionRequest
import com.google.android.gms.location.ActivityTransitionResult
import com.google.android.gms.location.DetectedActivity

/**
 * Driving detection for a car without Bluetooth: Google Play services'
 * Activity Recognition transitions (in a vehicle: enter / exit) wake
 * [DrivingReceiver] through a PendingIntent, with no foreground service and
 * no location. Each becomes car enter / leave (`via: activity`) when
 * [DrivingRule] lets it (the chosen Bluetooth car wins). Android drops the
 * registration on a reboot or an app update, so it is made again on app start
 * and after boot ([PhoneSignals.init], [PhoneSignals.onBoot]).
 */
object Driving {
    private const val TAG = "Driving"

    /** Physical activity (Android 10+; Play services' own permission before, granted at install). */
    fun allowed(context: Context): Boolean =
        Build.VERSION.SDK_INT < 29 || context.checkSelfPermission(Manifest.permission.ACTIVITY_RECOGNITION) == PackageManager.PERMISSION_GRANTED

    private fun pendingIntent(context: Context): PendingIntent {
        // Play services fills in the result, so the intent must stay mutable (Android 12+).
        val mutable = if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0
        return PendingIntent.getBroadcast(
            context, 0, Intent(context, DrivingReceiver::class.java), PendingIntent.FLAG_UPDATE_CURRENT or mutable
        )
    }

    /** Register the transitions when the car and driving switches are on and allowed, else remove them. */
    fun sync(context: Context) {
        val app = context.applicationContext
        val client = ActivityRecognition.getClient(app)
        try {
            if (SignalSettings.car(app) && SignalSettings.driving(app) && allowed(app)) {
                val transitions = listOf(ActivityTransition.ACTIVITY_TRANSITION_ENTER, ActivityTransition.ACTIVITY_TRANSITION_EXIT).map {
                    ActivityTransition.Builder().setActivityType(DetectedActivity.IN_VEHICLE).setActivityTransition(it).build()
                }
                client.requestActivityTransitionUpdates(ActivityTransitionRequest(transitions), pendingIntent(app))
                    .addOnSuccessListener { Log.i(TAG, "Watching for driving") }
                    .addOnFailureListener { Log.w(TAG, "Couldn't watch for driving: ${it.message}") }
            } else {
                client.removeActivityTransitionUpdates(pendingIntent(app))
                    .addOnFailureListener { Log.d(TAG, "Nothing to remove: ${it.message}") }
            }
        } catch (e: SecurityException) {
            Log.w(TAG, "Physical activity not allowed", e)
        }
    }

    /** One transition: a car event when [DrivingRule] lets it through. */
    fun report(context: Context, enter: Boolean, at: Long) {
        val app = context.applicationContext
        if (!SignalSettings.car(app) || !SignalSettings.driving(app)) return
        val send = DrivingRule.send(
            enter,
            bluetoothCar = SignalSettings.carDevice(app) != null,
            bluetoothState = SignalSettings.carState(app, CarVia.BLUETOOTH),
            lastActivity = SignalSettings.carState(app, CarVia.ACTIVITY)
        )
        if (!send) {
            Log.i(TAG, "In a vehicle ${if (enter) "enter" else "exit"}: not sent")
            return
        }
        SignalSettings.setCarState(app, CarVia.ACTIVITY, enter)
        Log.i(TAG, "Car ${if (enter) "enter" else "leave"} via activity")
        SignalSync.add(app, listOf(SignalEvent.Car(enter, at, CarVia.ACTIVITY)), now = true)
    }

    internal fun onResult(context: Context, intent: Intent) {
        if (!ActivityTransitionResult.hasResult(intent)) return
        val result = ActivityTransitionResult.extractResult(intent) ?: return
        val nowWall = System.currentTimeMillis()
        val nowElapsed = SystemClock.elapsedRealtimeNanos()
        result.transitionEvents
            .filter { it.activityType == DetectedActivity.IN_VEHICLE }
            .sortedBy { it.elapsedRealTimeNanos }
            .forEach { e ->
                val at = nowWall - (nowElapsed - e.elapsedRealTimeNanos) / 1_000_000
                report(context, e.transitionType == ActivityTransition.ACTIVITY_TRANSITION_ENTER, at)
            }
    }
}

/** Activity Recognition transitions from Google Play services (explicit PendingIntent, not exported). */
class DrivingReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) = Driving.onResult(context.applicationContext, intent)
}
