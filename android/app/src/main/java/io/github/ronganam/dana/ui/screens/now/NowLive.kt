package io.github.ronganam.dana.ui.screens.now

import io.github.ronganam.dana.data.models.LiveAction
import io.github.ronganam.dana.data.models.LiveActivity
import io.github.ronganam.dana.data.models.isoMillis
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/*
 * Now's live part, from the live activities (server core/live.py, `GET /api/live`
 * and the `live` push): a run waiting for your OK goes to "Needs you" (first),
 * everything else running is "Going on". Pure rules, tested in NowLiveTest.
 */

/** What a live row shows at its end. */
sealed class LiveTrailing {
    /** A run's plan: [done] of [total] steps (a small step line and "3 of 4"). */
    data class Steps(val done: Int, val total: Int) : LiveTrailing()
    /** Counting up from [sinceMs] ("14 min"). */
    data class Since(val sinceMs: Long) : LiveTrailing()
    /** Counting down to [toMs]: shown as its clock time ("09:16"). */
    data class At(val toMs: Long, val label: String) : LiveTrailing()
    data object None : LiveTrailing()
}

/** The kind of thing a live activity is, for its glyph. */
enum class LiveGlyph { RUN, CODE, TRAIN, BUS, CAR, TIMER, OTHER }

/** One live activity as a Now row. */
data class NowLiveRow(
    val id: String,
    val title: String,
    /** What it does now ("Running the tests"), else its context. */
    val line: String,
    val glyph: LiveGlyph,
    val trailing: LiveTrailing,
    /** Waiting for the owner (tone `warn`): it goes to "Needs you". */
    val waiting: Boolean,
    /** Its `server` buttons (Approve / Deny), at most two. */
    val actions: List<LiveAction>,
    /** Where tapping it goes (a "/chat/<id>", a screen, https), "" = nowhere special. */
    val open: String
)

object NowLive {

    /** More steps than this read as a plain bar on the shade; on Now they show as "N of M" only. */
    private const val MAX_STEP_DOTS = 8

    private const val WAITING_PREFIX = "Needs you · "

    /** The rows: running activities only (ended ones and their finals stay on the shade), waiting first, else server order. */
    fun rows(activities: List<LiveActivity>): List<NowLiveRow> =
        activities.filter { !it.ended }.map(::row).sortedBy { !it.waiting }

    fun row(a: LiveActivity): NowLiveRow {
        val look = a.look()
        val waiting = look.tone == "warn"
        val title = look.title.removePrefix(WAITING_PREFIX).ifBlank { a.source.ifBlank { "Running" } }
        val line = look.text.ifBlank { look.context }
        return NowLiveRow(
            id = a.id,
            title = title,
            line = line,
            glyph = glyph(look.icon, look.context),
            trailing = trailing(a),
            waiting = waiting,
            actions = a.actions.filter { it.kind == "server" }.take(2),
            open = look.open
        )
    }

    fun glyph(icon: String, context: String): LiveGlyph = when {
        icon == "train-front" || icon == "tram-front" -> LiveGlyph.TRAIN
        icon == "bus" -> LiveGlyph.BUS
        icon == "car" -> LiveGlyph.CAR
        icon == "timer" || icon == "clock" -> LiveGlyph.TIMER
        // A coding agent's session (Claude Code, Codex) says so in its context; a watch's run is Dana working.
        icon == "bot" && (context.startsWith("Claude") || context.startsWith("Codex") || context.startsWith("T3")) -> LiveGlyph.CODE
        icon == "bot" -> LiveGlyph.RUN
        else -> LiveGlyph.OTHER
    }

    /** Steps when its progress is a few equal segments, else its countdown, else how long it has run. */
    fun trailing(a: LiveActivity): LiveTrailing {
        val look = a.look()
        val p = look.progress
        if (p != null && !p.indeterminate && p.segments.size in 2..MAX_STEP_DOTS && p.segments.all { it.length == 1 }) {
            return LiveTrailing.Steps(p.value.coerceIn(0, p.segments.size), p.segments.size)
        }
        look.countdown?.toMs?.let { return LiveTrailing.At(it, look.countdown.label) }
        isoMillis(look.since)?.let { return LiveTrailing.Since(it) }
        return LiveTrailing.None
    }

    /** "now", "14 min", "2 h 5 min". */
    fun elapsed(sinceMs: Long, nowMs: Long): String {
        val min = ((nowMs - sinceMs).coerceAtLeast(0) / 60_000).toInt()
        return when {
            min < 1 -> "now"
            min < 60 -> "$min min"
            min % 60 == 0 -> "${min / 60} h"
            else -> "${min / 60} h ${min % 60} min"
        }
    }

    private val HM = DateTimeFormatter.ofPattern("HH:mm")

    fun clock(ms: Long, zone: ZoneId = ZoneId.systemDefault()): String = Instant.ofEpochMilli(ms).atZone(zone).format(HM)
}
