package io.github.ronganam.dana.wear.data

import android.content.Intent
import android.util.Log
import androidx.wear.tiles.TileService
import com.google.android.gms.wearable.DataEvent
import com.google.android.gms.wearable.DataEventBuffer
import com.google.android.gms.wearable.DataMapItem
import com.google.android.gms.wearable.WearableListenerService
import io.github.ronganam.dana.wear.tiles.CalendarTileService
import io.github.ronganam.dana.wear.tiles.TasksTileService

object WearConfigReceiver {
    var onConfigUpdated: (() -> Unit)? = null
}

class WearConfigListenerService : WearableListenerService() {

    override fun onDataChanged(dataEvents: DataEventBuffer) {
        super.onDataChanged(dataEvents)
        for (event in dataEvents) {
            if (event.type == DataEvent.TYPE_CHANGED && event.dataItem.uri.path == "/dana_config") {
                try {
                    val dataMap = DataMapItem.fromDataItem(event.dataItem).dataMap
                    val serverUrl = dataMap.getString("server_url") ?: ""
                    val apiToken = dataMap.getString("api_token") ?: ""
                    val sessionId = dataMap.getString("chat_session_id") ?: ""

                    val wearPrefs = WearPrefs(applicationContext)
                    if (serverUrl.isNotEmpty()) wearPrefs.serverUrl = serverUrl
                    if (apiToken.isNotEmpty()) wearPrefs.apiToken = apiToken
                    if (sessionId.isNotEmpty()) wearPrefs.sessionId = sessionId

                    val phoneVersionCode = dataMap.getInt("phone_version_code", 0)
                    if (phoneVersionCode > 0) wearPrefs.phoneVersionCode = phoneVersionCode

                    val eventsJson = dataMap.getString("calendar_events") ?: "[]"
                    val tasksJson = dataMap.getString("due_tasks") ?: "[]"
                    wearPrefs.cachedEvents = eventsJson
                    wearPrefs.cachedTasks = tasksJson

                    Log.d("WearConfigListener", "Updated Wear config from phone: url=$serverUrl, session=$sessionId, events=${eventsJson.take(50)}, tasks=${tasksJson.take(50)}")

                    // Notify view model / UI to reload
                    WearConfigReceiver.onConfigUpdated?.invoke()

                    // Refresh tiles
                    try {
                        TileService.getUpdater(applicationContext)
                            .requestUpdate(CalendarTileService::class.java)
                        TileService.getUpdater(applicationContext)
                            .requestUpdate(TasksTileService::class.java)
                    } catch (e: Exception) {
                        Log.e("WearConfigListener", "Failed to refresh tiles", e)
                    }
                } catch (e: Exception) {
                    Log.e("WearConfigListener", "Error processing DataLayer config event", e)
                }
            }
        }
    }
}

