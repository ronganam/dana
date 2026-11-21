package io.github.ronganam.dana.data.money

import org.json.JSONArray
import org.json.JSONObject

// The payloads of the Money dashboards (server docs/money.md → "Page API and
// insights", "Dashboards", "Investment accounts", "Meal cards"; the routes in
// integrations/money/api.py). Amounts are agorot, dates ISO `YYYY-MM-DD`.
// Each parser reads one route's answer and never throws on a missing field.

internal fun JSONObject.str(key: String): String? = if (!has(key) || isNull(key)) null else optString(key).ifBlank { null }

/** A number that may come as an int or a float (medians): rounded to agorot. */
internal fun JSONObject.long(key: String): Long? = when (val v = if (has(key)) opt(key) else null) {
    is Number -> Math.round(v.toDouble())
    else -> null
}

internal fun JSONObject.dbl(key: String): Double? = when (val v = if (has(key)) opt(key) else null) {
    is Number -> v.toDouble()
    else -> null
}

internal fun JSONObject.bool(key: String): Boolean? = if (!has(key) || isNull(key)) null else optBoolean(key)

internal inline fun <T : Any> JSONArray?.objects(f: (JSONObject) -> T?): List<T> =
    if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it)?.let(f) }

/** `[1200, null, 3400]` → longs with nulls kept; null when the key is missing or null. */
internal fun JSONObject.longs(key: String): List<Long?>? {
    val a = (if (has(key) && !isNull(key)) optJSONArray(key) else null) ?: return null
    return (0 until a.length()).map { i -> (a.opt(i) as? Number)?.let { Math.round(it.toDouble()) } }
}

// ---------------------------------------------------------------- overview

data class DatedBalance(val date: String, val balance: Long, val days: Int? = null)

internal fun datedBalance(o: JSONObject?): DatedBalance? =
    o?.str("date")?.let { DatedBalance(it, o.long("balance") ?: 0, o.long("days")?.toInt()) }

/** `overview.forecast`: the tile's part (the month in progress only). */
data class ForecastTile(
    val balance: Long,
    val asOf: String,
    val soonLow: DatedBalance?,
    val lowPoint: DatedBalance?,
    val end: String?,
    val endBalance: Long?
)

data class Everyday(val total: Long, val onTrack: Long?, val typical: Long?)

internal fun everyday(o: JSONObject?) = Everyday(o?.long("total") ?: 0, o?.long("on_track"), o?.long("typical"))

/** `GET overview?month=`: what the dashboards read of it (the tiles). */
data class MoneyOverview(
    val month: String,
    val inProgress: Boolean,
    val incomeMonth: Long,
    val incomeTypical: Long?,
    val net: Long,
    val everyday: Everyday,
    val oneOff: Long,
    val setAside: Long,
    val fixedMonthly: Long,
    val yearlyMonthly: Long,
    val uncategorized: Int,
    val forecast: ForecastTile?
) {
    companion object {
        fun parse(o: JSONObject): MoneyOverview {
            val f = o.optJSONObject("forecast")
            return MoneyOverview(
                month = o.optString("month"),
                inProgress = o.optJSONObject("period")?.optBoolean("in_progress") == true,
                incomeMonth = o.long("income_month") ?: 0,
                incomeTypical = o.long("income_typical"),
                net = o.long("net") ?: 0,
                everyday = everyday(o.optJSONObject("everyday")),
                oneOff = o.long("one_off") ?: 0,
                setAside = o.long("set_aside") ?: 0,
                fixedMonthly = o.long("fixed_monthly") ?: 0,
                yearlyMonthly = o.long("yearly_monthly") ?: 0,
                uncategorized = (o.long("uncategorized") ?: 0).toInt(),
                forecast = f?.let {
                    ForecastTile(
                        balance = it.long("balance") ?: 0,
                        asOf = it.optString("as_of"),
                        soonLow = datedBalance(it.optJSONObject("soon_low")),
                        lowPoint = datedBalance(it.optJSONObject("low_point")),
                        end = it.str("end"),
                        endBalance = it.long("end_balance")
                    )
                }
            )
        }
    }
}

// ---------------------------------------------------------------- insights

data class InsightLink(val view: String, val category: String?, val id: String?)

data class Insight(val kind: String, val tone: String, val title: String, val detail: String?, val amount: Long?, val link: InsightLink?)

/** `GET insights?month=` → the month's cards, ranked. */
object MoneyInsights {
    fun parse(o: JSONObject): List<Insight> = o.optJSONArray("items").objects { i ->
        Insight(
            kind = i.optString("kind"),
            tone = i.str("tone") ?: "info",
            title = i.optString("title"),
            detail = i.str("detail"),
            amount = i.long("amount"),
            link = i.optJSONObject("link")?.let { l -> l.str("view")?.let { InsightLink(it, l.str("category"), l.str("id")) } }
        )
    }
}

// ---------------------------------------------------------------- pace

/** `GET pace?month=`: everyday spending by day, cumulative (null after today), against the typical month. */
data class MoneyPace(
    val month: String,
    val label: String,
    val days: Int,
    val today: Int?,
    val thisMonth: List<Long?>,
    val typical: List<Long?>?,
    val low: List<Long?>?,
    val high: List<Long?>?,
    val months: Int
) {
    companion object {
        fun parse(o: JSONObject) = MoneyPace(
            month = o.optString("month"),
            label = o.optString("label"),
            days = (o.long("days") ?: 0).toInt(),
            today = o.long("today")?.toInt()?.takeIf { it > 0 },
            thisMonth = o.longs("this_month") ?: emptyList(),
            typical = o.longs("typical"),
            low = o.longs("low"),
            high = o.longs("high"),
            months = (o.long("months") ?: 0).toInt()
        )
    }
}

// ---------------------------------------------------------------- balance + forecast

data class BalancePoint(val date: String, val balance: Long, val low: Long? = null, val high: Long? = null)

data class BillItem(val label: String, val kind: String, val amount: Long)

data class ForecastEvent(
    val date: String,
    val kind: String,
    val label: String,
    val amount: Long,
    val estimated: Boolean,
    /** Card bill parts by key (known, installments, fixed, yearly, everyday). */
    val parts: Map<String, Long>,
    val items: List<BillItem>,
    val range: Pair<Long, Long>?,
    val accountId: String?
)

data class InstallmentPlan(
    val merchant: String,
    val amount: Long,
    val dates: List<String>,
    val total: Long,
    val left: Long,
    val number: Int,
    val of: Int,
    val accountId: String?
)

data class MoneyForecast(
    val asOf: String,
    val balance: Long,
    val end: String,
    val series: List<BalancePoint>,
    val events: List<ForecastEvent>,
    val soonLow: DatedBalance?,
    val lowPoint: DatedBalance?,
    val plans: List<InstallmentPlan>,
    val leftOutTotal: Long
)

/** `GET balance?days=` → the bank balance history and the forecast. */
data class MoneyBalance(val history: List<BalancePoint>, val forecast: MoneyForecast?) {
    companion object {
        fun parse(o: JSONObject): MoneyBalance {
            val f = o.optJSONObject("forecast")
            return MoneyBalance(
                history = o.optJSONArray("history").objects { BalancePoint(it.optString("date"), it.long("balance") ?: 0) },
                forecast = f?.let { fc ->
                    MoneyForecast(
                        asOf = fc.optString("as_of"),
                        balance = fc.long("balance") ?: 0,
                        end = fc.optString("end"),
                        series = fc.optJSONArray("series").objects {
                            val b = it.long("balance") ?: 0
                            BalancePoint(it.optString("date"), b, it.long("low") ?: b, it.long("high") ?: b)
                        },
                        events = fc.optJSONArray("events").objects { e ->
                            val parts = e.optJSONObject("parts")
                            val r = e.optJSONArray("range")
                            ForecastEvent(
                                date = e.optString("date"),
                                kind = e.optString("kind"),
                                label = e.optString("label"),
                                amount = e.long("amount") ?: 0,
                                estimated = e.optBoolean("estimated"),
                                parts = parts?.keys()?.asSequence()?.associateWith { k -> parts.long(k) ?: 0 } ?: emptyMap(),
                                items = e.optJSONArray("items").objects { BillItem(it.optString("label"), it.optString("kind"), it.long("amount") ?: 0) },
                                range = if (r != null && r.length() == 2) Math.round(r.optDouble(0)) to Math.round(r.optDouble(1)) else null,
                                accountId = e.str("account_id")
                            )
                        },
                        soonLow = datedBalance(fc.optJSONObject("soon_low")),
                        lowPoint = datedBalance(fc.optJSONObject("low_point")),
                        plans = fc.optJSONArray("plans").objects { p ->
                            val d = p.optJSONArray("dates")
                            InstallmentPlan(
                                merchant = p.optString("merchant"),
                                amount = p.long("amount") ?: 0,
                                dates = (0 until (d?.length() ?: 0)).map { d!!.optString(it) },
                                total = p.long("total") ?: 0,
                                left = p.long("left") ?: 0,
                                number = (p.long("number") ?: 0).toInt(),
                                of = (p.long("of") ?: 0).toInt(),
                                accountId = p.str("account_id")
                            )
                        },
                        leftOutTotal = fc.long("left_out_total") ?: 0
                    )
                }
            )
        }
    }
}

// ---------------------------------------------------------------- cash flow

data class CashflowMonth(
    val month: String,
    val label: String,
    val complete: Boolean,
    val income: Long,
    val fixed: Long,
    val yearly: Long,
    val everyday: Long,
    val oneOff: Long,
    val spent: Long,
    val setAside: Long,
    val net: Long,
    val savingsRate: Double?
) {
    /** The four kinds of spending, in the chart's stacking order. */
    fun out(key: String): Long = when (key) {
        "fixed" -> fixed
        "yearly" -> yearly
        "everyday" -> everyday
        "one_off" -> oneOff
        else -> 0
    }
}

data class CashflowTotals(val income: Long, val spent: Long, val setAside: Long, val net: Long)

/** `GET cashflow?n=12&end=`. */
data class MoneyCashflow(val months: List<CashflowMonth>, val totals: CashflowTotals, val typicalIncome: Long, val typicalNet: Long) {
    companion object {
        fun parse(o: JSONObject): MoneyCashflow {
            val t = o.optJSONObject("totals") ?: JSONObject()
            val ty = o.optJSONObject("typical") ?: JSONObject()
            return MoneyCashflow(
                months = o.optJSONArray("months").objects { m ->
                    CashflowMonth(
                        month = m.optString("month"),
                        label = m.optString("label"),
                        complete = m.bool("complete") != false,
                        income = m.long("income") ?: 0,
                        fixed = m.long("fixed") ?: 0,
                        yearly = m.long("yearly") ?: 0,
                        everyday = m.long("everyday") ?: 0,
                        oneOff = m.long("one_off") ?: 0,
                        spent = m.long("spent") ?: 0,
                        setAside = m.long("set_aside") ?: 0,
                        net = m.long("net") ?: 0,
                        savingsRate = m.dbl("savings_rate")
                    )
                },
                totals = CashflowTotals(t.long("income") ?: 0, t.long("spent") ?: 0, t.long("set_aside") ?: 0, t.long("net") ?: 0),
                typicalIncome = ty.long("income") ?: 0,
                typicalNet = ty.long("net") ?: 0
            )
        }
    }
}

// ---------------------------------------------------------------- trends

/** A group or a category of `trends`: its month, typical, on track and 12 monthly values (oldest first). */
data class TrendLine(
    val key: String,
    val label: String,
    val total: Long,
    val typical: Long,
    val onTrack: Long?,
    val months: List<Long>,
    val categories: List<TrendLine> = emptyList()
)

data class Mover(val key: String, val label: String, val group: String?, val direction: String, val delta: Long, val value: Long, val typical: Long)

data class OneOff(val id: String, val merchant: String?, val description: String?, val purchaseDate: String, val categoryLabel: String?, val amount: Long)

/** `GET trends?month=`. */
data class MoneyTrends(
    val month: String,
    val label: String,
    val inProgress: Boolean,
    val elapsed: Int,
    val days: Int,
    val monthLabels: List<String>,
    val everyday: Everyday,
    val groups: List<TrendLine>,
    val movers: List<Mover>,
    val oneOffs: List<OneOff>
) {
    companion object {
        private fun line(o: JSONObject): TrendLine = TrendLine(
            key = o.optString("key"),
            label = o.optString("label"),
            total = o.long("total") ?: 0,
            typical = o.long("typical") ?: 0,
            onTrack = o.long("on_track"),
            months = o.longs("months")?.map { it ?: 0 } ?: emptyList(),
            categories = o.optJSONArray("categories").objects { line(it) }
        )

        fun parse(o: JSONObject): MoneyTrends {
            val labels = o.optJSONArray("months")
            return MoneyTrends(
                month = o.optString("month"),
                label = o.optString("label"),
                inProgress = o.optBoolean("in_progress"),
                elapsed = (o.long("elapsed") ?: 0).toInt(),
                days = (o.long("days") ?: 0).toInt(),
                monthLabels = (0 until (labels?.length() ?: 0)).map { labels!!.optString(it) },
                everyday = everyday(o.optJSONObject("everyday")),
                groups = o.optJSONArray("groups").objects { line(it) },
                movers = o.optJSONArray("movers").objects {
                    Mover(it.optString("key"), it.optString("label"), it.str("group"), it.optString("direction"),
                        it.long("delta") ?: 0, it.long("value") ?: 0, it.long("typical") ?: 0)
                },
                oneOffs = o.optJSONArray("one_offs").objects {
                    OneOff(it.optString("id"), it.str("merchant"), it.str("description"), it.optString("purchase_date"),
                        it.str("category_label"), it.long("amount") ?: 0)
                }
            )
        }
    }
}

// ---------------------------------------------------------------- months

data class MonthTotal(val month: String, val label: String, val total: Long, val groups: Map<String, Long>)

data class GroupLabel(val key: String, val label: String)

/** `GET months?n=12&end=` (and trends folded into the same shape: [MoneyDashGeometry.trendsAsMonths]). */
data class MoneyMonths(val groups: List<GroupLabel>, val months: List<MonthTotal>) {
    companion object {
        fun parse(o: JSONObject) = MoneyMonths(
            groups = o.optJSONArray("groups").objects { GroupLabel(it.optString("key"), it.optString("label")) },
            months = o.optJSONArray("months").objects { m ->
                val g = m.optJSONObject("groups")
                MonthTotal(
                    m.optString("month"), m.optString("label"), m.long("total") ?: 0,
                    g?.keys()?.asSequence()?.associateWith { k -> g.long(k) ?: 0 } ?: emptyMap()
                )
            }
        )
    }
}

// ---------------------------------------------------------------- merchants

data class TopMerchant(
    val merchant: String,
    val category: String?,
    val categoryLabel: String?,
    val count: Int,
    val average: Long,
    val total: Long,
    val months: List<Long>,
    val oneOff: Boolean
)

/** `GET merchants?period=YYYY-MM&limit=`. */
object MoneyMerchants {
    fun parse(o: JSONObject): List<TopMerchant> = o.optJSONArray("merchants").objects {
        TopMerchant(
            merchant = it.optString("merchant"),
            category = it.str("category"),
            categoryLabel = it.str("category_label"),
            count = (it.long("count") ?: 0).toInt(),
            average = it.long("average") ?: 0,
            total = it.long("total") ?: 0,
            months = it.longs("months")?.map { v -> v ?: 0 } ?: emptyList(),
            oneOff = it.optBoolean("one_off")
        )
    }
}

// ---------------------------------------------------------------- recurring

data class PriceChange(val from: Long, val to: Long, val since: String)

data class RecurringItem(
    val merchant: String,
    val merchantKey: String,
    val cadence: String,
    val amount: Long,
    val monthlyCost: Long?,
    val nextDate: String?,
    val priceChange: PriceChange?,
    val accountId: String?,
    val accountLabel: String?,
    val direction: String
)

data class YearlyItem(
    val merchant: String,
    val merchantKey: String,
    val amount: Long,
    val monthlyCost: Long?,
    val nextDate: String?,
    val payments: Int,
    val categoryLabel: String?
)

/** `GET recurring`: money out, money in, the yearly charges. */
data class MoneyRecurring(val items: List<RecurringItem>, val income: List<RecurringItem>, val yearly: List<YearlyItem>) {
    companion object {
        private fun item(o: JSONObject) = RecurringItem(
            merchant = o.optString("merchant"),
            merchantKey = o.optString("merchant_key"),
            cadence = o.str("cadence") ?: "monthly",
            amount = o.long("amount") ?: 0,
            monthlyCost = o.long("monthly_cost"),
            nextDate = o.str("next_date"),
            priceChange = o.optJSONObject("price_change")?.let { PriceChange(it.long("from") ?: 0, it.long("to") ?: 0, it.optString("since")) },
            accountId = o.str("account_id"),
            accountLabel = o.str("account_label"),
            direction = o.str("direction") ?: "out"
        )

        fun parse(o: JSONObject) = MoneyRecurring(
            items = o.optJSONArray("items").objects { item(it) },
            income = o.optJSONArray("income").objects { item(it) },
            yearly = o.optJSONArray("yearly").objects {
                YearlyItem(it.optString("merchant"), it.optString("merchant_key"), it.long("amount") ?: 0, it.long("monthly_cost"),
                    it.str("next_date"), (it.long("payments") ?: 1).toInt(), it.str("category_label"))
            }
        )
    }
}

// ---------------------------------------------------------------- portfolio

data class Holding(
    val security: String?,
    val name: String?,
    val quantity: Double?,
    val price: Double?,
    val currency: String?,
    val value: Long?,
    val profit: Long?,
    val profitPct: Double?
)

data class InvestmentAccount(
    val id: String,
    val label: String?,
    val number: String,
    val companyName: String,
    val value: Long,
    val profitPct: Double?,
    val holdings: List<Holding>
)

data class PortfolioTotal(val value: Long, val cash: Long?, val dayChange: Long?, val profit: Long?, val cost: Long?, val asOf: String?)

data class AllocationRow(val name: String, val value: Long)

data class Flow(val date: String, val amount: Long)

data class DatedValue(val date: String, val value: Long)

data class Trade(
    val kind: String?,
    val action: String?,
    val name: String?,
    val date: String,
    val quantity: Double?,
    val security: String?,
    val commission: Long?,
    val tax: Long?,
    val amount: Long
)

/** `GET portfolio?days=365&trades=50`. */
data class MoneyPortfolio(
    val total: PortfolioTotal,
    val accounts: List<InvestmentAccount>,
    val allocation: List<AllocationRow>,
    val flows: List<Flow>,
    val history: List<DatedValue>,
    val trades: List<Trade>
) {
    companion object {
        fun parse(o: JSONObject): MoneyPortfolio {
            val t = o.optJSONObject("total") ?: JSONObject()
            return MoneyPortfolio(
                total = PortfolioTotal(t.long("value") ?: 0, t.long("cash"), t.long("day_change"), t.long("profit"), t.long("cost"), t.str("as_of")),
                accounts = o.optJSONArray("accounts").objects { a ->
                    InvestmentAccount(
                        id = a.optString("id"),
                        label = a.str("label"),
                        number = a.optString("number"),
                        companyName = a.optString("company_name"),
                        value = a.long("value") ?: 0,
                        profitPct = a.dbl("profit_pct"),
                        holdings = a.optJSONArray("holdings").objects { h ->
                            Holding(h.str("security"), h.str("name"), h.dbl("quantity"), h.dbl("price"), h.str("currency"),
                                h.long("value"), h.long("profit"), h.dbl("profit_pct"))
                        }
                    )
                },
                allocation = o.optJSONArray("allocation").objects { AllocationRow(it.optString("name"), it.long("value") ?: 0) },
                flows = o.optJSONArray("flows").objects { Flow(it.optString("date"), it.long("amount") ?: 0) },
                history = o.optJSONArray("history").objects { h -> h.long("value")?.let { DatedValue(h.optString("date"), it) } },
                trades = o.optJSONArray("trades").objects {
                    Trade(it.str("kind"), it.str("action"), it.str("name"), it.optString("date"), it.dbl("quantity"), it.str("security"),
                        it.long("commission"), it.long("tax"), it.long("amount") ?: 0)
                }
            )
        }
    }
}

// ---------------------------------------------------------------- meals

data class MealCycleNow(
    val start: String,
    val end: String,
    val daysLeft: Int,
    val budget: Long?,
    val left: Long,
    val used: Long,
    val cardPaid: Long,
    val count: Int,
    val perDayLeft: Long?,
    val runsOut: String?
)

data class MealCycle(
    val start: String,
    val end: String,
    val total: Long,
    val budgetPaid: Long,
    val cardPaid: Long,
    val count: Int,
    /** The card line that paid the own-card part: its date and transaction id. */
    val billed: Pair<String, String?>?
)

data class MealKind(val label: String, val count: Int, val total: Long)

data class MealPlace(val place: String, val count: Int, val total: Long)

data class Meal(
    val id: String,
    val place: String?,
    val date: String,
    val occurredAt: String?,
    val orderType: String?,
    val cardPaid: Long,
    val active: Boolean,
    val total: Long
)

/** `GET meals?months=12`: the first card's current cycle, the cycles (newest first), kinds, places, meals. */
data class MoneyMeals(
    val current: MealCycleNow?,
    val hasAccount: Boolean,
    val cycles: List<MealCycle>,
    val kinds: List<MealKind>,
    val places: List<MealPlace>,
    val meals: List<Meal>
) {
    companion object {
        fun parse(o: JSONObject): MoneyMeals {
            val a = o.optJSONArray("accounts")?.optJSONObject(0)
            val c = a?.optJSONObject("current")
            return MoneyMeals(
                current = c?.let {
                    MealCycleNow(
                        start = it.optString("start"),
                        end = it.optString("end"),
                        daysLeft = (it.long("days_left") ?: 0).toInt(),
                        budget = it.long("budget"),
                        left = it.long("left") ?: 0,
                        used = it.long("used") ?: 0,
                        cardPaid = it.long("card_paid") ?: 0,
                        count = (it.long("count") ?: 0).toInt(),
                        perDayLeft = it.long("per_day_left"),
                        runsOut = it.str("runs_out")
                    )
                },
                hasAccount = a != null,
                cycles = o.optJSONArray("cycles").objects { cy ->
                    MealCycle(
                        start = cy.optString("start"),
                        end = cy.optString("end"),
                        total = cy.long("total") ?: 0,
                        budgetPaid = cy.long("budget_paid") ?: 0,
                        cardPaid = cy.long("card_paid") ?: 0,
                        count = (cy.long("count") ?: 0).toInt(),
                        billed = cy.optJSONObject("billed")?.let { b -> b.str("date")?.let { it to b.str("txn_id") } }
                    )
                },
                kinds = o.optJSONArray("kinds").objects { MealKind(it.optString("label"), (it.long("count") ?: 0).toInt(), it.long("total") ?: 0) },
                places = o.optJSONArray("places").objects { MealPlace(it.optString("place"), (it.long("count") ?: 0).toInt(), it.long("total") ?: 0) },
                meals = o.optJSONArray("meals").objects {
                    Meal(it.optString("id"), it.str("place"), it.optString("date"), it.str("occurred_at"), it.str("order_type"),
                        it.long("card_paid") ?: 0, it.bool("active") != false, it.long("total") ?: 0)
                }
            )
        }
    }
}
