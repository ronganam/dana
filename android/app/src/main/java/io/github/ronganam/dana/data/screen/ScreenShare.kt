package io.github.ronganam.dana.data.screen

/**
 * The power-button sheet's "Screen" control as a pure state machine (tested in
 * `ScreenShareTest`). The system hands the assistant the app underneath
 * (`onHandleAssist`: text, `onHandleScreenshot`: image) when the sheet opens;
 * it is kept here, in memory, for that one opening, and goes out only with a
 * message sent while [on] — then [on] turns itself off.
 *
 * [I] is the screenshot (a bitmap + JPEG on the phone, anything in tests).
 */
data class ScreenShare<I>(
    val source: Source = Source.NONE,
    /** The user turned off "use text from screen" for the assistant (then no screenshot either). */
    val textOffInSettings: Boolean = false,
    /** The user turned off "use screenshot" for the assistant. */
    val screenshotOffInSettings: Boolean = false,
    /** The app's text arrived (or failed: [screen] null). */
    val textArrived: Boolean = false,
    /** The screenshot arrived (or the system said there is none: [shot] null). */
    val shotArrived: Boolean = false,
    val screen: DeviceScreen? = null,
    val shot: I? = null,
    /** Goes with the next message. */
    val on: Boolean = false,
    /** The one-line "why not" under an unavailable control is showing. */
    val explaining: Boolean = false
) {
    enum class Source {
        /** No capture at all (kiosk, TV, between openings). */
        NONE,
        /** Opened from Dana itself (the Ask Dana widget, Dana's own screen): nothing worth sending. */
        DANA,
        /** Opened by the system over another app (power button, assist gesture). */
        SYSTEM
    }

    enum class Availability {
        /** Not offered (kiosk / TV / opened from Dana). */
        HIDDEN,
        /** Waiting for the system's capture (a fraction of a second after opening). */
        PENDING,
        /** Something can be sent. */
        READY,
        /** The system switches for the assistant are off. */
        OFF_IN_SETTINGS,
        /** The app underneath allows neither its text nor a screenshot. */
        BLOCKED
    }

    val hasText: Boolean get() = screen?.hasText == true
    val hasShot: Boolean get() = shot != null

    val availability: Availability
        get() = when {
            source != Source.SYSTEM -> Availability.HIDDEN
            textOffInSettings -> Availability.OFF_IN_SETTINGS
            hasText || hasShot -> Availability.READY
            !textArrived || (!shotArrived && !screenshotOffInSettings) -> Availability.PENDING
            else -> Availability.BLOCKED
        }

    /** What is missing from a READY capture, to say next to the thumbnail. */
    val missing: Missing
        get() = when {
            availability != Availability.READY -> Missing.NOTHING
            !hasShot && screenshotOffInSettings -> Missing.SCREENSHOT_OFF_IN_SETTINGS
            !hasShot && shotArrived -> Missing.SCREENSHOT_BLOCKED
            !hasShot -> Missing.NOTHING // still coming
            !hasText && textArrived -> Missing.TEXT
            else -> Missing.NOTHING
        }

    enum class Missing { NOTHING, SCREENSHOT_OFF_IN_SETTINGS, SCREENSHOT_BLOCKED, TEXT }

    /** The control's one tap: on/off when there is something to send, else show/hide why not. */
    fun tap(): ScreenShare<I> = when (availability) {
        Availability.READY, Availability.PENDING -> copy(on = !on, explaining = false)
        Availability.HIDDEN -> this
        else -> copy(explaining = !explaining, on = false)
    }

    /** The × next to the thumbnail. */
    fun remove(): ScreenShare<I> = copy(on = false)

    fun withText(screen: DeviceScreen?): ScreenShare<I> = copy(textArrived = true, screen = screen).settle()

    fun withShot(shot: I?): ScreenShare<I> = copy(shotArrived = true, shot = shot).settle()

    /** A capture that turned out empty can't stay on, and a ready one needs no explanation. */
    private fun settle(): ScreenShare<I> = when (availability) {
        Availability.READY -> copy(explaining = false)
        Availability.PENDING -> this
        else -> copy(on = false)
    }

    /**
     * A message is being sent: what goes with it (null when the control is off
     * or nothing usable arrived), and the control turns off either way.
     */
    fun take(): Pair<ScreenShare<I>, Taken<I>?> {
        if (!on) return this to null
        val off = copy(on = false)
        if (availability != Availability.READY) return off to null
        return off to Taken(screen, shot)
    }

    /** A refused send (the thread was busy): it goes with the next message instead. */
    fun putBack(): ScreenShare<I> = if (availability == Availability.READY) copy(on = true) else this

    data class Taken<I>(val screen: DeviceScreen?, val shot: I?)

    companion object {
        /** A fresh opening: everything from the last one is dropped, the control starts off. */
        fun <I> opened(
            source: Source,
            textOffInSettings: Boolean,
            screenshotOffInSettings: Boolean,
            /** The show asked for a screenshot (`SHOW_WITH_SCREENSHOT`); without it none will come. */
            screenshotRequested: Boolean = true
        ) = ScreenShare<I>(
            source = source,
            textOffInSettings = textOffInSettings,
            // The system sends no screenshot without the text switch either.
            screenshotOffInSettings = screenshotOffInSettings || textOffInSettings,
            shotArrived = !screenshotRequested
        )
    }
}
