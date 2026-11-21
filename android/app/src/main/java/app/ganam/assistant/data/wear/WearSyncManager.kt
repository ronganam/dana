package app.ganam.assistant.data.wear

import android.content.Context
import android.util.Log
import com.google.android.gms.wearable.DataItem
import com.google.android.gms.wearable.PutDataMapRequest
import com.google.android.gms.wearable.Wearable
import kotlin.coroutines.resume

/** Version of the watch app as reported by the watch over the Data Layer. */
data class WearVersionInfo(val versionCode: Int, val versionName: String)

object WearSyncManager {
    private const val TAG = "WearSyncManager"
    private const val CONFIG_PATH = "/ganam_config"
    private const val VERSION_PATH = "/ganam/version"

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
                context.packageManager.getPackageInfo(context.packageName, 0).longVersionCode.toInt()
            } catch (e: Exception) { 0 }

            val putDataMapReq = PutDataMapRequest.create(CONFIG_PATH).apply {
                dataMap.putString("server_url", serverUrl)
                dataMap.putString("api_token", apiToken)
                dataMap.putString("chat_session_id", sessionId)
                dataMap.putString("calendar_events", calendarEventsJson)
                dataMap.putString("due_tasks", dueTasksJson)
                dataMap.putInt("phone_version_code", phoneVersionCode)
                dataMap.putLong("timestamp", System.currentTimeMillis())
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
     * Read the watch's own version from its /ganam/version data item
     * (published by the wear app at startup). Returns null when the watch is
     * unreachable or has not reported a version yet.
     */
    suspend fun getWearVersion(context: Context): WearVersionInfo? {
        return try {
            val buffer = Wearable.getDataClient(context).dataItems.await()
            try {
                var result: WearVersionInfo? = null
                for (item: DataItem in buffer) {
                    if (item.uri.path != VERSION_PATH) continue
                    val map = com.google.android.gms.wearable.DataMapItem.fromDataItem(item).dataMap
                    result = WearVersionInfo(
                        versionCode = map.getInt("version_code", 0),
                        versionName = map.getString("version_name") ?: ""
                    )
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
