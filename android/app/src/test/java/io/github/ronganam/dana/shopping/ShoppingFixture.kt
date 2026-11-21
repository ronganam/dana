package io.github.ronganam.dana.shopping

import io.github.ronganam.dana.data.shopping.ShoppingJson
import io.github.ronganam.dana.data.shopping.ShoppingScreen
import io.github.ronganam.dana.data.network.ApiJson
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * Real server payloads: `server/tests/fixtures/shopping_web.json`
 * (captured from its routes by `test_shopping_users.py`), copied into the test
 * resources. Each key holds `{status, body}`.
 */
object ShoppingFixture {
    private val all: JsonObject by lazy {
        val text = ShoppingFixture::class.java.classLoader!!.getResource("shopping_web.json")!!.readText()
        ApiJson.parseToJsonElement(text) as JsonObject
    }

    /** The body as parsed JSON. */
    fun obj(key: String): JsonObject = all.getValue(key).jsonObject.getValue("body").jsonObject
    /** The body as the server sent it. */
    fun body(key: String): String = obj(key).toString()
    fun raw(key: String): String = body(key)
    fun status(key: String): Int = all.getValue(key).jsonObject.getValue("status").jsonPrimitive.int
    fun screen(key: String): ShoppingScreen = ShoppingJson.screen(body(key))!!
    fun keys(): List<String> = all.keys.toList()
}
