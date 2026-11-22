package io.github.ronganam.dana.data.signals

import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.activity.result.contract.ActivityResultContract
import androidx.core.net.toUri
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.HealthConnectFeatures
import androidx.health.connect.client.PermissionController
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import kotlinx.coroutines.CancellationException
import java.time.Instant

/**
 * "Wake from sleep data": the sleep sessions a health app (a watch's or the
 * phone's own) writes to Health Connect, read since the last read
 * ([SleepSessions]) and sent as "sleep" events; the server takes a night
 * ending in the wake window as the wake, at that time. Health Connect is an
 * app on Android 9–13 and part of Android from 14; nothing here runs without
 * it, and every call is guarded so a missing or outdated one never crashes.
 */
object HealthSleep {
    private const val TAG = "HealthSleep"
    private const val PROVIDER = "com.google.android.apps.healthdata"

    val READ_SLEEP: String = HealthPermission.getReadPermission(SleepSessionRecord::class)
    const val READ_IN_BACKGROUND: String = HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND

    fun state(context: Context): HealthConnectState = when (runCatching { HealthConnectClient.getSdkStatus(context, PROVIDER) }.getOrNull()) {
        HealthConnectClient.SDK_AVAILABLE -> HealthConnectState.AVAILABLE
        HealthConnectClient.SDK_UNAVAILABLE_PROVIDER_UPDATE_REQUIRED -> HealthConnectState.NEEDS_UPDATE
        else -> HealthConnectState.UNSUPPORTED
    }

    private fun client(context: Context): HealthConnectClient? =
        if (state(context) != HealthConnectState.AVAILABLE) null
        else runCatching { HealthConnectClient.getOrCreate(context, PROVIDER) }.getOrNull()

    /** What Health Connect lets Dana do: read sleep; background reads supported; allowed. */
    data class Access(val sleep: Boolean, val backgroundSupported: Boolean, val background: Boolean)

    suspend fun access(context: Context): Access {
        val client = client(context) ?: return Access(sleep = false, backgroundSupported = false, background = false)
        return try {
            val granted = client.permissionController.getGrantedPermissions()
            val supported = backgroundSupported(client)
            Access(READ_SLEEP in granted, supported, supported && READ_IN_BACKGROUND in granted)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't read Health Connect's permissions: ${e.message}")
            Access(sleep = false, backgroundSupported = false, background = false)
        }
    }

    private fun backgroundSupported(client: HealthConnectClient): Boolean =
        client.features.getFeatureStatus(HealthConnectFeatures.FEATURE_READ_HEALTH_DATA_IN_BACKGROUND) == HealthConnectFeatures.FEATURE_STATUS_AVAILABLE

    /** The permissions to ask for: sleep, and reading in the background where Health Connect has it. */
    fun permissions(context: Context): Set<String> {
        val client = client(context) ?: return setOf(READ_SLEEP)
        return if (runCatching { backgroundSupported(client) }.getOrDefault(false)) setOf(READ_SLEEP, READ_IN_BACKGROUND) else setOf(READ_SLEEP)
    }

    /** Health Connect's own permission screen (the granted ones come back). */
    fun permissionContract(): ActivityResultContract<Set<String>, Set<String>> =
        PermissionController.createRequestPermissionResultContract(PROVIDER)

    /** Installing or updating Health Connect: its store page, set to open its onboarding. */
    fun installIntent(): Intent =
        Intent(Intent.ACTION_VIEW, "market://details?id=$PROVIDER&url=healthconnect%3A%2F%2Fonboarding".toUri())
            .setPackage("com.android.vending")

    /** The same page on the web, for a phone without the Play Store app. */
    fun installWebIntent(): Intent =
        Intent(Intent.ACTION_VIEW, "https://play.google.com/store/apps/details?id=$PROVIDER".toUri())

    /** Health Connect's settings (its app permissions, which apps write sleep). */
    fun settingsIntent(context: Context): Intent =
        HealthConnectClient.getHealthConnectManageDataIntent(context, PROVIDER)

    /**
     * Read the sessions written since the last read and queue their events.
     * Without the background permission Health Connect only answers while Dana
     * is open; the read then fails quietly and the next one catches up.
     */
    suspend fun read(context: Context, now: Long = System.currentTimeMillis()) {
        if (!SignalSettings.sleep(context)) return
        val client = client(context) ?: return
        val sessions = mutableListOf<SleepSession>()
        try {
            var page: String? = null
            do {
                val response = client.readRecords(
                    ReadRecordsRequest(
                        SleepSessionRecord::class,
                        TimeRangeFilter.after(Instant.ofEpochMilli(now - SignalQueue.MAX_AGE_MS)),
                        pageToken = page
                    )
                )
                response.records.mapTo(sessions) {
                    SleepSession(it.startTime.toEpochMilli(), it.endTime.toEpochMilli(), it.metadata.lastModifiedTime.toEpochMilli())
                }
                page = response.pageToken
            } while (page != null)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            // SecurityException: not allowed (or not in the background); anything else: Health Connect's own trouble.
            Log.w(TAG, "Couldn't read sleep: ${e.message}")
            return
        }
        val read = SleepSessions.read(sessions, SignalSettings.sleepCursor(context), now)
        SignalSync.add(context, read.events, now = false)
        SignalSettings.setSleepCursor(context, read.cursor)
        Log.i(TAG, "${sessions.size} sleep sessions → ${read.events.size} new")
    }
}
