package io.github.ronganam.dana.data.signals

/**
 * One thing that happened around the user, as `POST /api/signals` takes it
 * (server `docs/signals.md`). Times are epoch milliseconds.
 */
sealed interface SignalEvent {
    /** The latest moment the event covers: the queue drops events older than [SignalQueue.MAX_AGE_MS] by it. */
    val latest: Long

    /** The phone was unlocked (the wake flow). */
    data class Unlock(val at: Long) : SignalEvent {
        override val latest: Long get() = at
    }

    /** The screen was on from [start] to [end] (what the wake flow confirms with). */
    data class Screen(val start: Long, val end: Long) : SignalEvent {
        override val latest: Long get() = end
    }

    /** Got in (`enter`) or out (`leave`) of the car, noticed [via] Bluetooth or Android Auto. */
    data class Car(val enter: Boolean, val at: Long, val via: CarVia) : SignalEvent {
        override val latest: Long get() = at
    }

    /** Arrived at (`arrive`) or left (`leave`) one of the user's zones (its `zones.id`). */
    data class Zone(val arrive: Boolean, val zoneId: String, val at: Long) : SignalEvent {
        override val latest: Long get() = at
    }
}

/** How the phone noticed the car (`car.via`). */
enum class CarVia(val wire: String) {
    BLUETOOTH("bluetooth"),
    ANDROID_AUTO("android_auto");

    companion object {
        fun of(wire: String?): CarVia? = entries.firstOrNull { it.wire == wire }
    }
}

/** A refused event of a batch: its [index] in the batch and why (`bad_event`, `unknown_zone`, `stale`). */
data class RejectedSignal(val index: Int, val error: String)

/**
 * The answer to one `POST /api/signals`: the HTTP [code] (-1: the server
 * couldn't be reached), how many events it [accepted] and which it [rejected].
 */
data class SignalsPosted(val code: Int, val accepted: Int = 0, val rejected: List<RejectedSignal> = emptyList()) {
    val ok: Boolean get() = code in 200..299
}

/** One source of a signal (`GET /api/signals`): the phone, Home Assistant, Open-Meteo… */
data class SignalSource(
    val source: String,
    val label: String,
    /** The source Dana currently takes this signal from. */
    val active: Boolean,
    /** When it last reported (ISO 8601 with offset), null: never. */
    val lastAt: String?,
    /** What the last report was ("unlock", a zone's name…), "" when nothing to say. */
    val detail: String
)

/** One signal (wake, screen, car, location, sleep, weather) and its sources, highest rank first. */
data class SignalInfo(val signal: String, val sources: List<SignalSource>) {
    val activeSource: SignalSource? get() = sources.firstOrNull { it.active }
}

/** The user's wake window ("HH:MM"); [learned] = from their unlocks, not set by hand. */
data class WakeWindow(val start: String, val end: String, val learned: Boolean) {
    /** Whether [minuteOfDay] (0..1439) falls in the window (one that runs past midnight wraps). */
    fun contains(minuteOfDay: Int): Boolean {
        val s = minutes(start) ?: return false
        val e = minutes(end) ?: return false
        return if (s <= e) minuteOfDay in s until e else minuteOfDay >= s || minuteOfDay < e
    }

    companion object {
        /** What the server uses until it has learned one. */
        val DEFAULT = WakeWindow("05:00", "12:00", learned = true)

        /** "07:30" → 450; null for anything else. */
        fun minutes(hhmm: String): Int? {
            val parts = hhmm.split(":")
            if (parts.size != 2) return null
            val h = parts[0].toIntOrNull() ?: return null
            val m = parts[1].toIntOrNull() ?: return null
            return if (h in 0..23 && m in 0..59) h * 60 + m else null
        }

        /** 450 → "07:30". */
        fun hhmm(minuteOfDay: Int): String = String.format(java.util.Locale.ROOT, "%02d:%02d", minuteOfDay / 60, minuteOfDay % 60)
    }
}

/** `GET /api/signals`: every signal in the server's order, and the wake window. */
data class SignalsOverview(val signals: List<SignalInfo>, val wakeWindow: WakeWindow?) {
    fun signal(name: String): SignalInfo? = signals.firstOrNull { it.signal == name }
}
