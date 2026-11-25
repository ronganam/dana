package io.github.ronganam.dana.data.models

import io.github.ronganam.dana.data.network.LenientList
import io.github.ronganam.dana.data.network.OptString
import kotlinx.serialization.Serializable
import java.time.OffsetDateTime

/**
 * Live activities (server `core/live.py`, wire form `GET /api/live` and the
 * `live` push; fixture `server/tests/fixtures/live_web.json`). These classes
 * are their own wire form, so the phone can keep the last payload and redraw
 * from it; omitted fields take the defaults, unknown keys are ignored.
 * Datetimes stay ISO 8601 text; the `*Ms` properties read them.
 */

/** Epoch millis of an ISO 8601 datetime with offset; null when blank or unreadable. */
internal fun isoMillis(iso: String?): Long? =
    iso?.takeIf { it.isNotBlank() }?.let { runCatching { OffsetDateTime.parse(it).toInstant().toEpochMilli() }.getOrNull() }

/** A countdown to [to] (`"Departs"` as [label]). */
@Serializable
data class LiveCountdown(val to: String = "", val label: String = "") {
    val toMs: Long? get() = isoMillis(to)
}

/** One segment of a progress bar: [length] units in a tone. */
@Serializable
data class LiveSegment(val length: Int = 1, val tone: String = "neutral")

/** A point on a progress bar at unit [at]. */
@Serializable
data class LivePoint(val at: Int = 0, val tone: String = "neutral")

/** A segmented progress bar; [tracker] is an icon name riding it at [value]. */
@Serializable
data class LiveProgress(
    val segments: LenientList<LiveSegment> = emptyList(),
    val points: LenientList<LivePoint> = emptyList(),
    val value: Int = 0,
    val tracker: String = "",
    val indeterminate: Boolean = false,
    val styledByProgress: Boolean = true,
) {
    /** The bar's length: the segments' lengths summed (100 without segments, like Android's ProgressStyle). */
    val max: Int get() = if (segments.isEmpty()) 100 else segments.sumOf { it.length.coerceAtLeast(1) }
}

/** A button: [kind] `server` (the add-on handles [id]), `next`, `prev` or `stop`. */
@Serializable
data class LiveAction(val id: String = "", val label: String = "", val kind: String = "server")

/** What an ended activity leaves behind (posted once, then forgotten). */
@Serializable
data class LiveFinal(val title: String = "", val text: String = "", val tone: String = "neutral", val open: String = "")

/** An alternative the user can switch to: only the display fields that differ from the activity's (null = same). */
@Serializable
data class LiveChoice(
    val key: String = "",
    val title: OptString = null,
    val text: OptString = null,
    val context: OptString = null,
    val icon: OptString = null,
    val tone: OptString = null,
    val countdown: LiveCountdown? = null,
    val since: OptString = null,
    val chip: OptString = null,
    val progress: LiveProgress? = null,
    val open: OptString = null,
)

/** What an activity shows: its own display fields overridden by the selected choice's ([LiveActivity.look]). */
data class LiveLook(
    val title: String,
    val text: String,
    val context: String,
    val icon: String,
    val tone: String,
    val countdown: LiveCountdown?,
    val since: String,
    val chip: String,
    val progress: LiveProgress?,
    val open: String,
) {
    val sinceMs: Long? get() = isoMillis(since)
}

/** One live activity as the server sends it. */
@Serializable
data class LiveActivity(
    val id: String = "",
    val addon: String = "",
    val source: String = "",
    val rev: Int = 0,
    val updatedAt: String = "",
    val startedAt: String = "",
    val title: String = "",
    val text: String = "",
    val context: String = "",
    val icon: String = "",
    val tone: String = "neutral",
    val countdown: LiveCountdown? = null,
    val since: String = "",
    val chip: String = "",
    val progress: LiveProgress? = null,
    val actions: LenientList<LiveAction> = emptyList(),
    val choices: LenientList<LiveChoice> = emptyList(),
    val selected: String = "",
    val open: String = "",
    val alert: String = "",
    val endsAt: String = "",
    val ended: Boolean = false,
    val final: LiveFinal? = null,
) {
    val updatedMs: Long? get() = isoMillis(updatedAt)
    val endsAtMs: Long? get() = isoMillis(endsAt)

    /** The selected choice's key: [selected] when it names a choice, else the first choice's ("" without choices). */
    val selectedKey: String get() = choices.firstOrNull { it.key == selected }?.key ?: choices.firstOrNull()?.key.orEmpty()

    /** What to show: the top-level fields, overridden by the selected choice's. */
    fun look(): LiveLook {
        val c = choices.firstOrNull { it.key == selectedKey }
        return LiveLook(
            title = c?.title ?: title,
            text = c?.text ?: text,
            context = c?.context ?: context,
            icon = c?.icon ?: icon,
            tone = c?.tone ?: tone,
            countdown = c?.countdown ?: countdown,
            since = c?.since ?: since,
            chip = c?.chip ?: chip,
            progress = c?.progress ?: progress,
            open = c?.open ?: open,
        )
    }
}

/** `GET /api/live` and the `live` push: every activity, newest-updated first. */
@Serializable
data class LiveList(val activities: LenientList<LiveActivity> = emptyList())
