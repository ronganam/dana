package io.github.ronganam.dana.shopping

import io.github.ronganam.dana.data.shopping.ShoppingJson
import io.github.ronganam.dana.data.shopping.ShoppingScreen
import org.json.JSONObject

/**
 * Real server payloads: `test_fixtures/shopping_web.json` of the server repo
 * (captured from its routes by `test_shopping_users.py`), copied into the test
 * resources. Each key holds `{status, body}`.
 */
object ShoppingFixture {
    private val all: JSONObject by lazy {
        val text = ShoppingFixture::class.java.classLoader!!.getResource("shopping_web.json")!!.readText()
        JSONObject(text)
    }

    fun body(key: String): JSONObject = JSONObject(all.getJSONObject(key).getJSONObject("body").toString())
    fun raw(key: String): String = all.getJSONObject(key).getJSONObject("body").toString()
    fun status(key: String): Int = all.getJSONObject(key).getInt("status")
    fun screen(key: String): ShoppingScreen = ShoppingJson.screen(body(key))!!
    fun keys(): List<String> = all.keys().asSequence().toList()
}
