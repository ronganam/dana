package io.github.ronganam.dana.data.network

import io.github.ronganam.dana.data.network.ApiClient.Companion.enc
import io.github.ronganam.dana.data.network.ApiClient.Companion.jsonBody
import kotlinx.serialization.Serializable
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * `GET /api/me`: who the token belongs to, the addons they have connected,
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

/** `GET /api/phone/app-icons`: the user's stored app icons, package → the first 8 hex chars of the PNG's sha256. */
@Serializable
private data class AppIconsAnswer(val icons: Map<String, String> = emptyMap())

/** What the server said to a push registration (`POST /api/phone/register`). */
sealed interface PushRegistered {
    /** Stored; [latestSeq] is the user's newest event number (a fresh install starts there). */
    data class Ok(val latestSeq: Long) : PushRegistered

    /** Refused (400 `fcm_not_configured`, `invalid_endpoint`, …): [detail] says why, in words for the user. */
    data class Refused(val code: Int, val error: String, val detail: String) : PushRegistered
}

/** This user and this device: `/api/me`, direct push, app icons, coding-agent sessions. */
class DevicesApi(private val http: ApiClient) {

    suspend fun me(): MeAnswer = http.decode(MeAnswer.serializer(), http.text(http.request("/api/me"), "Failed to load your account", "{}"))

    /**
     * Register this device for push: [transport] `fcm` with its FCM token, or
     * `unifiedpush` with the distributor's endpoint, as [address]. The server
     * ties it to the device of this app's token. Throws [java.io.IOException]
     * when the server can't be reached.
     */
    suspend fun registerPush(transport: String, address: String, appVersion: String): PushRegistered {
        val body = buildJsonObject {
            put("transport", transport)
            put(if (transport == "unifiedpush") "endpoint" else "token", address)
            put("app_version", appVersion)
        }
        val res = http.result(http.request("/api/phone/register") { post(jsonBody(body)) })
        return if (res.ok) PushRegistered.Ok(http.decode(Registered.serializer(), res.body.ifEmpty { "{}" }).latestSeq)
        else PushRegistered.Refused(res.code, res.error, res.json().text("detail").orEmpty())
    }

    /** Events newer than [after]. */
    suspend fun phoneEvents(after: Long): PhoneEventsPage =
        http.decode(PhoneEventsPage.serializer(),
            http.text(http.request("/api/phone/events?after=$after"), "Couldn't fetch phone events", "{}"))

    /**
     * The app icons the server keeps for this user (package → hash). Throws
     * [ApiException] (403 when the phone addon isn't connected) or
     * [java.io.IOException] when the server can't be reached.
     */
    suspend fun appIcons(): Map<String, String> =
        http.decode(AppIconsAnswer.serializer(),
            http.text(http.request("/api/phone/app-icons"), "Couldn't list app icons", "{}")).icons

    /**
     * Store [png] as [pkg]'s icon (`PUT /api/phone/app-icons/{package}`); the
     * HTTP status (204 stored; 400 `bad_package` / `not_png`, 409 `too_many`,
     * 413 `too_large`). Throws [java.io.IOException] when unreachable.
     */
    suspend fun putAppIcon(pkg: String, png: ByteArray): Int =
        http.sendForCode(http.request("/api/phone/app-icons/${enc(pkg)}") { put(png.toRequestBody(PNG)) })

    /** Forget one of the user's coding-agent sessions (the live notification's Hide). */
    suspend fun hideAgentSession(id: String): Boolean = http.send(http.request("/api/agent-sessions/${enc(id)}") { delete() })

    private companion object {
        val PNG = "image/png".toMediaType()
    }
}
