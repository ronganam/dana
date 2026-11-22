package io.github.ronganam.dana.wear.data

import io.github.ronganam.dana.protocol.WearConfig
import io.github.ronganam.dana.protocol.WearPaths
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
            if (event.type == DataEvent.TYPE_CHANGED && event.dataItem.uri.path == WearPaths.CONFIG) {
                try {
                    val config = WearConfig.from(DataMapItem.fromDataItem(event.dataItem).dataMap)
                    val serverUrl = config.serverUrl
                    val sessionId = config.sessionId

                    val wearPrefs = WearPrefs(applicationContext)
                    wearPrefs.phoneServerUrl = serverUrl
                    if (sessionId.isNotEmpty()) wearPrefs.sessionId = sessionId
                    // The watch pairs itself with the phone's server (and again when it changes).
                    if (serverUrl.isNotEmpty()) WatchPairing.ensure(applicationContext)

                    val phoneVersionCode = config.phoneVersionCode
                    if (phoneVersionCode > 0) wearPrefs.phoneVersionCode = phoneVersionCode

                    val eventsJson = config.calendarEventsJson
                    val tasksJson = config.dueTasksJson
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

