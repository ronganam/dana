package io.github.ronganam.dana.data.network

import io.github.ronganam.dana.data.network.ApiClient.Companion.enc
import io.github.ronganam.dana.data.network.ApiClient.Companion.jsonBody
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * `GET /api/me`: who the token belongs to, the add-ons they have connected,
 * their languages (codes, the first is theirs) and their currency (ISO 4217).
 */
@Serializable
data class MeAnswer(
    val id: OptString = null,
    val name: String = "",
    val addons: TextList = emptyList(),
    val languages: TextList = emptyList(),
    val currency: OptString = null
)

/** One event of the phone's outbox: its number, type and data (read per type by `PhoneEvents`). */
@Serializable
data class PhoneEvent(val seq: Long = 0, val type: String = "", val data: JsonObject = JsonObject(emptyMap()))

/** A page of `GET /api/phone/events`; [registered] false = the server forgot this phone. */
@Serializable
data class PhoneEventsPage(val events: LenientList<PhoneEvent> = emptyList(), val latest: Long = 0, val registered: Boolean = true)

@Serializable
private data class Registered(val latestSeq: Long = 0)

@Serializable
private data class UnlockAnswer(val status: String = "")

/** This user and this device: `/api/me`, direct push, the wake signal, coding-agent sessions. */
class DevicesApi(private val http: ApiClient) {

    suspend fun me(): MeAnswer = http.decode(MeAnswer.serializer(), http.text(http.request("/api/me"), "Failed to load your account", "{}"))

    /** Register this phone's FCM token; returns the server's latest event number. */
    suspend fun registerPush(deviceId: String, token: String, name: String, appVersion: String): Long {
        val body = buildJsonObject { put("device_id", deviceId); put("token", token); put("name", name); put("app_version", appVersion) }
        return http.decode(Registered.serializer(),
            http.text(http.request("/api/phone/register") { post(jsonBody(body)) }, "Couldn't register for push", "{}")).latestSeq
    }

    /** Events newer than [after]. */
    suspend fun phoneEvents(after: Long, deviceId: String): PhoneEventsPage =
        http.decode(PhoneEventsPage.serializer(),
            http.text(http.request("/api/phone/events?after=$after&device_id=${enc(deviceId)}"), "Couldn't fetch phone events", "{}"))

    /** The phone was unlocked (the wake signal). Returns the server's status: confirming | done | outside_window. */
    suspend fun reportUnlock(atMillis: Long): String =
        http.decode(UnlockAnswer.serializer(),
            http.text(http.request("/api/phone/unlock") { post(jsonBody(buildJsonObject { put("at", atMillis) })) }, "Unlock report failed", "{}")).status

    /** Forget one of the user's coding-agent sessions (the live notification's Hide). */
    suspend fun hideAgentSession(id: String): Boolean = http.send(http.request("/api/agent-sessions/${enc(id)}") { delete() })
}
