package io.github.ronganam.dana.data.money

import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.YearMonth
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.abs

/**
 * Pure words and numbers of the Money screens (like the web's
 * `server/static/money-core.js`): months, "2 h ago". Amounts: [MoneyFormat].
 * Tested in `MoneyTextTest`.
 */
object MoneyText {
    fun thisMonth(today: LocalDate = LocalDate.now()): String = YearMonth.from(today).toString()

    /** "2026-11" ± [by] months. */
    fun shiftMonth(month: String, by: Int): String = YearMonth.parse(month).plusMonths(by.toLong()).toString()

    /** "November 2026" (or "November" for the current year when [short]). */
    fun monthLabel(month: String, short: Boolean = false, today: LocalDate = LocalDate.now()): String {
        val ym = YearMonth.parse(month)
        val name = ym.month.getDisplayName(java.time.format.TextStyle.FULL, Locale.ENGLISH)
        return if (short && ym.year == today.year) name else "$name ${ym.year}"
    }

    /** "18 Nov" or "18 Nov 2025" for another year, from `YYYY-MM-DD`. */
    fun day(date: String, today: LocalDate = LocalDate.now()): String = runCatching {
        val d = LocalDate.parse(date.take(10))
        d.format(DateTimeFormatter.ofPattern(if (d.year == today.year) "d MMM" else "d MMM yyyy", Locale.ENGLISH))
    }.getOrDefault(date)

    /** "just now", "5 min ago", "2 h ago", "3 days ago" from an ISO time. */
    fun ago(iso: String?, now: Instant = Instant.now()): String {
        if (iso.isNullOrBlank()) return "never"
        val t = runCatching { OffsetDateTime.parse(iso).toInstant() }.getOrNull() ?: return ""
        val m = Duration.between(t, now).toMinutes()
        return when {
            m < 1 -> "just now"
            m < 60 -> "$m min ago"
            m < 48 * 60 -> "${m / 60} h ago"
            else -> "${m / 1440} days ago"
        }
    }
}
