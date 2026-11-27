package io.github.ronganam.dana.push

/**
 * At most one automatic push registration per [MIN_INTERVAL_MS]: when the
 * server says it forgot this phone ([PhoneEvents.catchUp]) and in the
 * background sync's [PushSetup.ensureRegistered]. Without it a phone the
 * server keeps refusing (or one with no push app) asked again on every
 * fetch, and each attempt fetched again: a loop of dozens of requests a
 * second. The time of the last attempt is kept by the caller ([read] /
 * [write], the app's prefs), so a restart doesn't reopen the gate. A clock
 * set back (the last attempt "in the future") opens it.
 */
class RegistrationGate(
    private val now: () -> Long,
    private val read: () -> Long,
    private val write: (Long) -> Unit,
    private val intervalMs: Long = MIN_INTERVAL_MS,
) {
    /** True (and the attempt is noted) when an attempt may go now. */
    @Synchronized
    fun tryAcquire(): Boolean {
        val t = now()
        val last = read()
        if (last in 1..t && t - last < intervalMs) return false
        write(t)
        return true
    }

    companion object {
        const val MIN_INTERVAL_MS = 15L * 60 * 1000
    }
}
