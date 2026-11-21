package io.github.ronganam.dana.money

import org.json.JSONObject

/**
 * Real server payloads, read from `server/tests/fixtures/` (a test resources dir)
 * (a synthetic dataset, today 18 Nov 2026): `money_web.json`
 * (`server/addons/money/tests/test_money_page.py`), `money_portfolio_web.json` (`test_money_portfolio.py`),
 * `money_meals_web.json` (`test_money_meals.py`). Each key holds `{request, status, body}`.
 */
object MoneyDashFixture {
    private val files = mutableMapOf<String, JSONObject>()

    private fun file(name: String): JSONObject = files.getOrPut(name) {
        JSONObject(MoneyDashFixture::class.java.classLoader!!.getResource(name)!!.readText())
    }

    fun web(key: String): JSONObject = file("money_web.json").getJSONObject(key).getJSONObject("body")
    fun portfolio(key: String): JSONObject = file("money_portfolio_web.json").getJSONObject(key).getJSONObject("body")
    fun meals(key: String): JSONObject = file("money_meals_web.json").getJSONObject(key).getJSONObject("body")
}
