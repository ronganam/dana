package io.github.ronganam.dana.money

import io.github.ronganam.dana.data.money.jsonObjectOf
import io.github.ronganam.dana.data.money.obj
import kotlinx.serialization.json.JsonObject

/**
 * Real server payloads, read from `server/tests/fixtures/` (a test resources dir)
 * (a synthetic dataset, today 18 Nov 2026): `money_web.json`
 * (`server/addons/money/tests/test_money_page.py`), `money_portfolio_web.json` (`test_money_portfolio.py`),
 * `money_meals_web.json` (`test_money_meals.py`). Each key holds `{request, status, body}`.
 */
object MoneyDashFixture {
    private val files = mutableMapOf<String, JsonObject>()

    private fun file(name: String): JsonObject = files.getOrPut(name) {
        jsonObjectOf(MoneyDashFixture::class.java.classLoader!!.getResource(name)!!.readText())
    }

    fun web(key: String): JsonObject = file("money_web.json").obj(key)!!.obj("body")!!
    fun portfolio(key: String): JsonObject = file("money_portfolio_web.json").obj(key)!!.obj("body")!!
    fun meals(key: String): JsonObject = file("money_meals_web.json").obj(key)!!.obj("body")!!
}
