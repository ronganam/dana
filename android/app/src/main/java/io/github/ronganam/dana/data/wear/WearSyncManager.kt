package io.github.ronganam.dana.data.wear

import io.github.ronganam.dana.protocol.WearConfig
import io.github.ronganam.dana.protocol.WearPaths
import io.github.ronganam.dana.protocol.WearVersion
import android.content.Context
import android.util.Log
import com.google.android.gms.wearable.DataItem
import com.google.android.gms.wearable.PutDataMapRequest
import com.google.android.gms.wearable.Wearable
import kotlin.coroutines.resume

object WearSyncManager {
    private const val TAG = "WearSyncManager"

    fun syncConfigToWear(
        context: Context,
        serverUrl: String,
        apiToken: String,
        sessionId: String,
        calendarEventsJson: String = "[]",
        dueTasksJson: String = "[]"
    ) {
        try {
            val phoneVersionCode = try {
                androidx.core.content.pm.PackageInfoCompat.getLongVersionCode(context.packageManager.getPackageInfo(context.packageName, 0)).toInt()
            } catch (e: Exception) { 0 }

            val putDataMapReq = PutDataMapRequest.create(WearPaths.CONFIG).apply {
                WearConfig(serverUrl, apiToken, sessionId, calendarEventsJson, dueTasksJson, phoneVersionCode)
                    .writeTo(dataMap, System.currentTimeMillis())
            }
            val request = putDataMapReq.asPutDataRequest().setUrgent()
            Wearable.getDataClient(context).putDataItem(request)
                .addOnSuccessListener {
                    Log.d(TAG, "Successfully synced config to Wear OS: sessionId=$sessionId")
                }
                .addOnFailureListener { e ->
                    Log.w(TAG, "Failed to sync config to Wear OS: ${e.message}")
                }
        } catch (e: Exception) {
            Log.e(TAG, "Exception during Wear OS sync", e)
        }
    }

    /**
     * Read the watch's own version from its [WearPaths.VERSION] data item
     * (published by the wear app at startup). Returns null when the watch is
     * unreachable or has not reported a version yet.
     */
    suspend fun getWearVersion(context: Context): WearVersion? {
        return try {
            val buffer = Wearable.getDataClient(context).dataItems.await()
            try {
                var result: WearVersion? = null
                for (item: DataItem in buffer) {
                    if (item.uri.path != WearPaths.VERSION) continue
                    result = WearVersion.from(com.google.android.gms.wearable.DataMapItem.fromDataItem(item).dataMap)
                    break
                }
                result
            } finally {
                buffer.release()
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to read wear version: ${e.message}")
            null
        }
    }

    /** Minimal await for Play Services Tasks (avoids an extra dependency). */
    private suspend fun <T> com.google.android.gms.tasks.Task<T>.await(): T =
        kotlin.coroutines.suspendCoroutine { cont ->
            addOnSuccessListener { cont.resume(it) }
            addOnFailureListener { cont.resumeWith(Result.failure(it)) }
        }
}
