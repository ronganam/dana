package io.github.ronganam.dana.data.money

import kotlinx.serialization.json.JsonObject
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.TextStyle
import java.util.Locale
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.roundToLong

/**
 * The pure part of Transactions, the detail, Accounts and the banners (the
 * web's `server/static/money-core.js`): the list items and the detail
 * (`GET /api/money/transactions…`), day grouping, tones, badges, the
 * "Counts as" choices, the filters ↔ query, the category taxonomy, account
 * names, a login's state and the words of answers and errors. Every number is
 * the server's (agorot, signed: money out is negative). Tested in
 * `MoneyLedgerTest` against `server/tests/fixtures/money_web.json`.
 */

/** A row's badge: provisional | pending | unmatched | installment (n/total) | foreign (amount, currency). */
data class TxnBadge(val type: String, val number: Int = 0, val total: Int = 0, val amount: Long = 0, val currency: String = "")

data class Installment(val number: Int, val total: Int, val purchaseAmount: Long?)
data class Foreign(val amount: Long, val currency: String)
data class SameMerchant(val count: Int, val total: Long, val firstDate: String?)
data class TxnMealCycle(val start: String?, val end: String?, val places: List<String>)

/** What only `GET transactions/{id}` carries. */
data class TxnExtras(
    val installment: Installment?,
    val foreign: Foreign?,
    val bankCategory: String?,
    val sameMerchant: SameMerchant?,
    /** The user's marks: true / false / null (Dana decides). */
    val oneOff: Boolean?,
    val yearly: Boolean?,
    val mealCycle: TxnMealCycle?
)

/** A list item (or the detail, with [extras]). */
data class Txn(
    val id: String,
    val accountId: String,
    val accountKind: String,
    val accountLabel: String,
    val purchaseDate: String,
    val chargeDate: String?,
    val occurredAt: String?,
    val amount: Long,
    val merchant: String,
    val merchantKey: String,
    val description: String,
    val memo: String?,
    val category: String?,
    val categoryLabel: String,
    val group: String?,
    val categorySource: String?,
    val flow: String?,
    val spending: Boolean,
    /** fixed | yearly | one_off | everyday | null (not spending). */
    val kind: String?,
    val status: String,
    val note: String?,
    val badges: List<TxnBadge>,
    val extras: TxnExtras? = null
) {
    val title: String get() = merchant.ifBlank { description }.ifBlank { "—" }

    companion object {
        fun parse(o: JsonObject): Txn = Txn(
            id = o.string("id"),
            accountId = o.str("account_id") ?: "",
            accountKind = o.str("account_kind") ?: "",
            accountLabel = o.str("account_label") ?: "",
            purchaseDate = o.str("purchase_date") ?: "",
            chargeDate = o.str("charge_date"),
            occurredAt = o.str("occurred_at"),
            amount = o.longValue("amount"),
            merchant = o.str("merchant") ?: "",
            merchantKey = o.str("merchant_key") ?: "",
            description = o.str("description") ?: "",
            memo = o.str("memo"),
            category = o.str("category"),
            categoryLabel = o.str("category_label") ?: "Uncategorized",
            group = o.str("group"),
            categorySource = o.str("category_source"),
            flow = o.str("flow"),
            spending = o.flag("spending"),
            kind = o.str("kind"),
            status = o.str("status") ?: "",
            note = o.str("note"),
            badges = o.arr("badges").objects().map {
                TxnBadge(
                    type = it.string("type"),
                    number = it.intValue("number"),
                    total = it.intValue("total"),
                    amount = it.longValue("amount"),
                    currency = it.string("currency")
                )
            },
            extras = if ("same_merchant" !in o && "installment" !in o && "one_off" !in o) null else TxnExtras(
                installment = o.obj("installment")?.let {
                    Installment(it.intValue("number"), it.intValue("total"), if (it.absent("purchase_amount")) null else it.longValue("purchase_amount"))
                },
                foreign = o.obj("foreign")?.let { Foreign(it.longValue("amount"), it.string("currency")) },
                bankCategory = o.str("bank_category"),
                sameMerchant = o.obj("same_merchant")?.let { SameMerchant(it.intValue("count"), it.longValue("total"), it.str("first_date")) },
                oneOff = o.bool("one_off"),
                yearly = o.bool("yearly"),
                mealCycle = o.obj("meal_cycle")?.let { mc ->
                    TxnMealCycle(mc.str("start"), mc.str("end"), mc.arr("meals").objects().map { it.str("place") ?: "—" })
                }
            )
        )

        fun parseList(o: JsonObject): List<Txn> = o.arr("transactions").objects().map { parse(it) }
    }
}

/** One purchase day of the list: its rows, what they add to spending (refunds reduce it) and the money in. */
data class TxnDay(val date: String, val items: List<Txn>, val spent: Long, val income: Long)

/** A category of the taxonomy (`GET categories` → `taxonomy`). */
data class TaxCategory(val key: String, val label: String, val labelHe: String)
data class TaxGroup(val key: String, val label: String, val labelHe: String, val income: Boolean, val categories: List<TaxCategory>)

/** A badge in words: its [type] decides the look. */
data class BadgeText(val type: String, val text: String, val title: String)

/** A login's state for people: tone ok | busy | warn | bad. */
data class SourceStatus(val tone: String, val label: String, val detail: String)

/** A login in the add / update form: `GET companies`. */
data class MoneyCompany(val id: String, val name: String, val kind: String, val fields: List<CompanyField>) {
    companion object {
        fun parseAll(o: JsonObject): List<MoneyCompany> = o.arr("companies").objects().map { c ->
            MoneyCompany(
                id = c.string("id"), name = c.string("name"), kind = c.string("kind"),
                fields = c.arr("fields").objects().map { CompanyField(it.string("name"), it.string("label"), it.flag("secret")) }
            )
        }
    }
}

data class CompanyField(val name: String, val label: String, val secret: Boolean)

/** Something that needs the user on a login (the banner): from the overview's `attention` or the sources. */
data class Attention(val sourceId: String, val name: String, val state: String, val reason: String?)

object MoneyLedger {
    const val MINUS = "−"
    private const val FSI = "⁨"
    private const val PDI = "⁩"
    private val INCOME_FLOWS = setOf("income", "transfer_in")
    private val FLOW_CHIPS = mapOf("card_bill" to "Card bill", "own_transfer" to "Between your accounts")
    private val CATEGORY_SOURCES = mapOf(
        "user" to "set by you", "rule" to "your rule for this merchant", "memory" to "learned from earlier charges",
        "model" to "Dana's guess", "bank" to "from the card company"
    )
    /** What a transaction counts as (PATCH {flow}), in the page's words. */
    val FLOW_LABELS = linkedMapOf(
        "purchase" to "spending", "transfer_out" to "a transfer to a person", "investment" to "money set aside",
        "own_transfer" to "my own money (not spending)", "income" to "income", "transfer_in" to "money received", "refund" to "a refund",
        "loan" to "a loan payment", "fee" to "a fee", "cash" to "cash"
    )
    private val OUT_FLOWS = listOf("purchase", "transfer_out", "investment", "own_transfer", "loan", "fee", "cash")
    private val IN_FLOWS = listOf("income", "transfer_in", "refund", "own_transfer")
    private val KIND_LABELS = mapOf("everyday" to "everyday spending", "fixed" to "a fixed cost", "yearly" to "a yearly charge", "one_off" to "a one-off")
    private val KIND_WORDS = mapOf("bank" to "Bank", "card" to "Card", "investment" to "Investments", "meal" to "Meal card")

    fun isolate(s: String?): String = FSI + (s ?: "") + PDI

    // ---------- money ----------

    private fun group3(n: Long): String = String.format(Locale.US, "%,d", n)

    // ---------- dates ----------

    private val MON = listOf("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

    private fun date(iso: String?): LocalDate? = runCatching { LocalDate.parse(iso!!.take(10)) }.getOrNull()
    private fun instant(iso: String?): Instant? = runCatching { OffsetDateTime.parse(iso!!).toInstant() }.getOrNull()

    /** "15 Dec" (+ " 2025" when not in [today]'s year). */
    fun shortDate(iso: String?, today: LocalDate? = LocalDate.now()): String {
        val d = date(iso) ?: return ""
        val year = if (today != null && today.year != d.year) " ${d.year}" else ""
        return "${d.dayOfMonth} ${MON[d.monthValue - 1]}$year"
    }

    /** "Tue 17 Nov 2026". */
    fun longDate(iso: String?): String {
        val d = date(iso) ?: return ""
        return "${weekday(d)} ${d.dayOfMonth} ${MON[d.monthValue - 1]} ${d.year}"
    }

    private fun weekday(d: LocalDate) = d.dayOfWeek.getDisplayName(TextStyle.SHORT, Locale.ENGLISH)

    /** A day header: "Today", "Yesterday", "Mon 16 Nov" (+ year when not this year). */
    fun dayLabel(iso: String, today: LocalDate = LocalDate.now()): String {
        val d = date(iso) ?: return iso
        if (d == today) return "Today"
        if (d == today.minusDays(1)) return "Yesterday"
        val year = if (today.year != d.year) " ${d.year}" else ""
        return "${weekday(d)} ${d.dayOfMonth} ${MON[d.monthValue - 1]}$year"
    }

    /** The local time an aware ISO time was written in ("…T08:10:00+02:00" → "08:10"): no zone shift. */
    fun timeOf(iso: String?): String = Regex("T(\\d{2}):(\\d{2})").find(iso ?: "")?.let { "${it.groupValues[1]}:${it.groupValues[2]}" } ?: ""

    /** "just now", "5 min ago", "2 h ago", "yesterday", "3 days ago", then a date. */
    fun ago(iso: String?, now: Instant = Instant.now(), zone: ZoneId = ZoneId.systemDefault()): String {
        val t = instant(iso) ?: return ""
        val s = max(0L, Duration.between(t, now).seconds)
        return when {
            s < 60 -> "just now"
            s < 3600 -> "${s / 60} min ago"
            s < 86400 -> "${s / 3600} h ago"
            s < 2 * 86400 -> "yesterday"
            s < 30 * 86400 -> "${s / 86400} days ago"
            else -> shortDate(t.atZone(zone).toLocalDate().toString(), now.atZone(zone).toLocalDate())
        }
    }

    /** "in 5 min", "in 3 h", "in 2 days"; "now" once it has come. */
    fun until(iso: String?, now: Instant = Instant.now()): String {
        val t = instant(iso) ?: return ""
        val s = Duration.between(now, t).toMillis() / 1000.0
        return when {
            s <= 30 -> "now"
            s < 3600 -> "in ${max(1L, Math.round(s / 60))} min"
            s < 86400 -> "in ${Math.round(s / 3600)} h"
            else -> "in ${Math.round(s / 86400)} days"
        }
    }

    /** "3:42" to an expiry (never negative). */
    fun countdown(iso: String?, now: Instant = Instant.now()): String {
        val t = instant(iso) ?: return "0:00"
        val s = max(0L, floor(Duration.between(now, t).toMillis() / 1000.0).toLong())
        return "${s / 60}:${(s % 60).toString().padStart(2, '0')}"
    }

    /** Seconds left to an expiry (0 once it passed). */
    fun secondsLeft(iso: String?, now: Instant = Instant.now()): Long =
        instant(iso)?.let { max(0L, Duration.between(now, it).seconds) } ?: 0L

    /** A server instant in the viewer's clock: "07:01" today, "yesterday 19:00", "tomorrow 05:00", "17 Nov 19:00". */
    fun whenText(iso: String?, now: Instant = Instant.now(), zone: ZoneId = ZoneId.systemDefault()): String {
        val t = instant(iso)?.atZone(zone) ?: return ""
        val hm = "%02d:%02d".format(t.hour, t.minute)
        val today = now.atZone(zone).toLocalDate()
        val day = t.toLocalDate()
        return when (day) {
            today -> hm
            today.minusDays(1) -> "yesterday $hm"
            today.plusDays(1) -> "tomorrow $hm"
            else -> "${shortDate(day.toString(), today)} $hm"
        }
    }

    /** Seconds to wait → "45 s" / "7 min". */
    fun waitText(sec: Int): String = if (sec < 60) "${max(1, sec)} s" else "${ceil(sec / 60.0).toInt()} min"

    // ---------- the list ----------

    private fun counts(t: Txn) = t.spending && t.status != "unmatched"
    fun isIncome(t: Txn) = t.flow in INCOME_FLOWS && t.status != "unmatched"

    /** The list grouped by purchase date, in the server's order (newest first). */
    fun groupByDay(txns: List<Txn>): List<TxnDay> {
        val out = mutableListOf<TxnDay>()
        var date: String? = null
        var items = mutableListOf<Txn>()
        var spent = 0L
        var income = 0L
        fun flush() { date?.let { out += TxnDay(it, items, spent, income) } }
        for (t in txns) {
            if (t.purchaseDate != date) {
                flush()
                date = t.purchaseDate; items = mutableListOf(); spent = 0; income = 0
            }
            items += t
            if (counts(t)) spent -= t.amount else if (isIncome(t)) income += t.amount
        }
        flush()
        return out
    }

    /** Appends a page, dropping rows already there (a refetch may overlap). */
    fun merge(have: List<Txn>, page: List<Txn>): List<Txn> {
        val seen = have.mapTo(HashSet()) { it.id }
        return have + page.filter { it.id !in seen }
    }

    /** Rows the "uncategorized" filter shows: card bills and own transfers take no category. */
    fun needsCategory(t: Txn) = t.category == null && t.flow !in FLOW_CHIPS

    /** How the amount reads: "out" (spending), "in" (money in), "muted" (not counted). */
    fun amountTone(t: Txn): String = when {
        t.status == "unmatched" -> "muted"
        t.amount > 0 -> if (t.spending || isIncome(t)) "in" else "muted"
        else -> if (t.spending) "out" else "muted"
    }

    /** The amount as the row shows it: "+₪15,000.00" for money in, "−₪25.00" out. */
    fun amountText(t: Txn): String = MoneyFormat.amount(t.amount, if (amountTone(t) == "in") MoneyFormat.Sign.PLUS else MoneyFormat.Sign.MINUS)

    /** The row's badges. [company] names the card company ("Cal"). */
    fun badges(t: Txn, company: String?): List<BadgeText> {
        val who = company?.takeIf { it.isNotBlank() } ?: "the card company"
        val out = mutableListOf<BadgeText>()
        t.badges.forEach { b ->
            when (b.type) {
                "provisional" -> out += BadgeText("provisional", "Waiting for $who", "From your phone's payment notification. It counts now; $who hasn't listed it yet.")
                "pending" -> out += BadgeText("pending", "Pending", "$who lists it as pending: the amount or date may still change.")
                "unmatched" -> out += BadgeText("unmatched", "Not charged?", "Your phone saw this payment but it never appeared on the card: probably declined or reversed. Not counted.")
                "installment" -> out += BadgeText("installment", "${b.number}/${b.total}", "Payment ${b.number} of ${b.total}")
                "foreign" -> out += BadgeText("foreign", MoneyFormat.foreign(b.amount, b.currency), "Paid in ${b.currency}")
            }
        }
        if (t.amount > 0 && t.flow == "refund") out += BadgeText("refund", "Refund", "Money back: it reduces spending")
        return out
    }

    /** A row's kind as a small badge: One-off, Yearly, Fixed; null for everyday and what isn't spending. */
    fun kindBadge(t: Txn): BadgeText? = when (t.kind) {
        "one_off" -> BadgeText("oneoff", "One-off", "A one-off: left out of everyday spending and the forecast")
        "yearly" -> BadgeText("yearly", "Yearly", "A yearly charge: counted a twelfth a month in fixed costs")
        "fixed" -> BadgeText("fixed", "Fixed", "A regular charge: counted in fixed costs")
        else -> null
    }

    /** "Card bill" / "Between your accounts" instead of a category; null otherwise. */
    fun flowChip(t: Txn): String? = if (t.category == null) FLOW_CHIPS[t.flow] else null

    fun categorySourceLabel(src: String?): String = CATEGORY_SOURCES[src] ?: ""

    /** Why a row is shown the way it is (the detail's explanation), or "". */
    fun statusExplain(t: Txn, company: String?): String {
        val who = company?.takeIf { it.isNotBlank() } ?: "the card company"
        return when {
            t.status == "provisional" -> "Your phone saw this payment${timeOf(t.occurredAt).let { if (it.isNotEmpty()) " at $it" else "" }}. It counts now; when $who lists it, the two become one row."
            t.status == "unmatched" -> "Your phone saw this payment, but $who never listed it within 5 days: probably declined or reversed. It isn't counted."
            t.status == "pending" -> "$who lists it as pending: the amount or date may still change."
            t.flow == "card_bill" -> "The bank's line for a card bill. The card's own transactions are the spending, so this isn't counted twice."
            t.flow == "own_transfer" -> "Money moved between your own accounts: not spending."
            !t.spending && t.amount < 0 && t.category == null -> "Not counted as spending until it has a category."
            else -> ""
        }
    }

    /** The "Counts as" choices: (flow, label) by the amount's sign, the row's own flow kept. */
    fun flowChoices(t: Txn): List<Pair<String, String>> {
        val list = (if (t.amount > 0) IN_FLOWS else OUT_FLOWS).toMutableList()
        if (t.flow != null && t.flow in FLOW_LABELS && t.flow !in list) list += t.flow
        return list.map { f -> f to FLOW_LABELS.getValue(f).replaceFirstChar { it.uppercase() } }
    }

    fun flowLabel(flow: String?): String = FLOW_LABELS[flow] ?: ""

    /** A three-way mark as "yes" | "no" | "auto". */
    fun markState(v: Boolean?): String = when (v) { true -> "yes"; false -> "no"; null -> "auto" }

    /** "Dana counts it as a one-off." (the detail's hint), or "". */
    fun kindText(kind: String?): String = KIND_LABELS[kind]?.let { "Dana counts it as $it." } ?: ""

    /** The Undo line after PATCH {one_off} / {yearly}. */
    fun markText(answer: JsonObject?, mark: String, value: Boolean?): String {
        val t = answer?.obj("transaction")
        val what = if (mark == "yearly") "a yearly charge" else "a one-off"
        val said = when (value) {
            true -> what
            false -> "not $what"
            null -> "Dana decides if it's ${if (mark == "yearly") "yearly" else "a one-off"}"
        }
        val changed = answer?.intValue("changed") ?: 0
        val more = if (changed > 1) " ($changed charges)" else ""
        return "${isolate(t?.str("merchant"))}: $said$more"
    }

    /** The Undo line after PATCH {flow}. */
    fun flowText(answer: JsonObject?): String {
        val t = answer?.obj("transaction")
        return "${isolate(t?.str("merchant"))} now counts as ${flowLabel(t?.str("flow"))}"
    }

    /** The Undo line after PATCH {category} ([label] the new category) or {note} ([label] null). */
    fun undoText(answer: JsonObject?, label: String?, forMerchant: Boolean = false): String {
        val t = answer?.obj("transaction") ?: return "Saved"
        if (label == null) return if (t.str("note") != null) "Note saved" else "Note removed"
        val changed = answer.intValue("changed")
        val more = if (changed > 1) " ($changed charges)" else ""
        return "${isolate(t.str("merchant"))} → $label${if (forMerchant) ", now and from now on$more" else ""}"
    }

    /** An error answer in words ([code] 0 = unreachable). */
    fun errorText(code: Int, error: String?, message: String? = null, retryAfter: Int? = null, reason: String? = null): String = when {
        code == 0 -> "Can't reach Dana. Check the connection and try again."
        error == "too_soon" -> "Synced a moment ago. Try again in ${waitText(retryAfter ?: 60)}."
        error == "needs_attention" -> reason?.takeIf { it.isNotBlank() } ?: "This login needs fixing first."
        error == "bad_code" -> "That doesn't look like the code: 4 to 8 digits."
        error == "not_pending" -> "That code request is over. Dana will ask again when it signs in next."
        error == "not_connected" -> "Money is turned off for you. Turn it on in Integrations."
        error == "not_found" -> "It isn't there any more."
        error == "credentials_key" -> "The server can't store login details right now (its credentials key isn't set). Ask whoever runs Dana."
        !message.isNullOrBlank() -> message
        else -> "Something went wrong (HTTP $code)."
    }

    /** A [MoneyApiError] (or a failure to reach the server) in words. */
    fun errorText(e: Throwable): String = when (e) {
        is MoneyApiError -> errorText(
            e.code, e.error.ifBlank { null }, e.body.str("message"), e.retryAfter, e.body.str("reason")
        )
        is java.io.IOException -> errorText(0, null)
        else -> e.message ?: "Something went wrong."
    }

    // ---------- filters ----------

    /** `GET transactions`' query: the month unless all time, then the filters. */
    fun query(q: String, category: String?, account: String?, min: String, max: String, allTime: Boolean, month: String, limit: Int? = null, cursor: String? = null): Map<String, Any?> =
        linkedMapOf(
            "period" to if (allTime) null else month,
            "q" to q.trim().ifBlank { null },
            "category" to category,
            "account" to account,
            "min" to num(min),
            "max" to num(max),
            "limit" to limit,
            "cursor" to cursor
        )

    /** A typed amount as the API takes it ("100", "49.9"), null when it isn't a number. */
    fun num(v: String): String? {
        val d = v.trim().toDoubleOrNull() ?: return null
        if (d.isNaN() || d.isInfinite()) return null
        return if (d == floor(d) && abs(d) < 1e15) d.toLong().toString() else d.toString()
    }

    /** The amount chip: "₪100 – ₪500", "₪100 or more", "up to ₪49.90"; null without either. */
    fun amountChip(min: String, max: String): String? {
        val a = num(min)?.let { MoneyFormat.major(it.toDouble(), cents = null) }
        val b = num(max)?.let { MoneyFormat.major(it.toDouble(), cents = null) }
        return when {
            a != null && b != null -> "$a – $b"
            a != null -> "$a or more"
            b != null -> "up to $b"
            else -> null
        }
    }

    // ---------- the taxonomy ----------

    fun taxonomy(categories: JsonObject?): List<TaxGroup> =
        categories?.obj("taxonomy")?.arr("groups").objects().map { g ->
            TaxGroup(
                key = g.string("key"), label = g.string("label"), labelHe = g.string("label_he"), income = g.flag("income"),
                categories = g.arr("categories").objects().map { TaxCategory(it.string("key"), it.string("label"), it.string("label_he")) }
            )
        }

    /** A group, a category or "uncategorized" → its label. */
    fun categoryLabel(taxonomy: List<TaxGroup>, key: String?): String {
        if (key.isNullOrBlank()) return ""
        if (key == "uncategorized") return "Uncategorized"
        for (g in taxonomy) {
            if (g.key == key) return g.label
            g.categories.firstOrNull { it.key == key }?.let { return it.label }
        }
        return key
    }

    /** The group a category key belongs to (or the key when it is a group). */
    fun groupOf(taxonomy: List<TaxGroup>, key: String?): String? =
        taxonomy.firstOrNull { g -> g.key == key || g.categories.any { it.key == key } }?.key

    /**
     * The category picker's list: groups with the categories matching [q]
     * (English or Hebrew label, key; a matching group keeps all of its
     * categories); money-in rows get the income group first.
     */
    fun pickerGroups(taxonomy: List<TaxGroup>, q: String, income: Boolean = false): List<TaxGroup> {
        val needle = q.trim().lowercase()
        fun hit(vararg s: String) = needle.isEmpty() || s.any { it.lowercase().contains(needle) }
        val groups = taxonomy.map { g ->
            if (hit(g.key, g.label, g.labelHe)) g else g.copy(categories = g.categories.filter { hit(it.key, it.label, it.labelHe) })
        }.filter { it.categories.isNotEmpty() }
        return groups.filter { it.income == income } + groups.filter { it.income != income }
    }

    // ---------- accounts ----------

    fun kindWord(kind: String?): String = KIND_WORDS[kind] ?: "Card"

    /** "Cal ••8763", "Shared card ••4580" (a label of the user's own), "Hapoalim ••901". */
    fun accountName(kind: String, number: String?, label: String?, companyName: String?): String {
        val digits = (number ?: "").filter { it.isDigit() }
        val short = kind == "bank" || kind == "investment"
        val last = digits.takeLast(if (short) 3 else 4)
        val company = companyName ?: ""
        val custom = if (!label.isNullOrBlank() && !(company.isNotEmpty() && label.startsWith(company))) label else ""
        val name = custom.ifEmpty { (if (kind == "bank") company.removePrefix("Bank ").trimStart() else company).ifEmpty { kindWord(kind) } }
        return if (last.isNotEmpty()) "$name ••$last" else name
    }

    /** A login's state for people. */
    fun sourceStatus(state: String, stateReason: String?, lastOkAt: String?, canSync: Boolean, now: Instant = Instant.now()): SourceStatus {
        val lastOk = if (lastOkAt != null) "last good sync ${ago(lastOkAt, now)}" else ""
        return when (state) {
            "new" -> SourceStatus("busy", "Starting the first sync…", "The first sync reads a year back and takes a few minutes.")
            "syncing" -> SourceStatus("busy", "Syncing…", if (lastOkAt != null) "Last good sync ${ago(lastOkAt, now)}" else "The first sync reads a year back and takes a few minutes.")
            "needs_code" -> SourceStatus("warn", "Waiting for the SMS code", stateReason ?: "")
            "needs_attention" -> SourceStatus("bad", if (canSync) "Stopped: sign in again yourself" else "Login needs fixing", stateReason ?: "")
            "failing" -> SourceStatus("warn", "Last sync failed", listOfNotNull(stateReason, lastOk.ifEmpty { null }).joinToString(" · "))
            else -> SourceStatus("ok", if (lastOkAt != null) "Synced ${ago(lastOkAt, now)}" else "Synced", "")
        }
    }

    /** The overview's `attention` list (falls back to the sources' states when the overview isn't there). */
    fun attention(overview: JsonObject?): List<Attention>? = overview?.arr("attention")?.objects()?.map {
        Attention(it.string("source_id"), it.string("name"), it.string("state"), it.str("reason"))
    }

}
