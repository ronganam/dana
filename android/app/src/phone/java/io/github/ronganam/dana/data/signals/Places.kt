package io.github.ronganam.dana.data.signals

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log
import com.google.android.gms.location.Geofence
import com.google.android.gms.location.GeofenceStatusCodes
import com.google.android.gms.location.GeofencingEvent
import com.google.android.gms.location.GeofencingRequest
import com.google.android.gms.location.LocationServices
import com.google.android.gms.tasks.Task
import com.google.android.gms.tasks.Tasks
import io.github.ronganam.dana.data.network.ServerConfig
import kotlinx.coroutines.CancellationException
import java.util.concurrent.TimeUnit

/**
 * Arriving at and leaving the user's zones (`GET /api/zones`) through Google
 * Play services geofences: Play services watches the circles with the
 * phone's own location batching, wakes [PlacesReceiver] on a crossing, and the
 * app sends only "zone arrive / leave" with the zone's id. No location is read
 * or sent by the app itself. Needs location "all the time" (the background
 * permission on Android 10+).
 */
object Places {
    private const val TAG = "Places"
    private const val DAY_MS = 24L * 3600 * 1000
    /** Play services may hold a crossing this long to batch it with others (saves battery). */
    private const val RESPONSIVENESS_MS = 2 * 60 * 1000

    private fun pendingIntent(context: Context): PendingIntent {
        // Play services fills in the event, so the intent must stay mutable (Android 12+).
        val mutable = if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0
        return PendingIntent.getBroadcast(
            context, 0, Intent(context, PlacesReceiver::class.java), PendingIntent.FLAG_UPDATE_CURRENT or mutable
        )
    }

    /**
     * Bring the registered geofences in line with the zones. [full] (after a
     * reboot, turning places on, once a day) registers every one afresh;
     * otherwise only the zones added, moved or removed since the last time.
     * Blocks on Play services: call it on [kotlinx.coroutines.Dispatchers.IO].
     */
    suspend fun refresh(context: Context, full: Boolean, now: Long = System.currentTimeMillis()) {
        if (!SignalSettings.places(context)) return
        if (PhoneSignals.locationAccess(context) != LocationAccess.ALWAYS) {
            Log.i(TAG, "No location all the time; geofences not registered")
            return
        }
        val zones = try {
            ServerConfig.load(context).api().tasks.zones()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't fetch the zones: ${e.message}")
            return
        }
        val wanted = GeofenceDiff.fences(zones)
        val fresh = full || now - SignalSettings.fencesFullAt(context) > DAY_MS
        val client = LocationServices.getGeofencingClient(context)
        try {
            if (fresh) {
                await(client.removeGeofences(pendingIntent(context)))
                SignalSettings.setFences(context, emptyList())
            }
            val changes = GeofenceDiff.diff(if (fresh) emptyList() else SignalSettings.fences(context), wanted)
            if (changes.remove.isNotEmpty()) await(client.removeGeofences(changes.remove))
            if (changes.add.isNotEmpty()) {
                val request = GeofencingRequest.Builder()
                    // No events for where the phone already is: only crossings count.
                    .setInitialTrigger(0)
                    .addGeofences(changes.add.map { it.toGeofence() })
                    .build()
                await(client.addGeofences(request, pendingIntent(context)))
            }
            SignalSettings.setFences(context, wanted)
            if (fresh) SignalSettings.setFencesFullAt(context, now)
            Log.i(TAG, "Geofences: ${wanted.size} (+${changes.add.size} −${changes.remove.size})")
        } catch (e: SecurityException) {
            Log.w(TAG, "Location permission gone", e)
        } catch (e: Exception) {
            // GEOFENCE_NOT_AVAILABLE: location is off. The next run tries again.
            Log.w(TAG, "Couldn't register the geofences: ${e.message}")
            SignalSettings.setFences(context, emptyList())
        }
    }

    /** Places turned off: remove every geofence. */
    fun removeAll(context: Context) {
        try {
            await(LocationServices.getGeofencingClient(context).removeGeofences(pendingIntent(context)))
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't remove the geofences: ${e.message}")
        }
        SignalSettings.setFences(context, emptyList())
        SignalSettings.setFencesFullAt(context, 0)
    }

    private fun Fence.toGeofence(): Geofence = Geofence.Builder()
        .setRequestId(id)
        .setCircularRegion(latitude, longitude, radiusM)
        .setExpirationDuration(Geofence.NEVER_EXPIRE)
        .setTransitionTypes(Geofence.GEOFENCE_TRANSITION_ENTER or Geofence.GEOFENCE_TRANSITION_EXIT)
        .setNotificationResponsiveness(RESPONSIVENESS_MS)
        .build()

    private fun <T> await(task: Task<T>): T = Tasks.await(task, 30, TimeUnit.SECONDS)

    /** A crossing Play services reported: queue it, or forget the fences it says are gone. */
    internal fun onEvent(context: Context, intent: Intent) {
        val event = GeofencingEvent.fromIntent(intent) ?: return
        if (event.hasError()) {
            Log.w(TAG, "Geofence error ${GeofenceStatusCodes.getStatusCodeString(event.errorCode)}")
            // Location was turned off: Play services dropped every geofence.
            if (event.errorCode == GeofenceStatusCodes.GEOFENCE_NOT_AVAILABLE) SignalSettings.setFences(context, emptyList())
            return
        }
        if (!SignalSettings.places(context)) return
        val arrive = when (event.geofenceTransition) {
            Geofence.GEOFENCE_TRANSITION_ENTER -> true
            Geofence.GEOFENCE_TRANSITION_EXIT -> false
            else -> return
        }
        val at = event.triggeringLocation?.time?.takeIf { it > 0 } ?: System.currentTimeMillis()
        val events = event.triggeringGeofences.orEmpty().map { SignalEvent.Zone(arrive, it.requestId, at) }
        Log.i(TAG, "${if (arrive) "Arrived at" else "Left"} ${events.size} zone(s)")
        SignalSync.add(context, events, now = true)
    }
}

/** Geofence crossings from Google Play services (explicit PendingIntent, not exported). */
class PlacesReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) = Places.onEvent(context.applicationContext, intent)
}
