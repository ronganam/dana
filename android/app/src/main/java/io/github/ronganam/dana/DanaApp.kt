package io.github.ronganam.dana

import android.app.Application
import androidx.work.Configuration

/**
 * Supplies WorkManager's configuration so it starts on demand in every
 * process. The assistant sheet runs in `:session`, where androidx.startup's
 * default initializer never runs (content providers start only in the main
 * process): any WorkManager call from there — the sheet's view model syncs the
 * Inbox on start — crashed with "WorkManager is not initialized". Work queued
 * from `:session` is still run by the main process only.
 */
open class DanaApp : Application(), Configuration.Provider {
    override fun onCreate() {
        super.onCreate()
        // Direct push (phone flavor; a no-op on the kiosk and the TV). Only in
        // the main process: the assistant sheet's `:session` process skips it.
        if (android.os.Build.VERSION.SDK_INT < 28 || getProcessName() == packageName) {
            io.github.ronganam.dana.push.PushSetup.init(this)
            io.github.ronganam.dana.data.wake.UnlockWatcher.register(this)
        }
    }

    override val workManagerConfiguration: Configuration
        get() = Configuration.Builder().setDefaultProcessName(packageName).build()
}
