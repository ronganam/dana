package io.github.ronganam.dana.data.models

import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.OptString
import kotlinx.serialization.KSerializer
import kotlinx.serialization.Serializable
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * A task's `triggers` (server addons/tasks): a time ("2026-11-14T18:30:00",
 * snoozes with an offset), a place (`{location, event: arrive | leave}`), the
 * car (`enter` | `leave`) and the wake signal. Empty = no trigger. Read from
 * an object or from its JSON text (older rows).
 */
@Serializable(with = TriggersSerializer::class)
data class Triggers(
    val time: String? = null,
    val location: LocationTrigger? = null,
    val car: String? = null,
    val wake: Boolean? = null
) {
    val isEmpty: Boolean get() = time == null && location?.location == null && car == null && wake != true

    companion object {
        /** From any JSON value (object, its text, or nothing). */
        fun of(element: JsonElement?): Triggers = when (element) {
            is JsonObject -> runCatching { ApiJson.decodeFromJsonElement(TriggersFields.serializer(), element).toTriggers() }.getOrDefault(Triggers())
            is JsonPrimitive -> if (element.isString) runCatching { of(ApiJson.parseToJsonElement(element.content)) }.getOrDefault(Triggers()) else Triggers()
            else -> Triggers()
        }
    }
}

@Serializable
data class LocationTrigger(val location: OptString = null, val event: OptString = null)

/** The wire shape of [Triggers]. */
@Serializable
internal data class TriggersFields(
    val time: OptString = null,
    val location: LocationTrigger? = null,
    val car: OptString = null,
    val wake: Boolean? = null
) {
    fun toTriggers() = Triggers(time, location, car, wake)
}

object TriggersSerializer : KSerializer<Triggers> {
    override val descriptor: SerialDescriptor = TriggersFields.serializer().descriptor

    override fun deserialize(decoder: Decoder): Triggers {
        val json = decoder as? JsonDecoder ?: return TriggersFields.serializer().deserialize(decoder).toTriggers()
        return Triggers.of(json.decodeJsonElement())
    }

    override fun serialize(encoder: Encoder, value: Triggers) =
        TriggersFields.serializer().serialize(encoder, TriggersFields(value.time, value.location, value.car, value.wake))
}
