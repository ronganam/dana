package io.github.ronganam.dana.data.money

import java.math.BigDecimal
import java.math.RoundingMode
import java.text.DecimalFormat
import java.text.NumberFormat
import java.util.Currency
import java.util.Locale
import kotlin.math.abs
import kotlin.math.floor

/**
 * The one money formatter: amounts in the user's [currency] (`/api/me`
 * `currency`, ISO 4217; kept by `Me`) laid out the way the phone's [locale]
 * writes money — symbol, grouping and decimals. Server amounts are in the
 * currency's minor unit (agorot, cents), so a currency with no fraction
 * digits (JPY) takes them as whole units. Money out gets "−" (U+2212),
 * "+" marks money in where asked.
 */
object MoneyFormat {
    const val MINUS = "−"

    @Volatile var currency: Currency = Currency.getInstance("ILS")
    @Volatile var locale: Locale = Locale.getDefault()

    enum class Sign { MINUS, PLUS, NONE }

    /** Set the user's currency from its ISO code; an unknown or blank code keeps the current one. */
    fun use(code: String?) {
        currencyOf(code)?.let { currency = it }
    }

    /** The currency's symbol as [locale] writes it ("₪", "$", "€"). */
    val symbol: String get() = currency.getSymbol(locale)

    private val fractionDigits: Int get() = currency.defaultFractionDigits.coerceAtLeast(0)

    private val minorPerMajor: Long get() = BigDecimal.TEN.pow(fractionDigits).toLong()

    private fun format(cur: Currency, digits: Int): DecimalFormat =
        (NumberFormat.getCurrencyInstance(locale) as DecimalFormat).apply {
            currency = cur
            minimumFractionDigits = digits
            maximumFractionDigits = digits
            roundingMode = RoundingMode.HALF_UP
        }

    /** Whether [locale] writes the symbol before the number. */
    private val symbolFirst: Boolean get() = format(currency, 0).toPattern().substringBefore(";").trimStart().startsWith("¤")

    private fun signed(body: String, negative: Boolean, positive: Boolean, sign: Sign): String = when {
        sign == Sign.NONE -> body
        negative -> MINUS + body
        positive && sign == Sign.PLUS -> "+$body"
        else -> body
    }

    /**
     * Minor units → "₪1,234.50". [cents]: true always shows the fraction,
     * false rounds to whole units, null drops a zero fraction ("₪1,234").
     * [sign]: MINUS ("−" on money out), PLUS (also "+" on money in), NONE (the absolute amount).
     */
    fun amount(minor: Long, sign: Sign = Sign.MINUS, cents: Boolean? = true): String {
        val a = abs(minor)
        val per = minorPerMajor
        val whole = cents == false || fractionDigits == 0 || (cents == null && a % per == 0L)
        val value = if (whole) BigDecimal(floor(a.toDouble() / per + 0.5).toLong()) else BigDecimal(a).movePointLeft(fractionDigits)
        val shownZero = value.signum() == 0
        return signed(format(currency, if (whole) 0 else fractionDigits).format(value), minor < 0 && !shownZero, minor > 0 && !shownZero, sign)
    }

    /** Whole units, rounded ("₪4,018"). */
    fun whole(minor: Long, sign: Sign = Sign.MINUS): String = amount(minor, sign, cents = false)

    /** An amount already in whole units (a project's budget, a price typed as 4,500) → "₪4,500"; [cents] as in [amount]. */
    fun major(value: Double, sign: Sign = Sign.MINUS, cents: Boolean? = false): String = amount(Math.round(value * minorPerMajor), sign, cents)

    /** Short axis and tile values: ₪850, ₪4.8k, ₪12k, ₪1.2M (from minor units). */
    fun compact(minor: Double, sign: Sign = Sign.MINUS): String {
        val v = abs(minor) / minorPerMajor
        fun trim(x: Double) = String.format(locale, "%.1f", x).removeSuffix(".0").removeSuffix(",0")
        fun round(x: Double) = floor(x + 0.5).toLong().toString()
        val number = when {
            v >= 1e6 -> trim(v / 1e6) + "M"
            v >= 1e4 -> round(v / 1e3) + "k"
            v >= 1e3 -> trim(v / 1e3) + "k"
            else -> round(v)
        }
        val body = if (symbolFirst) symbol + number else "$number $symbol"
        return signed(body, minor < 0 && number != "0", minor > 0, sign)
    }

    /** A short value of whole units ("₪12k", "₪850"). */
    fun compactMajor(value: Double): String = compact(value * minorPerMajor)

    /** Whole units in thousands with one decimal, like the web's Projects panel: "₪13.4k", "₪150k"; under 1,000 as [major]. */
    fun thousands(value: Double): String {
        if (abs(value) < 1000) return major(value)
        val k = Math.round(value / 100) / 10.0
        val number = String.format(locale, if (k % 1.0 == 0.0) "%,.0f" else "%,.1f", abs(k)) + "k"
        val body = if (symbolFirst) symbol + number else "$number $symbol"
        return if (k < 0) MINUS + body else body
    }

    /** "Price (₪)", "Budget (₪)": a label for a field that takes amounts. */
    fun label(text: String): String = "$text ($symbol)"

    private fun currencyOf(code: String?): Currency? =
        code?.trim()?.uppercase()?.let { if (it == "NIS") "ILS" else it }?.takeIf { it.isNotEmpty() }
            ?.let { runCatching { Currency.getInstance(it) }.getOrNull() }

    /** Whether [code] is the user's currency ("NIS" counts as ILS). */
    fun isUsers(code: String?): Boolean = currencyOf(code) == currency

    /** A foreign amount in that currency's minor units → "$5.00", "€35.00" (absolute). */
    fun foreign(minor: Long, code: String): String {
        val cur = currencyOf(code) ?: return "${BigDecimal(abs(minor)).movePointLeft(2).toPlainString()} $code".trim()
        val digits = cur.defaultFractionDigits.coerceAtLeast(0)
        return format(cur, digits).format(BigDecimal(abs(minor)).movePointLeft(digits))
    }

    /** A unit price in its own currency (holdings): "₪250.00", "$150.00"; 4 decimals under 1. */
    fun unitPrice(price: Double?, code: String?): String {
        if (price == null || price.isNaN()) return ""
        val cur = currencyOf(code) ?: currency
        val a = abs(price)
        val f = format(cur, 2).apply { if (a < 1) maximumFractionDigits = 4 }
        return f.format(BigDecimal(a))
    }

    /** "650", "₪1,200", "1 200 €" → the number typed (whole units), or null. */
    fun parseMajor(text: String?): Double? {
        val cleaned = (text ?: "").replace(symbol, "").replace(Regex("[\\s,'  ]"), "")
        return cleaned.toDoubleOrNull()
    }
}
