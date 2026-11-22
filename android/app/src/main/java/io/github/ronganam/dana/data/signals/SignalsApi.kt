package io.github.ronganam.dana.data.signals

import io.github.ronganam.dana.data.network.ApiClient
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.ApiException
import io.github.ronganam.dana.data.network.LenientList
import io.github.ronganam.dana.data.network.LenientListSerializer
import io.github.ronganam.dana.data.network.OptString
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonObject

/*
 * The wire shapes of `/api/signals` and the profile's `wake_window` (server
 * `docs/signals.md`). One flat event shape serves the request body and the
 * queue's file: absent fields are left out (`ApiJson` drops nulls).
 */

@Serializable
private data class EventWire(
    val type: String = "",
    val at: Long? = null,
    val start: Long? = null,
    val end: Long? = null,
    val action: OptString = null,
    val via: OptString = null,
    val zoneId: OptString = null
) {
    fun toModel(): SignalEvent? = when (type) {
        "unlock" -> at?.let { SignalEvent.Unlock(it) }
        "screen" -> if (start != null && end != null && end > start) SignalEvent.Screen(start, end) else null
        "car" -> {
            val via = CarVia.of(via)
            if (at != null && via != null && (action == "enter" || action == "leave")) SignalEvent.Car(action == "enter", at, via) else null
        }
        "zone" -> if (at != null && !zoneId.isNullOrEmpty() && (action == "arrive" || action == "leave")) SignalEvent.Zone(action == "arrive", zoneId, at) else null
        else -> null
    }
}

private fun SignalEvent.toWire(): EventWire = when (this) {
    is SignalEvent.Unlock -> EventWire("unlock", at = at)
    is SignalEvent.Screen -> EventWire("screen", start = start, end = end)
    is SignalEvent.Car -> EventWire("car", at = at, action = if (enter) "enter" else "leave", via = via.wire)
    is SignalEvent.Zone -> EventWire("zone", at = at, action = if (arrive) "arrive" else "leave", zoneId = zoneId)
}

@Serializable
private data class RejectedWire(val index: Int = -1, val error: String = "")

@Serializable
private data class PostedWire(val accepted: Int = 0, val rejected: LenientList<RejectedWire> = emptyList())

@Serializable
private data class SourceWire(
    val source: String = "",
    val label: String = "",
    val active: Boolean = false,
    val lastAt: OptString = null,
    val detail: OptString = null
) {
    fun toModel(): SignalSource? = source.takeIf { it.isNotEmpty() }?.let {
        SignalSource(it, label.ifEmpty { it }, active, lastAt, detail.orEmpty())
    }
}

@Serializable
private data class SignalWire(val signal: String = "", val sources: LenientList<SourceWire> = emptyList()) {
    fun toModel(): SignalInfo? = signal.takeIf { it.isNotEmpty() }?.let { SignalInfo(it, sources.mapNotNull { s -> s.toModel() }) }
}

@Serializable
private data class WakeWindowWire(val start: OptString = null, val end: OptString = null, val learned: Boolean = false) {
    fun toModel(): WakeWindow? =
        if (start != null && end != null && WakeWindow.minutes(start) != null && WakeWindow.minutes(end) != null) WakeWindow(start, end, learned) else null
}

@Serializable
private data class OverviewWire(val signals: LenientList<SignalWire> = emptyList(), val wakeWindow: WakeWindowWire? = null)

@Serializable
private data class ProfileWire(val wakeWindow: WakeWindowWire? = null)

/** Reading and writing the signals payloads; the queue's file uses the same event shape. */
internal object SignalsJson {
    fun encodeEvents(events: List<SignalEvent>): String =
        ApiJson.encodeToString(ListSerializer(EventWire.serializer()), events.map { it.toWire() })

    /** The events of [text] (a JSON array); entries that don't read as an event are dropped. */
    fun decodeEvents(text: String): List<SignalEvent> =
        ApiJson.decodeFromString(LenientListSerializer(EventWire.serializer()), text.ifBlank { "[]" }).mapNotNull { it.toModel() }

    /** The body of `POST /api/signals`: `{"events": [...]}`. */
    fun body(events: List<SignalEvent>): JsonObject = buildJsonObject {
        put("events", ApiJson.encodeToJsonElement(ListSerializer(EventWire.serializer()), events.map { it.toWire() }))
    }

    fun posted(code: Int, text: String): SignalsPosted {
        val wire = runCatching { ApiJson.decodeFromString(PostedWire.serializer(), text.ifBlank { "{}" }) }.getOrDefault(PostedWire())
        return SignalsPosted(code, wire.accepted, wire.rejected.filter { it.index >= 0 }.map { RejectedSignal(it.index, it.error) })
    }

    fun overview(text: String): SignalsOverview {
        val wire = ApiJson.decodeFromString(OverviewWire.serializer(), text.ifBlank { "{}" })
        return SignalsOverview(wire.signals.mapNotNull { it.toModel() }, wire.wakeWindow?.toModel())
    }

    fun profileWakeWindow(text: String): WakeWindow? =
        runCatching { ApiJson.decodeFromString(ProfileWire.serializer(), text.ifBlank { "{}" }).wakeWindow?.toModel() }.getOrNull()
}

/** `/api/signals` and the wake window on `/api/profile` (server `docs/signals.md`). */
class SignalsApi(private val http: ApiClient) {

    /**
     * Send [events] (≤ 200, oldest first). Never throws for the server's
     * answer: an unreachable server comes back as code -1, a refusal as its
     * status.
     */
    suspend fun send(events: List<SignalEvent>): SignalsPosted {
        val r = try {
            http.call("POST", "/api/signals", SignalsJson.body(events))
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            return SignalsPosted(-1)
        }
        return SignalsJson.posted(r.code, if (r.ok) r.body else "")
    }

    /** The caller's signals and where each comes from. */
    suspend fun overview(): SignalsOverview =
        SignalsJson.overview(http.text(http.request("/api/signals"), "Couldn't load the signals", "{}"))

    /**
     * Set the wake window to [start]–[end] ("HH:MM"), or back to the learned
     * one with nulls. Returns the window the server now uses.
     */
    suspend fun setWakeWindow(start: String?, end: String?): WakeWindow? {
        val body = buildJsonObject {
            if (start != null && end != null) putJsonObject("wake_window") { put("start", start); put("end", end) }
            else put("wake_window", JsonNull)
        }
        val r = http.call("PUT", "/api/profile", body)
        if (!r.ok) throw ApiException(r.code, "Couldn't change the wake window (HTTP ${r.code})")
        return SignalsJson.profileWakeWindow(r.body)
    }
}
