package io.github.ronganam.dana.data.network

import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.KSerializer
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.descriptors.PrimitiveKind
import kotlinx.serialization.descriptors.PrimitiveSerialDescriptor
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.descriptors.nullable
import kotlinx.serialization.descriptors.listSerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonEncoder
import kotlinx.serialization.json.JsonNamingStrategy
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.longOrNull

/**
 * The one JSON setup for the server's payloads (and the app's own files that
 * hold them). Lenient like the server's clients must be: unknown keys are
 * ignored, a missing or null field takes the property's default, numbers may
 * come quoted, and property names map to the server's snake_case
 * (`dueDate` ↔ `due_date`; `@SerialName` where the server's name differs).
 * Nulls are left out of request bodies (an absent field = "not given").
 */
@OptIn(ExperimentalSerializationApi::class)
val ApiJson: Json = Json {
    ignoreUnknownKeys = true
    isLenient = true
    coerceInputValues = true
    explicitNulls = false
    encodeDefaults = true
    namingStrategy = JsonNamingStrategy.SnakeCase
}

/** Decode [text] as [T]; a blank body reads as [blank] (the server's "no answer"). */
inline fun <reified T> decodeJson(text: String, blank: String = "{}"): T =
    ApiJson.decodeFromString(text.ifBlank { blank })

/** Decode a JSON array, skipping the elements that don't read as [T] (like the server's other clients). */
fun <T> decodeListLenient(element: JsonElement?, serializer: KSerializer<T>): List<T> =
    (element as? JsonArray).orEmpty().mapNotNull { runCatching { ApiJson.decodeFromJsonElement(serializer, it) }.getOrNull() }

/**
 * A text field the server may leave out, send as null, "" or "null": all of
 * them read as null. Numbers and booleans read as their text.
 */
typealias OptString = @Serializable(with = BlankAsNullSerializer::class) String?

/** Any JSON value kept as its JSON text (a string as its content); null / absent → null. */
typealias JsonText = @Serializable(with = JsonTextSerializer::class) String?

/** A list whose elements that don't parse are dropped instead of failing the whole answer. */
typealias LenientList<T> = @Serializable(with = LenientListSerializer::class) List<T>

/** Strings of a list, blanks and nulls dropped (`["a", "", null]` → `["a"]`). */
typealias TextList = @Serializable(with = TextListSerializer::class) List<String>

object BlankAsNullSerializer : KSerializer<String?> {
    @OptIn(ExperimentalSerializationApi::class)
    override val descriptor: SerialDescriptor = PrimitiveSerialDescriptor("OptString", PrimitiveKind.STRING).nullable

    override fun deserialize(decoder: Decoder): String? {
        val element = (decoder as? JsonDecoder)?.decodeJsonElement() ?: return decoder.decodeString()
        return (element as? JsonPrimitive)?.takeIf { it !is JsonNull }?.content?.takeIf { it.isNotEmpty() && it != "null" }
    }

    @OptIn(ExperimentalSerializationApi::class)
    override fun serialize(encoder: Encoder, value: String?) {
        if (value == null) encoder.encodeNull() else encoder.encodeString(value)
    }
}

object JsonTextSerializer : KSerializer<String?> {
    @OptIn(ExperimentalSerializationApi::class)
    override val descriptor: SerialDescriptor = PrimitiveSerialDescriptor("JsonText", PrimitiveKind.STRING).nullable

    override fun deserialize(decoder: Decoder): String? {
        val element = (decoder as? JsonDecoder)?.decodeJsonElement() ?: return decoder.decodeString()
        return when (element) {
            is JsonNull -> null
            is JsonPrimitive -> element.content.takeIf { it.isNotEmpty() && it != "null" }
            else -> element.toString()
        }
    }

    @OptIn(ExperimentalSerializationApi::class)
    override fun serialize(encoder: Encoder, value: String?) {
        if (value == null) { encoder.encodeNull(); return }
        val json = encoder as? JsonEncoder
        val parsed = if (json != null) runCatching { ApiJson.parseToJsonElement(value) }.getOrNull() else null
        if (json != null && (parsed is JsonObject || parsed is JsonArray)) json.encodeJsonElement(parsed) else encoder.encodeString(value)
    }
}

class LenientListSerializer<T>(private val element: KSerializer<T>) : KSerializer<List<T>> {
    @OptIn(ExperimentalSerializationApi::class)
    override val descriptor: SerialDescriptor = listSerialDescriptor(element.descriptor)

    override fun deserialize(decoder: Decoder): List<T> {
        val json = decoder as? JsonDecoder ?: return ListSerializer(element).deserialize(decoder)
        return json.decodeJsonElement().let { (it as? JsonArray).orEmpty() }
            .mapNotNull { runCatching { json.json.decodeFromJsonElement(element, it) }.getOrNull() }
    }

    override fun serialize(encoder: Encoder, value: List<T>) = ListSerializer(element).serialize(encoder, value)
}

object TextListSerializer : KSerializer<List<String>> {
    @OptIn(ExperimentalSerializationApi::class)
    override val descriptor: SerialDescriptor = listSerialDescriptor(String.serializer().descriptor)

    override fun deserialize(decoder: Decoder): List<String> {
        val json = decoder as? JsonDecoder ?: return ListSerializer(String.serializer()).deserialize(decoder)
        return (json.decodeJsonElement() as? JsonArray).orEmpty()
            .mapNotNull { (it as? JsonPrimitive)?.takeIf { p -> p !is JsonNull }?.content?.takeIf(String::isNotBlank) }
    }

    override fun serialize(encoder: Encoder, value: List<String>) = ListSerializer(String.serializer()).serialize(encoder, value)
}

// --- Reading a JSON tree (payloads whose shape the server leaves open: tool args, choice values) ---

/** The value as plain Kotlin data: String, Long, Double, Boolean, Map, List or null. */
fun JsonElement?.toPlain(): Any? = when (this) {
    null, JsonNull -> null
    is JsonObject -> mapValues { it.value.toPlain() }
    is JsonArray -> map { it.toPlain() }
    is JsonPrimitive -> if (isString) content else booleanOrNull ?: longOrNull ?: doubleOrNull ?: content
}

/** Plain Kotlin data (as [toPlain] gives it) back to JSON. */
fun Any?.toJsonElement(): JsonElement = when (this) {
    null -> JsonNull
    is JsonElement -> this
    is String -> JsonPrimitive(this)
    is Number -> JsonPrimitive(this)
    is Boolean -> JsonPrimitive(this)
    is Map<*, *> -> JsonObject(entries.associate { (k, v) -> k.toString() to v.toJsonElement() })
    is Iterable<*> -> JsonArray(map { it.toJsonElement() })
    is Array<*> -> JsonArray(map { it.toJsonElement() })
    else -> JsonPrimitive(toString())
}

/** A field as text the way the server's other clients read it: null, "" and "null" are absent. */
fun JsonObject.text(key: String): String? =
    (this[key] as? JsonPrimitive)?.takeIf { it !is JsonNull }?.content?.takeIf { it.isNotEmpty() && it != "null" }

fun JsonObject.long(key: String): Long? = (this[key] as? JsonPrimitive)?.let { it.longOrNull ?: it.doubleOrNull?.let(Math::round) }
fun JsonObject.bool(key: String): Boolean? = (this[key] as? JsonPrimitive)?.booleanOrNull
fun JsonObject.obj(key: String): JsonObject? = this[key] as? JsonObject

@OptIn(ExperimentalSerializationApi::class)
private val PrettyJson = Json { prettyPrint = true; prettyPrintIndent = "  " }

/**
 * A tool argument / result blob for display: JSON (or a string holding JSON)
 * pretty-printed with two-space indents, any other text as is; null → "".
 */
fun JsonElement?.pretty(): String {
    val text = when (this) {
        null, JsonNull -> return ""
        is JsonPrimitive -> content
        else -> toString()
    }
    if (!text.startsWith("{") && !text.startsWith("[")) return text
    return runCatching { PrettyJson.encodeToString(JsonElement.serializer(), Json.parseToJsonElement(text)) }.getOrDefault(text)
}
