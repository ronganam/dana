package io.github.ronganam.dana.data.money

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.longOrNull

// Reading the money routes' answers (kotlinx.serialization's JSON tree). The
// parsers reshape what they read (agorot rounded, blanks to defaults, derived
// fields), so they read the tree with these helpers, which never throw: a
// missing field, JSON null or a value of another type reads as the default.

/** The value at [key] unless it is missing or JSON null. */
private fun JsonObject.value(key: String): JsonElement? = this[key]?.takeIf { it !is JsonNull }

private fun JsonObject.primitive(key: String): JsonPrimitive? = value(key) as? JsonPrimitive

/** True when [key] is missing or JSON null. */
internal fun JsonObject.absent(key: String): Boolean = value(key) == null

/** The value as text (a number or boolean as written, an object or array as JSON); [default] when missing or null. */
internal fun JsonObject.string(key: String, default: String = ""): String = when (val v = value(key)) {
    null -> default
    is JsonPrimitive -> v.content
    else -> v.toString()
}

/** A text value, null when missing, JSON null or blank. */
internal fun JsonObject.str(key: String): String? = if (absent(key)) null else string(key).ifBlank { null }

/** A number that may come as an int or a float (medians): rounded to agorot; null when not a number. */
internal fun JsonObject.long(key: String): Long? =
    primitive(key)?.takeIf { !it.isString }?.let { it.longOrNull ?: it.doubleOrNull?.let(Math::round) }

/** A number (quoted or not) cut to a whole number; [default] when there is none. */
internal fun JsonObject.longValue(key: String, default: Long = 0): Long =
    primitive(key)?.let { it.longOrNull ?: it.doubleOrNull?.toLong() } ?: default

internal fun JsonObject.intValue(key: String, default: Int = 0): Int = longValue(key, default.toLong()).toInt()

internal fun JsonObject.dbl(key: String): Double? = primitive(key)?.takeIf { !it.isString }?.doubleOrNull

/** A number (quoted or not); NaN when there is none. */
internal fun JsonObject.doubleValue(key: String): Double = primitive(key)?.doubleOrNull ?: Double.NaN

/** `true` / `false` (also as text, any case); [default] for anything else. */
internal fun JsonObject.flag(key: String, default: Boolean = false): Boolean =
    when (primitive(key)?.content?.lowercase()) {
        "true" -> true
        "false" -> false
        else -> default
    }

/** A boolean, null when missing or JSON null (anything else not `true` reads as false). */
internal fun JsonObject.bool(key: String): Boolean? = if (absent(key)) null else flag(key)

internal fun JsonObject.obj(key: String): JsonObject? = this[key] as? JsonObject

internal fun JsonObject.arr(key: String): JsonArray? = this[key] as? JsonArray

/** The objects of an array, other elements skipped. */
internal fun JsonArray?.objects(): List<JsonObject> = this?.filterIsInstance<JsonObject>().orEmpty()

internal inline fun <T : Any> JsonArray?.objects(f: (JsonObject) -> T?): List<T> = objects().mapNotNull(f)

/** Every element as text (JSON null as ""). */
internal fun JsonArray?.texts(): List<String> = this?.map { e -> (e as? JsonPrimitive)?.takeIf { it !is JsonNull }?.content ?: if (e is JsonNull) "" else e.toString() }.orEmpty()

/** The non-empty texts of an array. */
internal fun JsonArray?.strings(): List<String> = texts().filter { it.isNotEmpty() }

/** `[1200, null, 3400]` → longs with nulls kept; null when the key is missing or not an array. */
internal fun JsonObject.longs(key: String): List<Long?>? =
    arr(key)?.map { e -> (e as? JsonPrimitive)?.takeIf { !it.isString }?.let { it.longOrNull ?: it.doubleOrNull?.let(Math::round) } }

/** A JSON primitive unpacked to a Kotlin scalar (String, Long, Double, Boolean) or null when absent. */
internal fun JsonObject.anyValue(key: String): Any? {
    val p = primitive(key) ?: return value(key)
    if (p.isString) return p.content
    return p.longOrNull ?: p.doubleOrNull ?: when (p.content.lowercase()) {
        "true" -> true
        "false" -> false
        else -> p.content
    }
}

/** A Kotlin scalar or JsonElement wrapped as a [JsonElement]. */
internal fun jsonValue(v: Any?): JsonElement = when (v) {
    null -> JsonNull
    is JsonElement -> v
    is Boolean -> JsonPrimitive(v)
    is Number -> JsonPrimitive(v)
    else -> JsonPrimitive(v.toString())
}

/** The answer text as a JSON object; an empty object when it is not one. */
internal fun jsonObjectOf(text: String?): JsonObject =
    runCatching { MoneyJson.parseToJsonElement(text.orEmpty()) as? JsonObject }.getOrNull() ?: EmptyJson

/** The text as a JSON array; an empty array when it is not one. */
internal fun jsonArrayOf(text: String?): JsonArray =
    runCatching { MoneyJson.parseToJsonElement(text.orEmpty()) as? JsonArray }.getOrNull() ?: EmptyJsonArray

internal val EmptyJson = JsonObject(emptyMap())
internal val EmptyJsonArray = JsonArray(emptyList())

/** Plain JSON for the money files and bodies (field names as written). */
internal val MoneyJson = kotlinx.serialization.json.Json { ignoreUnknownKeys = true; isLenient = true }
