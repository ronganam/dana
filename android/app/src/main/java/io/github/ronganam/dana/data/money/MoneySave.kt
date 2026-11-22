package io.github.ronganam.dana.data.money

import io.github.ronganam.dana.data.money.MoneyLedger.isolate
import io.github.ronganam.dana.data.money.MoneyLedger.shortDate
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.YearMonth
import java.time.ZoneId
import java.util.Locale
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * The Save view's model and words (server docs/money.md → Savings; the web's
 * `money-core.js` savings part): `GET /api/money/savings` parsed, the two
 * parts and their sections, the chips, the actions of each status and their
 * PATCH bodies, the Undo lines, price checks (states, answers, offers),
 * targets and outcomes. Only formatting: every number is the server's.
 * Tested in `MoneySaveTest` against `server/tests/fixtures/money_savings_web.json`.
 */

data class Saving(val low: Long, val high: Long, val basis: String)
data class Effort(val minutes: Int, val how: String)
data class Timing(val from: String?, val until: String?, val label: String?)
data class SeriesPoint(val date: String, val amount: Long)
data class EvidenceLink(val view: String, val q: String?, val category: String?)
data class Evidence(val lines: List<String>, val series: List<SeriesPoint>, val link: EvidenceLink?)
data class Step(val text: String, val url: String?)
data class Script(val lang: String, val text: String)
/** An answer option: [value] as the server sent it (a string or a number), sent back unchanged. */
data class QOption(val value: Any, val label: String)
data class Question(val id: String, val text: String, val options: List<QOption>, val answer: Any?) {
    fun isAnswer(o: QOption) = answer != null && answer.toString() == o.value.toString()
}
data class SourceLink(val title: String, val url: String)
data class Market(
    val topic: String,
    val state: String,
    val checkedAt: String?,
    val stale: Boolean,
    val items: List<JsonObject>,
    val sources: List<SourceLink>,
    val error: String?,
    val needsAnswer: String?
)
data class SubItem(val key: String, val merchant: String, val amount: Long, val cadence: String?, val yearly: Long?, val nextDate: String?, val choice: String?, val active: Boolean)
data class TargetMonth(val month: String, val spent: Long, val target: Long, val under: Boolean)
data class Target(val monthly: Long, val thisMonth: Long, val onTrack: Boolean?, val daysLeft: Int?, val months: List<TargetMonth>, val saved: Long)
data class Outcome(val state: String, val saved: Long, val savedYearly: Long, val detail: String)

/** A way to save (`group` save) or spending to watch (`watch`). */
data class SavingItem(
    val key: String,
    val type: String,
    val group: String,
    val status: String,
    val statusAt: String?,
    val snoozeUntil: String?,
    val isNew: Boolean,
    val active: Boolean,
    val title: String,
    val summary: String,
    val saving: Saving?,
    val atStake: Long?,
    val effort: Effort?,
    val timing: Timing?,
    val evidence: Evidence,
    val math: List<String>,
    val steps: List<Step>,
    val script: Script?,
    val questions: List<Question>,
    val market: Market?,
    val subitems: List<SubItem>,
    val target: Target?,
    val suggestedTarget: Long?,
    val outcome: Outcome?
) {
    val watch: Boolean get() = group == "watch"

    companion object {
        fun parse(o: JsonObject): SavingItem {
            val ev = o.obj("evidence")
            return SavingItem(
                key = o.string("key"),
                type = o.string("type"),
                group = o.string("group"),
                status = o.string("status"),
                statusAt = o.str("status_at"),
                snoozeUntil = o.str("snooze_until"),
                isNew = o.flag("new"),
                active = o.flag("active", true),
                title = o.string("title"),
                summary = o.str("summary") ?: "",
                saving = o.obj("saving")?.let { Saving(it.longValue("low"), it.longValue("high"), it.string("basis")) },
                atStake = o.long("at_stake"),
                effort = o.obj("effort")?.let { Effort(it.intValue("minutes"), it.string("how")) },
                timing = o.obj("timing")?.let { Timing(it.str("from"), it.str("until"), it.str("label")) },
                evidence = Evidence(
                    lines = ev?.arr("lines").strings(),
                    series = ev?.arr("series").objects().mapNotNull { p ->
                        val d = p.str("date") ?: return@mapNotNull null
                        if (p.absent("amount")) null else SeriesPoint(d, p.longValue("amount"))
                    },
                    link = ev?.obj("link")?.let { l -> l.str("view")?.let { EvidenceLink(it, l.str("q"), l.str("category")) } }
                ),
                math = o.arr("math").strings(),
                steps = o.arr("steps").objects().map { Step(it.string("text"), it.str("url")) },
                script = o.obj("script")?.let { s -> s.str("text")?.let { Script(s.str("lang") ?: "he", it) } },
                questions = o.arr("questions").objects().map { parseQuestion(it) },
                market = o.obj("market")?.let { m ->
                    Market(
                        topic = m.string("topic"),
                        state = m.str("state") ?: "none",
                        checkedAt = m.str("checked_at"),
                        stale = m.flag("stale"),
                        items = m.arr("items").objects(),
                        sources = m.arr("sources").objects().mapNotNull { MoneySave.safeSource(it) },
                        error = m.str("error"),
                        needsAnswer = m.str("needs_answer")
                    )
                },
                subitems = o.arr("subitems").objects().map {
                    SubItem(
                        key = it.string("key"), merchant = it.string("merchant"), amount = it.longValue("amount"),
                        cadence = it.str("cadence"), yearly = it.long("yearly"), nextDate = it.str("next_date"),
                        choice = it.str("choice"), active = it.flag("active", true)
                    )
                },
                target = o.obj("target")?.let { t ->
                    Target(
                        monthly = t.longValue("monthly"), thisMonth = t.longValue("this_month"), onTrack = t.bool("on_track"),
                        daysLeft = if (t.absent("days_left")) null else t.intValue("days_left"),
                        months = t.arr("months").objects().map { TargetMonth(it.string("month"), it.longValue("spent"), it.longValue("target"), it.flag("under")) },
                        saved = t.longValue("saved")
                    )
                },
                suggestedTarget = o.long("suggested_target"),
                outcome = o.obj("outcome")?.let { Outcome(it.string("state"), it.longValue("saved"), it.longValue("saved_yearly"), it.str("detail") ?: "") }
            )
        }

        fun parseQuestion(q: JsonObject) = Question(
            id = q.string("id"),
            text = q.string("text"),
            options = q.arr("options").objects().map { QOption(it.anyValue("value") ?: "", it.string("label")) },
            answer = q.anyValue("answer")
        )
    }
}

data class SaveTotals(val low: Long, val high: Long, val count: Int, val saved: Long, val savedYearly: Long)
data class SaveRate(val policy: Double?, val date: String?, val bank: String?)

/** `GET /api/money/savings`. */
data class SavingsOverview(val asOf: String?, val rate: SaveRate?, val totals: SaveTotals, val items: List<SavingItem>) {
    fun item(key: String) = items.firstOrNull { it.key == key }
    fun with(item: SavingItem) = copy(items = items.map { if (it.key == item.key) item else it })

    companion object {
        fun parse(o: JsonObject): SavingsOverview {
            val t = o.obj("totals") ?: EmptyJson
            return SavingsOverview(
                asOf = o.str("as_of"),
                rate = o.obj("rate")?.let { SaveRate(if (it.absent("policy")) null else it.doubleValue("policy"), it.str("date"), it.str("bank")) },
                totals = SaveTotals(t.longValue("low"), t.longValue("high"), t.intValue("count"), t.longValue("saved"), t.longValue("saved_yearly")),
                items = o.arr("items").objects().map { SavingItem.parse(it) }
            )
        }
    }
}

data class SaveSection(val key: String, val label: String, val items: List<SavingItem>)
data class SavePart(val group: String, val label: String, val count: Int, val sections: List<SaveSection>)
data class SaveTiles(val could: String, val couldSub: String, val saved: Long, val savedSub: String, val source: String)
data class Chip(val kind: String, val text: String)
/** A button of an item's status: [body] the PATCH (null for [input]: the target field). */
data class SaveAction(val key: String, val label: String, val body: JsonObject?, val primary: Boolean = false, val input: Boolean = false)
data class LinkTarget(val view: String, val q: String, val category: String?, val label: String)
data class Spark(val points: List<Pair<Float, Float>>, val base: Float, val range: String, val count: Int)
data class MarketView(val state: String, val text: String, val button: String?, val offers: Boolean, val question: String?, val checked: String, val stale: String)
data class CheckReply(val kind: String, val text: String, val question: Question? = null)
data class OfferCell(val label: String, val text: String, val sub: String = "", val strong: Boolean = false, val muted: Boolean = false)
data class OfferRow(val name: String, val plan: String, val cells: List<OfferCell>, val source: SourceLink?)
data class OfferTable(val topic: String, val columns: List<String>, val hasPlan: Boolean, val rows: List<OfferRow>, val sources: List<SourceLink>)
data class OutcomeText(val tone: String, val title: String, val sub: String, val detail: String)
data class MonthText(val month: String, val label: String, val text: String, val under: Boolean)
data class TargetProgress(val bar: Int, val over: Boolean, val text: String, val track: String, val tone: String, val left: String, val months: List<MonthText>, val saved: String)
data class SubItemText(val amount: String, val yearly: String, val next: String)

object MoneySave {
    private val PARTS = listOf("save" to "Ways to save", "watch" to "Spending to watch")
    private val SECTIONS = listOf("now" to "Now", "coming" to "Coming up", "doing" to "In progress", "watching" to "Watching", "done" to "Done", "choices" to "Your choices")
    private val AT_STAKE_ONCE = setOf("spree")
    private val BASIS = mapOf("measured" to "measured", "increase" to "price rise", "market" to "market")
    private val FX_KINDS = mapOf("card" to "Card", "wallet" to "Wallet", "bank" to "Bank account")
    private val CONFIRM_ON = mapOf("telecom" to "the provider's site", "electricity" to "the supplier's site", "fx_card" to "the issuer's site", "arnona" to "the municipality's site")
    private val PER = mapOf("weekly" to "/ week", "monthly" to "/ month", "bimonthly" to "/ 2 months", "yearly" to "/ year")
    private val HEB = Regex("[\\u0590-\\u05FF\\uFB1D-\\uFB4F](?:[\\u0590-\\u05FF\\uFB1D-\\uFB4F\\d\\s'\"׳״.,&\\-–]*[\\u0590-\\u05FF\\uFB1D-\\uFB4F])?")

    private fun plural(n: Int, word: String) = "$n $word${if (n == 1) "" else "s"}"

    /** Hebrew runs isolated inside a server-written English sentence, so an amount beside them keeps its place. */
    fun isolateHebrew(text: String): String = HEB.replace(text) { "⁨${it.value}⁩" }

    /** The viewer's local day of an aware ISO time. */
    fun localDay(iso: String?, zone: ZoneId = ZoneId.systemDefault()): String? =
        runCatching { OffsetDateTime.parse(iso!!).atZoneSameInstant(zone).toLocalDate().toString() }.getOrNull()

    /** "example.com" from an http(s) link (no "www."), "" when it isn't one. */
    fun hostOf(url: String?): String =
        Regex("^https?://([^/?#:]+)", RegexOption.IGNORE_CASE).find(url ?: "")?.groupValues?.get(1)?.lowercase()?.removePrefix("www.") ?: ""

    /** A link the page may open: http(s) only. */
    fun safeSource(o: JsonObject?): SourceLink? {
        val url = o?.str("url") ?: return null
        val host = hostOf(url)
        if (host.isEmpty()) return null
        return SourceLink(o.str("title") ?: host, url)
    }

    // ---------- sections and tiles ----------

    /** An open item by its timing (Coming up while timing.from is ahead, else Now), the rest by status; any other status is a choice. */
    fun section(item: SavingItem, today: String?): String = when (item.status) {
        "open" -> if (item.timing?.from != null && today != null && item.timing.from > today) "coming" else "now"
        "doing" -> "doing"
        "watching" -> "watching"
        "done" -> "done"
        else -> "choices"
    }

    /** Ways to save and Spending to watch, each with its non-empty sections in order and the server's order inside. */
    fun parts(items: List<SavingItem>, today: String?): List<SavePart> = PARTS.map { (group, label) ->
        val mine = items.filter { (if (it.watch) "watch" else "save") == group }
        val sections = SECTIONS.map { (key, l) -> SaveSection(key, l, mine.filter { section(it, today) == key }) }.filter { it.items.isNotEmpty() }
        SavePart(group, label, mine.size, sections)
    }

    fun tiles(sv: SavingsOverview, today: LocalDate?): SaveTiles {
        val t = sv.totals
        val lo = min(t.low, t.high)
        val hi = max(t.low, t.high)
        val r = sv.rate
        return SaveTiles(
            could = if (MoneyFormat.whole(lo) == MoneyFormat.whole(hi)) MoneyFormat.whole(hi) else "${MoneyFormat.whole(lo)}–${MoneyFormat.whole(hi)}",
            couldSub = if (t.count > 0) "a year · ${plural(t.count, "idea")}" else "No open idea with a saving right now",
            saved = t.saved,
            savedSub = when {
                t.savedYearly != 0L -> "≈ ${MoneyFormat.whole(t.savedYearly)} a year"
                t.saved != 0L -> ""
                else -> "Counted once something you did shows in your charges"
            },
            source = if (r?.policy != null) "${r.bank ?: "Central bank"} rate ${pctText(r.policy, signed = false)}${r.date?.let { " (${shortDate(it, today)})" } ?: ""} · amounts from your charges"
            else "Amounts from your charges"
        )
    }

    /** The card's money chip: "+₪476/yr" / "+₪768–₪888/yr", else "₪3,520/yr at stake" (a spree: "₪615 spent"). */
    fun savingChip(item: SavingItem): Chip? {
        val s = item.saving
        if (s != null && (s.low != 0L || s.high != 0L)) {
            val lo = min(s.low, s.high)
            val hi = max(s.low, s.high)
            return Chip("save", if (MoneyFormat.whole(lo) == MoneyFormat.whole(hi)) "+${MoneyFormat.whole(hi)}/yr" else "+${MoneyFormat.whole(lo)}–${MoneyFormat.whole(hi)}/yr")
        }
        val stake = item.atStake ?: return null
        if (stake == 0L) return null
        return if (item.type in AT_STAKE_ONCE) Chip("stake", "${MoneyFormat.whole(stake)} spent") else Chip("stake", "${MoneyFormat.whole(stake)}/yr at stake")
    }

    private fun minutesText(m: Int): String = if (m < 60) "$m min" else {
        val h = m / 60.0
        if (m % 60 == 0) "${m / 60} h" else String.format(Locale.US, "%.1f h", h)
    }

    /** "10-min call", "2 min in the app", "15 min online", "30 min comparing", "10 min to review"; "" without one. */
    fun effortText(effort: Effort?): String {
        if (effort == null || effort.minutes <= 0) return ""
        val m = effort.minutes
        val t = minutesText(m)
        return when (effort.how) {
            "call" -> if (m < 60) "$m-min call" else "$t call"
            "app" -> "$t in the app"
            "online" -> "$t online"
            "compare" -> "$t comparing"
            "review" -> "$t to review"
            else -> t
        }
    }

    fun basisChip(item: SavingItem): String? = item.saving?.let { BASIS[it.basis] }

    /** "On it since 18 Nov", "Done 18 Nov", "Not for you · 18 Nov", "Worth it to you · 18 Nov", "Snoozed until 18 Dec", "Watching since 10 Sep"; "" when open. */
    fun statusLine(item: SavingItem, today: LocalDate?, zone: ZoneId = ZoneId.systemDefault()): String {
        val on = localDay(item.statusAt, zone)?.let { shortDate(it, today) } ?: ""
        return when (item.status) {
            "doing" -> if (on.isNotEmpty()) "On it since $on" else "On it"
            "done" -> if (on.isNotEmpty()) "Done $on" else "Done"
            "dismissed" -> if (on.isNotEmpty()) "Not for you · $on" else "Not for you"
            "accepted" -> if (on.isNotEmpty()) "Worth it to you · $on" else "Worth it to you"
            "snoozed" -> item.snoozeUntil?.let { "Snoozed until ${shortDate(it, today)}" } ?: "Snoozed"
            "watching" -> if (on.isNotEmpty()) "Watching since $on" else "Watching"
            "open" -> ""
            else -> item.status
        }
    }

    private fun body(vararg kv: Pair<String, Any?>) = buildJsonObject { kv.forEach { (k, v) -> put(k, jsonValue(v)) } }

    /** The buttons of an item's status; every status has a way back (`{status: "open"}` gives it back to Dana). */
    fun actions(item: SavingItem): List<SaveAction> = when (item.status) {
        "open" -> if (item.watch) listOf(
            SaveAction("target", "Cut back", null, primary = true, input = true),
            SaveAction("accept", "Worth it", body("status" to "accepted"))
        ) else listOf(
            SaveAction("doing", "I'm on it", body("status" to "doing"), primary = true),
            SaveAction("done", "I did it", body("status" to "done")),
            SaveAction("snooze", "Snooze a month", body("status" to "snoozed", "snooze_days" to 30)),
            SaveAction("dismiss", "Not for me", body("status" to "dismissed"))
        )
        "doing" -> listOf(SaveAction("done", "I did it", body("status" to "done"), primary = true), SaveAction("open", "Not doing it", body("status" to "open")))
        "done" -> listOf(SaveAction("open", "Not done", body("status" to "open")))
        "dismissed", "accepted" -> listOf(SaveAction("open", "Flag again", body("status" to "open")))
        "snoozed" -> listOf(SaveAction("open", "Unsnooze", body("status" to "open")))
        "watching" -> listOf(SaveAction("target", "Edit target", null, input = true), SaveAction("stop", "Stop tracking", body("target" to null)))
        else -> listOf(SaveAction("open", "Dana decides again", body("status" to "open")))
    }

    /** The Undo line after a PATCH: what changed, then the item ([item] is the answer's). */
    fun toast(item: SavingItem?, body: JsonObject, today: LocalDate? = LocalDate.now()): String {
        val name = isolateHebrew(item?.title ?: "")
        body.obj("subitem")?.let { sub ->
            val key = sub.string("key")
            val line = item?.subitems?.firstOrNull { it.key == key }
            val choice = sub.str("choice")
            val said = when (choice) { "keep" -> "keeping it"; "cancel" -> "marked to cancel"; else -> "no choice" }
            return "${isolate(line?.merchant ?: key)}: $said"
        }
        if ("answers" in body) return "Answer saved · $name"
        if ("target" in body) return if (body.absent("target")) "Stopped tracking · $name" else "Target ${MoneyFormat.whole(body.longValue("target"))} a month · $name"
        val words = when (body.str("status")) {
            "doing" -> "On it"
            "done" -> "Done"
            "dismissed" -> "Not for you"
            "accepted" -> "Worth it"
            "open" -> "Dana decides again"
            "snoozed" -> item?.snoozeUntil?.let { "Snoozed until ${shortDate(it, today)}" } ?: "Snoozed"
            else -> "Saved"
        }
        return "$words · $name"
    }

    /** Whole units typed for a monthly target ("650", "₪1,200") → minor units; null unless 1…1,000,000. */
    fun targetAgorot(text: String?): Long? {
        val s = (text ?: "").replace(MoneyFormat.symbol, "").replace(Regex("[,\\s]"), "")
        if (!Regex("^\\d{1,7}$").matches(s)) return null
        val n = s.toLong()
        return if (n in 1..1_000_000) n * 100 else null
    }

    /** The target field's starting text: the target, else the suggested one, in whole units. */
    fun targetDraft(item: SavingItem): String {
        val a = item.target?.monthly ?: item.suggestedTarget ?: 0L
        val n = Math.round(a / 100.0)
        return if (n > 0) n.toString() else ""
    }

    private val VIEW_LABELS = mapOf(
        "overview" to "Overview", "spending" to "Spending", "cashflow" to "Cash flow", "save" to "Save",
        "investments" to "Investments", "meals" to "Meal card", "transactions" to "Transactions"
    )

    /** What the evidence link opens: Transactions over all time with a search or category, or a named view. */
    fun link(l: EvidenceLink?): LinkTarget? {
        if (l == null) return null
        if (l.view == "transactions") {
            val q = l.q?.trim() ?: ""
            val cat = l.category?.ifBlank { null }
            return LinkTarget("transactions", q, cat, if (q.isNotEmpty() || cat != null) "See these charges" else "Open Transactions")
        }
        return VIEW_LABELS[l.view]?.let { LinkTarget(l.view, "", null, "Open $it") }
    }

    private fun dayNum(iso: String): Long = LocalDate.parse(iso.take(10)).toEpochDay()

    /** The evidence sparkline in a [w] × [h] box: a line by date from zero; null under 2 points. */
    fun spark(series: List<SeriesPoint>, w: Float = 160f, h: Float = 40f, pad: Float = 3f, today: LocalDate? = LocalDate.now()): Spark? {
        val pts = series.filter { runCatching { LocalDate.parse(it.date.take(10)) }.isSuccess }
        if (pts.size < 2) return null
        val d0 = dayNum(pts.first().date)
        val span = max(1L, dayNum(pts.last().date) - d0).toFloat()
        val lo = min(0L, pts.minOf { it.amount }).toFloat()
        val hi = pts.maxOf { it.amount }.toFloat()
        val top = if (hi > lo) hi else lo + 1
        fun x(iso: String) = r1(pad + ((dayNum(iso) - d0) / span) * (w - 2 * pad))
        fun y(v: Float) = r1(h - pad - ((v - lo) / (top - lo)) * (h - 2 * pad))
        return Spark(
            points = pts.map { x(it.date) to y(it.amount.toFloat()) },
            base = y(max(lo, 0f)),
            range = "${shortDate(pts.first().date, today)} – ${shortDate(pts.last().date, today)}",
            count = pts.size
        )
    }

    private fun r1(v: Float) = (v * 10).roundToInt() / 10f

    // ---------- price checks ----------

    /** The Prices block's state in words; null without a price check. */
    fun marketView(m: Market?, today: LocalDate?, zone: ZoneId = ZoneId.systemDefault()): MarketView? {
        if (m == null) return null
        val offers = m.items.isNotEmpty()
        val checked = m.checkedAt?.let { "Checked ${shortDate(localDay(it, zone), today)} with a web search · confirm on ${CONFIRM_ON[m.topic] ?: "the provider's site"}" } ?: ""
        val stale = if (offers && m.stale) "prices may have changed" else ""
        val q = m.needsAnswer
        return when (m.state) {
            "checking" -> MarketView("checking", "Checking prices… this can take a minute or two.", null, offers, q, checked, stale)
            "ok" -> MarketView("ok", "", "Check again", offers, q, checked, stale)
            "failed" -> MarketView(
                "failed", m.error?.let { "The last check didn't work: $it." } ?: "The last check didn't work.",
                if (offers) "Check again" else "Try again", offers, q, checked, stale
            )
            "unavailable" -> MarketView("unavailable", "Price checks aren't available right now.", null, offers, q, checked, stale)
            else -> if (q != null) MarketView(m.state, "To check prices, answer this first:", null, offers, q, checked, stale)
            else MarketView(m.state, "See what others charge today. Dana searches the web only when you press the button.", "Check prices", offers, q, checked, stale)
        }
    }

    /** `POST …/check`'s answer: checking (202, or 409: one already runs) | question | wait (429) | error. */
    fun checkReply(code: Int, body: JsonObject?): CheckReply {
        val b = body ?: EmptyJson
        val error = b.str("error")
        return when {
            code == 202 || code == 409 -> CheckReply("checking", "")
            code == 400 && error == "needs_answer" -> CheckReply("question", "Answer this first, then check prices.", b.obj("question")?.let { SavingItem.parseQuestion(it) })
            code == 429 -> {
                val s = max(60, if (b.absent("retry_after")) 0 else b.intValue("retry_after"))
                CheckReply("wait", "Prices were checked a short while ago. Try again in ${if (s < 3600) "${ceil(s / 60.0).toInt()} min" else "${ceil(s / 3600.0).toInt()} h"}.")
            }
            code == 503 -> CheckReply("error", "Price checks aren't available right now.")
            code == 400 && error == "no_check" -> CheckReply("error", "This idea has no price check.")
            else -> CheckReply("error", MoneyLedger.errorText(code, error, b.str("message"), null, b.str("reason")))
        }
    }

    /** A percentage: "+6.98%", "−3.62%", "0%" (two decimals under 10, one above; [digits] fewer); [signed] false drops the plus. */
    fun pctText(p: Double?, signed: Boolean = true, digits: Int = 2): String {
        if (p == null || p.isNaN() || p.isInfinite()) return ""
        val a = abs(p)
        val d = min(digits, if (a >= 10) 1 else 2)
        val fixed = String.format(Locale.US, "%.${d}f", a)
        if (fixed.toDouble() == 0.0) return "0%"
        val body = (if (fixed.contains('.')) fixed.trimEnd('0').trimEnd('.') else fixed) + "%"
        return (if (p < 0) MoneyLedger.MINUS else if (signed) "+" else "") + body
    }

    /** A price check's offers as rows; a column empty in every row is left out. */
    fun offerRows(m: Market?, today: LocalDate?): OfferTable? {
        val t = offerTable(m, today) ?: return null
        if (t.rows.isEmpty()) return t
        val n = t.rows.first().cells.size
        val keep = (0 until n).map { i -> t.rows.any { r -> r.cells.getOrNull(i)?.text?.isNotEmpty() == true } }
        val first = if (t.hasPlan) 2 else 1
        val columns = t.columns.filterIndexed { i, _ -> i < first || i >= t.columns.size - 1 || keep[i - first] }
        return t.copy(columns = columns, rows = t.rows.map { r -> r.copy(cells = r.cells.filterIndexed { i, _ -> keep[i] }) })
    }

    private fun offerTable(m: Market?, today: LocalDate?): OfferTable? {
        if (m == null) return null
        fun mo(v: Long) = "${MoneyFormat.amount(v)} a month"
        fun pctOff(p: Double?) = "${pctText(p, signed = false, digits = 1)} off"
        fun row(name: String?, plan: String?, cells: List<OfferCell>, it: JsonObject) = OfferRow(name ?: "", plan ?: "", cells, safeSource(it.obj("source")))
        fun d(o: JsonObject, k: String): Double? = if (o.absent(k)) null else o.doubleValue(k)
        val items = m.items
        return when (m.topic) {
            "telecom" -> OfferTable(m.topic, listOf("Provider", "Plan", "12-month average", "After the promo", "Conditions", "Source"), true, items.map { it ->
                val promo = it.long("promo_price")
                val months = if (it.absent("promo_months")) 0 else it.intValue("promo_months")
                row(it.str("provider"), it.str("plan"), listOf(
                    OfferCell("12-month average", mo(it.longValue("avg_12")), strong = true),
                    if (promo != null && months > 0) OfferCell("After the promo", "${mo(it.longValue("price"))} after ${plural(months, "month")}", sub = "${mo(promo)} until then")
                    else OfferCell("After the promo", "No promo", sub = mo(it.longValue("price")), muted = true),
                    OfferCell("Conditions", it.str("conditions") ?: "")
                ), it)
            }, m.sources)
            "electricity" -> OfferTable(m.topic, listOf("Supplier", "Plan", "Discount", "Hours", "Meter", "Source"), true, items.map {
                row(it.str("supplier"), it.str("plan"), listOf(
                    OfferCell("Discount", pctOff(d(it, "discount_pct")), strong = true),
                    OfferCell("Hours", listOfNotNull(it.str("hours"), it.str("days")).joinToString(" · ")),
                    OfferCell("Meter", if (it.flag("smart_meter")) "Smart meter needed" else "Any meter")
                ), it)
            }, m.sources)
            "fx_card" -> OfferTable(m.topic, listOf("Card or wallet", "Kind", "Conversion fee", "Monthly fee", "Conditions", "Source"), false, items.map {
                val fee = it.long("monthly_fee") ?: 0L
                row(it.str("name"), "", listOf(
                    OfferCell("Kind", FX_KINDS[it.str("kind")] ?: ""),
                    OfferCell("Conversion fee", pctText(d(it, "fx_fee_pct"), signed = false), strong = true),
                    OfferCell("Monthly fee", if (fee != 0L) mo(fee) else "None"),
                    OfferCell("Conditions", it.str("conditions") ?: "")
                ), it)
            }, m.sources)
            "arnona" -> OfferTable(m.topic, listOf("Result", "Discount", "Deadline", "How", "Source"), false, items.map {
                val found = it.flag("found")
                val pct = d(it, "discount_pct")
                row(if (found) "A discount for paying the year at once" else "No discount found for paying at once", "", listOf(
                    OfferCell("Discount", if (found && pct != null) pctOff(pct) else "—", strong = found),
                    OfferCell("Deadline", it.str("deadline")?.let { dl -> shortDate(dl, today) } ?: "—"),
                    OfferCell("How", it.str("method") ?: "")
                ), it)
            }, m.sources)
            else -> OfferTable(m.topic, listOf("Offer", "Source"), false, items.map {
                row(it.str("name") ?: it.str("provider") ?: it.str("supplier"), "", emptyList(), it)
            }, m.sources)
        }
    }

    // ---------- outcomes, targets, lines ----------

    fun outcomeText(o: Outcome?): OutcomeText? {
        if (o == null) return null
        return when (o.state) {
            "confirmed" -> OutcomeText("good", if (o.saved != 0L) "Saved ${MoneyFormat.whole(o.saved)} so far" else "It worked", if (o.savedYearly != 0L) "≈ ${MoneyFormat.whole(o.savedYearly)} a year" else "", o.detail)
            "no_change" -> OutcomeText("warn", "No change in your charges yet", "", o.detail)
            else -> OutcomeText("wait", "Waiting to see it in your charges", "", o.detail)
        }
    }

    fun targetProgress(t: Target?): TargetProgress? {
        if (t == null) return null
        val over = t.monthly > 0 && t.thisMonth > t.monthly
        return TargetProgress(
            bar = if (t.monthly > 0) min(100, Math.round(t.thisMonth.toDouble() / t.monthly * 100).toInt()) else 0,
            over = over,
            text = "${MoneyFormat.whole(t.thisMonth)} of ${MoneyFormat.whole(t.monthly)} this month",
            track = when { over -> "Over the target"; t.onTrack == true -> "On track"; t.onTrack == false -> "Off track"; else -> "" },
            tone = when { over || t.onTrack == false -> "bad"; t.onTrack == true -> "good"; else -> "" },
            left = t.daysLeft?.let { "${plural(it, "day")} left" } ?: "",
            months = t.months.map { m ->
                val label = runCatching { YearMonth.parse(m.month).month.getDisplayName(java.time.format.TextStyle.SHORT, Locale.ENGLISH) }.getOrDefault(m.month)
                MonthText(m.month, label, "${MoneyFormat.whole(m.spent)} of ${MoneyFormat.whole(m.target)}", m.under)
            },
            saved = if (t.saved != 0L) "${MoneyFormat.whole(t.saved)} saved so far" else ""
        )
    }

    fun subitemText(s: SubItem, today: LocalDate?): SubItemText = SubItemText(
        amount = "${MoneyFormat.amount(s.amount, MoneyFormat.Sign.NONE)} ${PER[s.cadence] ?: ""}".trim(),
        yearly = s.yearly?.takeIf { it != 0L }?.let { "${MoneyFormat.whole(it)} a year" } ?: "",
        next = when {
            !s.active -> if (s.choice == "cancel") "No charge since you cancelled" else "Not charged lately"
            s.nextDate != null -> "next ${shortDate(s.nextDate, today)}"
            else -> ""
        }
    )
}
