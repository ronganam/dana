package io.github.ronganam.dana.data.signals

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.provider.Settings
import android.util.Log
import androidx.activity.result.contract.ActivityResultContract
import androidx.core.net.toUri
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.workDataOf
import java.util.concurrent.TimeUnit

/**
 * The phone's signal sources (`docs/phone.md` → Signals): unlocks
 * (`data/wake/UnlockWatcher`), screen time ([ScreenTime]), the car
 * ([CarSignals], driving detection: [Driving]), places ([Places]) and sleep
 * from Health Connect ([HealthSleep]). No foreground service and no
 * continuous location: system broadcasts the app is woken for, geofences and
 * activity transitions Google Play services watches, and one periodic
 * [SignalsWorker] run (every 15 min with screen time on, else hourly).
 */
object PhoneSignals {
    private const val TAG = "PhoneSignals"
    private const val PERIODIC = "signals-periodic"
    private const val AFTER_UNLOCK = "signals-after-unlock"
    private const val PLACES = "signals-places"
    private const val SLEEP = "signals-sleep"
    /** The server checks ten minutes after an unlock: read the screen history before that. */
    private const val AFTER_UNLOCK_MINUTES = 5L

    /** App start (main process): the periodic run, the car-mode watch, driving detection, the geofences when places are on. */
    fun init(context: Context) {
        val app = context.applicationContext
        schedulePeriodic(app)
        watch(app)
        if (SignalSettings.driving(app)) Driving.sync(app)
        if (SignalSettings.places(app)) runPlaces(app, full = false, policy = ExistingWorkPolicy.KEEP)
    }

    /** Runtime receivers for what the manifest can't declare (car mode); from the app and the notification listener. */
    fun watch(context: Context) = CarSignals.watchCarMode(context.applicationContext)

    /** An unlock in the wake window: read the sleep sessions now, the screen history a few minutes later. */
    fun afterUnlock(context: Context) {
        readSleep(context)
        if (!SignalSettings.screenTime(context)) return
        WorkManager.getInstance(context.applicationContext).enqueueUniqueWork(
            AFTER_UNLOCK, ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<SignalsWorker>()
                .setInputData(workDataOf(SignalsWorker.MODE to SignalsWorker.MODE_SCREEN))
                .setInitialDelay(AFTER_UNLOCK_MINUTES, TimeUnit.MINUTES)
                .setConstraints(network())
                .build()
        )
    }

    /** The user's zones changed (the `zones_changed` phone event): register the geofences again. */
    fun zonesChanged(context: Context) {
        if (SignalSettings.places(context)) runPlaces(context, full = false, policy = ExistingWorkPolicy.REPLACE)
    }

    /** The app came to the foreground: Health Connect answers a read then even without the background permission. */
    fun onForeground(context: Context) = readSleep(context)

    /** After a reboot or an app update: Android dropped the geofences and the activity transitions. */
    fun onBoot(context: Context) {
        if (SignalSettings.driving(context)) Driving.sync(context)
        SignalSettings.setFences(context, emptyList())
        if (SignalSettings.places(context)) runPlaces(context, full = true, policy = ExistingWorkPolicy.REPLACE)
    }

    /** The switches and their access (asks Health Connect, hence suspend). */
    suspend fun status(context: Context): PhoneSignalStatus {
        val health = HealthSleep.state(context)
        val access = HealthSleep.access(context)
        return PhoneSignalStatus(
            screenTime = SignalSettings.screenTime(context),
            screenTimeSupported = ScreenTime.supported,
            usageAccess = ScreenTime.hasAccess(context),
            car = SignalSettings.car(context),
            carDevice = SignalSettings.carDevice(context)?.second,
            bluetoothAllowed = CarSignals.canListDevices(context),
            places = SignalSettings.places(context),
            location = locationAccess(context),
            fences = SignalSettings.fences(context).size,
            driving = SignalSettings.driving(context),
            activityAllowed = Driving.allowed(context),
            sleep = SignalSettings.sleep(context),
            healthConnect = health,
            sleepAllowed = access.sleep,
            sleepBackgroundSupported = access.backgroundSupported,
            sleepBackground = access.background
        )
    }

    fun setScreenTime(context: Context, on: Boolean) {
        SignalSettings.setScreenTime(context, on)
        // The history is read from now on, not from before the switch was turned on.
        if (on) SignalSettings.setScreenRead(context, System.currentTimeMillis(), null)
        schedulePeriodic(context)
    }

    fun setCar(context: Context, on: Boolean) {
        SignalSettings.setCar(context, on)
        if (on) CarSignals.reconcileCarMode(context, announce = false)
        if (SignalSettings.driving(context)) Driving.sync(context)
    }

    /** "Detect driving" (needs the car switch and Physical activity). */
    fun setDriving(context: Context, on: Boolean) {
        SignalSettings.setDriving(context, on)
        if (!on) SignalSettings.clearCarState(context, CarVia.ACTIVITY)
        Driving.sync(context)
    }

    /** "Wake from sleep data": the sessions written from now on are read. */
    fun setSleep(context: Context, on: Boolean) {
        SignalSettings.setSleep(context, on)
        if (on) SignalSettings.setSleepCursor(context, System.currentTimeMillis())
    }

    fun pairedDevices(context: Context): List<BluetoothChoice> = CarSignals.pairedDevices(context)

    fun setCarDevice(context: Context, device: BluetoothChoice?) = SignalSettings.setCarDevice(context, device?.address, device?.name)

    fun setPlaces(context: Context, on: Boolean) {
        SignalSettings.setPlaces(context, on)
        if (on) runPlaces(context, full = true, policy = ExistingWorkPolicy.REPLACE)
        else runPlaces(context, full = true, policy = ExistingWorkPolicy.REPLACE, remove = true)
    }

    /** The Health Connect permissions "Wake from sleep data" asks for, and the contract that asks. */
    fun sleepPermissions(context: Context): Set<String> = HealthSleep.permissions(context)
    fun sleepPermissionContract(): ActivityResultContract<Set<String>, Set<String>> = HealthSleep.permissionContract()

    /** Health Connect's store page (install or update it); the web page when there is no Play Store. */
    fun openHealthConnectInstall(context: Context) = startFirst(context, HealthSleep.installIntent(), HealthSleep.installWebIntent())

    /** Health Connect's own settings (Dana's permissions there, the apps that write sleep). */
    fun openHealthConnect(context: Context) = startFirst(context, HealthSleep.settingsIntent(context))

    private fun startFirst(context: Context, vararg intents: Intent) {
        for (intent in intents) {
            try {
                context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                return
            } catch (e: Exception) {
                Log.d(TAG, "Nothing opens $intent")
            }
        }
    }

    /** Settings → Usage access (Dana's own page where the phone has one). */
    fun openUsageAccess(context: Context) {
        val withPackage = Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS, "package:${context.packageName}".toUri())
        val plain = Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS)
        for (intent in listOf(withPackage, plain)) {
            try {
                context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                return
            } catch (e: Exception) {
                Log.d(TAG, "No usage access page for $intent")
            }
        }
    }

    fun locationAccess(context: Context): LocationAccess {
        fun granted(p: String) = context.checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED
        if (!granted(Manifest.permission.ACCESS_FINE_LOCATION)) return LocationAccess.NONE
        return if (Build.VERSION.SDK_INT < 29 || granted(Manifest.permission.ACCESS_BACKGROUND_LOCATION)) LocationAccess.ALWAYS
        else LocationAccess.WHILE_IN_USE
    }

    private fun network() = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

    private fun schedulePeriodic(context: Context) {
        val minutes = if (SignalSettings.screenTime(context)) 15L else 60L
        WorkManager.getInstance(context.applicationContext).enqueueUniquePeriodicWork(
            PERIODIC, ExistingPeriodicWorkPolicy.UPDATE,
            PeriodicWorkRequestBuilder<SignalsWorker>(minutes, TimeUnit.MINUTES).setConstraints(network()).build()
        )
    }

    /** A one-time sleep read (KEEP: one waiting is enough). */
    private fun readSleep(context: Context) {
        if (!SignalSettings.sleep(context)) return
        WorkManager.getInstance(context.applicationContext).enqueueUniqueWork(
            SLEEP, ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<SignalsWorker>()
                .setInputData(workDataOf(SignalsWorker.MODE to SignalsWorker.MODE_SLEEP))
                .setConstraints(network())
                .build()
        )
    }

    private fun runPlaces(context: Context, full: Boolean, policy: ExistingWorkPolicy, remove: Boolean = false) {
        val mode = when {
            remove -> SignalsWorker.MODE_PLACES_OFF
            full -> SignalsWorker.MODE_PLACES_FULL
            else -> SignalsWorker.MODE_PLACES
        }
        val builder = OneTimeWorkRequestBuilder<SignalsWorker>().setInputData(workDataOf(SignalsWorker.MODE to mode))
        // Removing the geofences needs no network.
        if (!remove) builder.setConstraints(network())
        WorkManager.getInstance(context.applicationContext).enqueueUniqueWork(PLACES, policy, builder.build())
    }
}
