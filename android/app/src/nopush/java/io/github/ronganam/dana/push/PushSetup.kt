package io.github.ronganam.dana.push

import android.content.Context

/** Kiosk / TV: no direct push (the server only pushes to the phone). */
object PushSetup {
    fun init(context: Context) {}
    fun ensureRegistered(context: Context, force: Boolean = false) {}
    /** No Settings → Push section. */
    fun status(context: Context): PushStatus? = null
}
