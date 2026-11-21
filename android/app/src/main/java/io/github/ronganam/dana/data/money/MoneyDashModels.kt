package io.github.ronganam.dana.data.money

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.doubleOrNull

// The payloads of the Money dashboards (server docs/money.md → "Page API and
// insights", "Dashboards", "Investment accounts", "Meal cards"; the routes in
// server/addons/money/api.py). Amounts are agorot, dates ISO `YYYY-MM-DD`.
// Each parser reads one route's answer and never throws on a missing field.

// ---------------------------------------------------------------- overview

data class DatedBalance(val date: String, val balance: Long, val days: Int? = null)

internal fun datedBalance(o: JsonObject?): DatedBalance? =
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

internal fun everyday(o: JsonObject?) = Everyday(o?.long("total") ?: 0, o?.long("on_track"), o?.long("typical"))

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
        fun parse(o: JsonObject): MoneyOverview {
            val f = o.obj("forecast")
            return MoneyOverview(
                month = o.string("month"),
                inProgress = o.obj("period")?.flag("in_progress") == true,
                incomeMonth = o.long("income_month") ?: 0,
                incomeTypical = o.long("income_typical"),
                net = o.long("net") ?: 0,
                everyday = everyday(o.obj("everyday")),
                oneOff = o.long("one_off") ?: 0,
                setAside = o.long("set_aside") ?: 0,
                fixedMonthly = o.long("fixed_monthly") ?: 0,
                yearlyMonthly = o.long("yearly_monthly") ?: 0,
                uncategorized = (o.long("uncategorized") ?: 0).toInt(),
                forecast = f?.let {
                    ForecastTile(
                        balance = it.long("balance") ?: 0,
                        asOf = it.string("as_of"),
                        soonLow = datedBalance(it.obj("soon_low")),
                        lowPoint = datedBalance(it.obj("low_point")),
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
    fun parse(o: JsonObject): List<Insight> = o.arr("items").objects { i ->
        Insight(
            kind = i.string("kind"),
            tone = i.str("tone") ?: "info",
            title = i.string("title"),
            detail = i.str("detail"),
            amount = i.long("amount"),
            link = i.obj("link")?.let { l -> l.str("view")?.let { InsightLink(it, l.str("category"), l.str("id")) } }
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
        fun parse(o: JsonObject) = MoneyPace(
            month = o.string("month"),
            label = o.string("label"),
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
        fun parse(o: JsonObject): MoneyBalance {
            val f = o.obj("forecast")
            return MoneyBalance(
                history = o.arr("history").objects { BalancePoint(it.string("date"), it.long("balance") ?: 0) },
                forecast = f?.let { fc ->
                    MoneyForecast(
                        asOf = fc.string("as_of"),
                        balance = fc.long("balance") ?: 0,
                        end = fc.string("end"),
                        series = fc.arr("series").objects {
                            val b = it.long("balance") ?: 0
                            BalancePoint(it.string("date"), b, it.long("low") ?: b, it.long("high") ?: b)
                        },
                        events = fc.arr("events").objects { e ->
                            val parts = e.obj("parts")
                            val r = e.arr("range")
                            ForecastEvent(
                                date = e.string("date"),
                                kind = e.string("kind"),
                                label = e.string("label"),
                                amount = e.long("amount") ?: 0,
                                estimated = e.flag("estimated"),
                                parts = parts?.keys?.associateWith { k -> parts.long(k) ?: 0 } ?: emptyMap(),
                                items = e.arr("items").objects { BillItem(it.string("label"), it.string("kind"), it.long("amount") ?: 0) },
                                range = if (r != null && r.size == 2) {
                                    val a = (r[0] as? JsonPrimitive)?.doubleOrNull ?: Double.NaN
                                    val b = (r[1] as? JsonPrimitive)?.doubleOrNull ?: Double.NaN
                                    Math.round(a) to Math.round(b)
                                } else null,
                                accountId = e.str("account_id")
                            )
                        },
                        soonLow = datedBalance(fc.obj("soon_low")),
                        lowPoint = datedBalance(fc.obj("low_point")),
                        plans = fc.arr("plans").objects { p ->
                            InstallmentPlan(
                                merchant = p.string("merchant"),
                                amount = p.long("amount") ?: 0,
                                dates = p.arr("dates").texts(),
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
        fun parse(o: JsonObject): MoneyCashflow {
            val t = o.obj("totals") ?: EmptyJson
            val ty = o.obj("typical") ?: EmptyJson
            return MoneyCashflow(
                months = o.arr("months").objects { m ->
                    CashflowMonth(
                        month = m.string("month"),
                        label = m.string("label"),
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
        private fun line(o: JsonObject): TrendLine = TrendLine(
            key = o.string("key"),
            label = o.string("label"),
            total = o.long("total") ?: 0,
            typical = o.long("typical") ?: 0,
            onTrack = o.long("on_track"),
            months = o.longs("months")?.map { it ?: 0 } ?: emptyList(),
            categories = o.arr("categories").objects { line(it) }
        )

        fun parse(o: JsonObject): MoneyTrends = MoneyTrends(
            month = o.string("month"),
            label = o.string("label"),
            inProgress = o.flag("in_progress"),
            elapsed = (o.long("elapsed") ?: 0).toInt(),
            days = (o.long("days") ?: 0).toInt(),
            monthLabels = o.arr("months").texts(),
            everyday = everyday(o.obj("everyday")),
            groups = o.arr("groups").objects { line(it) },
            movers = o.arr("movers").objects {
                Mover(it.string("key"), it.string("label"), it.str("group"), it.string("direction"),
                    it.long("delta") ?: 0, it.long("value") ?: 0, it.long("typical") ?: 0)
            },
            oneOffs = o.arr("one_offs").objects {
                OneOff(it.string("id"), it.str("merchant"), it.str("description"), it.string("purchase_date"),
                    it.str("category_label"), it.long("amount") ?: 0)
            }
        )
    }
}

// ---------------------------------------------------------------- months

data class MonthTotal(val month: String, val label: String, val total: Long, val groups: Map<String, Long>)

data class GroupLabel(val key: String, val label: String)

/** `GET months?n=12&end=` (and trends folded into the same shape: [MoneyDashGeometry.trendsAsMonths]). */
data class MoneyMonths(val groups: List<GroupLabel>, val months: List<MonthTotal>) {
    companion object {
        fun parse(o: JsonObject) = MoneyMonths(
            groups = o.arr("groups").objects { GroupLabel(it.string("key"), it.string("label")) },
            months = o.arr("months").objects { m ->
                val g = m.obj("groups")
                MonthTotal(
                    m.string("month"), m.string("label"), m.long("total") ?: 0,
                    g?.keys?.associateWith { k -> g.long(k) ?: 0 } ?: emptyMap()
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
    fun parse(o: JsonObject): List<TopMerchant> = o.arr("merchants").objects {
        TopMerchant(
            merchant = it.string("merchant"),
            category = it.str("category"),
            categoryLabel = it.str("category_label"),
            count = (it.long("count") ?: 0).toInt(),
            average = it.long("average") ?: 0,
            total = it.long("total") ?: 0,
            months = it.longs("months")?.map { v -> v ?: 0 } ?: emptyList(),
            oneOff = it.flag("one_off")
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
        private fun item(o: JsonObject) = RecurringItem(
            merchant = o.string("merchant"),
            merchantKey = o.string("merchant_key"),
            cadence = o.str("cadence") ?: "monthly",
            amount = o.long("amount") ?: 0,
            monthlyCost = o.long("monthly_cost"),
            nextDate = o.str("next_date"),
            priceChange = o.obj("price_change")?.let { PriceChange(it.long("from") ?: 0, it.long("to") ?: 0, it.string("since")) },
            accountId = o.str("account_id"),
            accountLabel = o.str("account_label"),
            direction = o.str("direction") ?: "out"
        )

        fun parse(o: JsonObject) = MoneyRecurring(
            items = o.arr("items").objects { item(it) },
            income = o.arr("income").objects { item(it) },
            yearly = o.arr("yearly").objects {
                YearlyItem(it.string("merchant"), it.string("merchant_key"), it.long("amount") ?: 0, it.long("monthly_cost"),
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
        fun parse(o: JsonObject): MoneyPortfolio {
            val t = o.obj("total") ?: EmptyJson
            return MoneyPortfolio(
                total = PortfolioTotal(t.long("value") ?: 0, t.long("cash"), t.long("day_change"), t.long("profit"), t.long("cost"), t.str("as_of")),
                accounts = o.arr("accounts").objects { a ->
                    InvestmentAccount(
                        id = a.string("id"),
                        label = a.str("label"),
                        number = a.string("number"),
                        companyName = a.string("company_name"),
                        value = a.long("value") ?: 0,
                        profitPct = a.dbl("profit_pct"),
                        holdings = a.arr("holdings").objects { h ->
                            Holding(h.str("security"), h.str("name"), h.dbl("quantity"), h.dbl("price"), h.str("currency"),
                                h.long("value"), h.long("profit"), h.dbl("profit_pct"))
                        }
                    )
                },
                allocation = o.arr("allocation").objects { AllocationRow(it.string("name"), it.long("value") ?: 0) },
                flows = o.arr("flows").objects { Flow(it.string("date"), it.long("amount") ?: 0) },
                history = o.arr("history").objects { h -> h.long("value")?.let { DatedValue(h.string("date"), it) } },
                trades = o.arr("trades").objects {
                    Trade(it.str("kind"), it.str("action"), it.str("name"), it.string("date"), it.dbl("quantity"), it.str("security"),
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
        fun parse(o: JsonObject): MoneyMeals {
            val a = o.arr("accounts").objects().firstOrNull()
            val c = a?.obj("current")
            return MoneyMeals(
                current = c?.let {
                    MealCycleNow(
                        start = it.string("start"),
                        end = it.string("end"),
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
                cycles = o.arr("cycles").objects { cy ->
                    MealCycle(
                        start = cy.string("start"),
                        end = cy.string("end"),
                        total = cy.long("total") ?: 0,
                        budgetPaid = cy.long("budget_paid") ?: 0,
                        cardPaid = cy.long("card_paid") ?: 0,
                        count = (cy.long("count") ?: 0).toInt(),
                        billed = cy.obj("billed")?.let { b -> b.str("date")?.let { it to b.str("txn_id") } }
                    )
                },
                kinds = o.arr("kinds").objects { MealKind(it.string("label"), (it.long("count") ?: 0).toInt(), it.long("total") ?: 0) },
                places = o.arr("places").objects { MealPlace(it.string("place"), (it.long("count") ?: 0).toInt(), it.long("total") ?: 0) },
                meals = o.arr("meals").objects {
                    Meal(it.string("id"), it.str("place"), it.string("date"), it.str("occurred_at"), it.str("order_type"),
                        it.long("card_paid") ?: 0, it.bool("active") != false, it.long("total") ?: 0)
                }
            )
        }
    }
}
