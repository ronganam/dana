package app.ganam.assistant.data.money

import java.text.DecimalFormat
import java.text.DecimalFormatSymbols
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
 * `static/money-core.js`): shekels from agorot, months, "2 h ago".
 * Tested in `MoneyTextTest`.
 */
object MoneyText {
    private val whole = DecimalFormat("#,##0", DecimalFormatSymbols(Locale.US))
    private val cents = DecimalFormat("#,##0.00", DecimalFormatSymbols(Locale.US))

    /**
     * "₪1,234" (whole shekels) or "₪1,234.50" when [exact] and there are
     * agorot; money out (negative) gets a minus sign "−₪12". [signed] puts
     * "+" before money in.
     */
    fun shekels(agorot: Long, exact: Boolean = false, signed: Boolean = false): String {
        val a = abs(agorot)
        val body = if (exact && a % 100 != 0L) cents.format(a / 100.0) else whole.format(Math.round(a / 100.0))
        val sign = when {
            agorot < 0 -> "−"
            signed && agorot > 0 -> "+"
            else -> ""
        }
        return "$sign₪$body"
    }

    /** "₪12.4k" / "₪1.2M" for chart axes and tight tiles. */
    fun shekelsShort(agorot: Long): String {
        val s = abs(agorot) / 100.0
        val sign = if (agorot < 0) "−" else ""
        return sign + when {
            s >= 1_000_000 -> "₪" + trim(s / 1_000_000) + "M"
            s >= 10_000 -> "₪" + Math.round(s / 1000) + "k"
            s >= 1_000 -> "₪" + trim(s / 1000) + "k"
            else -> "₪" + Math.round(s)
        }
    }

    private fun trim(v: Double): String = String.format(Locale.US, "%.1f", v).removeSuffix(".0")

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
