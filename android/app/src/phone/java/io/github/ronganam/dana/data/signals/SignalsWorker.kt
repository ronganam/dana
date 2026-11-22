package io.github.ronganam.dana.data.signals

import android.content.Context
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import io.github.ronganam.dana.data.network.ServerConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * The phone's signal work. Periodic (no mode): read the screen history,
 * compare car mode with what was sent, refresh the cached wake window once a
 * day, bring the geofences in line (afresh once a day), send the queue. The
 * one-time modes do one part: [MODE_SCREEN] after an unlock, [MODE_PLACES] /
 * [MODE_PLACES_FULL] / [MODE_PLACES_OFF] for the geofences.
 */
class SignalsWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val context = applicationContext
        val mode = inputData.getString(MODE)
        if (!ServerConfig.load(context).isConfigured) return@withContext Result.success()
        when (mode) {
            MODE_SCREEN -> ScreenTime.read(context, now = true)
            MODE_PLACES -> Places.refresh(context, full = false)
            MODE_PLACES_FULL -> Places.refresh(context, full = true)
            MODE_PLACES_OFF -> {
                Places.removeAll(context)
                return@withContext Result.success()
            }
            else -> {
                ScreenTime.read(context, now = false)
                CarSignals.reconcileCarMode(context)
                refreshWakeWindow(context)
                Places.refresh(context, full = false)
            }
        }
        val r = SignalSync.queue(context).flush()
        // The periodic run comes back anyway; a one-time run retries a failed send.
        if (r.retry && mode != null) Result.retry() else Result.success()
    }

    /** The wake window the unlocks are measured against, at most a day old. */
    private suspend fun refreshWakeWindow(context: Context) {
        if (System.currentTimeMillis() - SignalSettings.wakeWindowAt(context) < DAY_MS) return
        try {
            ServerConfig.load(context).api().signals.overview().wakeWindow?.let { SignalSettings.setWakeWindow(context, it) }
        } catch (e: Exception) {
            Log.d("SignalsWorker", "Wake window not refreshed: ${e.message}")
        }
    }

    companion object {
        const val MODE = "mode"
        const val MODE_SCREEN = "screen"
        const val MODE_PLACES = "places"
        const val MODE_PLACES_FULL = "places_full"
        const val MODE_PLACES_OFF = "places_off"
        private const val DAY_MS = 24L * 3600 * 1000
    }
}
