package io.github.ronganam.dana.wear.data

import android.content.Context
import android.util.Log
import com.google.android.gms.wearable.PutDataMapRequest
import com.google.android.gms.wearable.Wearable

/**
 * Publishes the watch app's own version to the phone over the Data Layer so
 * the phone's Settings tab can tell whether the watch app is outdated.
 * The phone reads this at app/.../data/wear/WearSyncManager.getWearVersion().
 */
object WearVersionPublisher {
    private const val TAG = "WearVersionPublisher"
    private const val VERSION_PATH = "/dana/version"

    fun publish(context: Context) {
        try {
            val info = context.packageManager.getPackageInfo(context.packageName, 0)
            val versionCode = if (android.os.Build.VERSION.SDK_INT >= 28) {
                info.longVersionCode.toInt()
            } else {
                @Suppress("DEPRECATION")
                info.versionCode
            }
            val request = PutDataMapRequest.create(VERSION_PATH).apply {
                dataMap.putInt("version_code", versionCode)
                dataMap.putString("version_name", info.versionName ?: "")
                // Data items need a changing value to be re-delivered; include a
                // coarse timestamp (day granularity is enough for a version ping).
                dataMap.putLong("day", System.currentTimeMillis() / (24L * 60 * 60 * 1000))
            }.asPutDataRequest().setUrgent()
            Wearable.getDataClient(context).putDataItem(request)
                .addOnSuccessListener {
                    Log.d(TAG, "Published watch version $versionCode to phone")
                }
                .addOnFailureListener { e ->
                    Log.w(TAG, "Failed to publish watch version: ${e.message}")
                }
        } catch (e: Exception) {
            Log.e(TAG, "Error publishing watch version", e)
        }
    }
}
