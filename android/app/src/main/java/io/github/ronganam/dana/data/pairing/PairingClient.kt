package io.github.ronganam.dana.data.pairing

import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.HttpClients
import io.github.ronganam.dana.data.network.LenientList
import io.github.ronganam.dana.data.network.ServerConfig
import io.github.ronganam.dana.data.network.await
import io.github.ronganam.dana.data.network.bearerAuth
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException

/** This app's device on the server, as `/api/pair` and `/api/devices` describe it. */
@Serializable
data class PairedDevice(
    val id: String = "",
    val name: String = "",
    val kind: String = "",
    val room: String = "",
    val role: String = "",
    val userName: String = "",
) {
    /** "Wall tablet · Upstairs · Kitchen": name, room and role that are set (a role the room already says is left out). */
    val summary: String get() = listOf(name, room, roleLabel(role)).filter { it.isNotBlank() }
        .distinctBy { it.lowercase() }.joinToString(" · ")

    companion object {
        /** "living_room" → "Living room"; "" stays "". */
        fun roleLabel(role: String): String =
            role.replace('_', ' ').replaceFirstChar { it.uppercaseChar() }
    }
}

/** Why a pairing failed; [message] is plain words for the screen. */
sealed interface PairError {
    val message: String

    data object InvalidCode : PairError {
        override val message = "That code isn't valid. Check it, or make a new one on Dana's Devices page."
    }
    data class WrongKind(val expected: String) : PairError {
        override val message get() = "That code is for a ${expected.ifBlank { "different" }} device. Make a code for this kind of device."
    }
    data class RateLimited(val retryAfterSeconds: Long?) : PairError {
        override val message get() = retryAfterSeconds?.let { s ->
            "Too many wrong codes. Try again in ${if (s >= 90) "${(s + 59) / 60} minutes" else "$s seconds"}."
        } ?: "Too many wrong codes. Try again in a few minutes."
    }
    data class Invalid(val detail: String) : PairError {
        override val message get() = detail.ifBlank { "The server refused the request." }
    }
    data class Unreachable(val detail: String) : PairError {
        override val message get() = "Can't reach the server${if (detail.isNotBlank()) " ($detail)" else ""}. Check the address."
    }
    data class Http(val code: Int) : PairError {
        override val message get() = "The server answered HTTP $code. Is this a Dana server?"
    }
}

/** What `POST /api/pair` gave: a token and the device it belongs to, or why not. */
sealed interface PairOutcome {
    data class Paired(val token: String, val device: PairedDevice, val serverVersion: String) : PairOutcome
    data class Failed(val error: PairError) : PairOutcome
}

@Serializable
private data class PairAnswer(
    val token: String = "",
    val device: DeviceWire = DeviceWire(),
    val user: UserWire = UserWire(),
    val server: ServerWire = ServerWire(),
)

@Serializable
private data class DeviceWire(
    val id: String = "", val name: String = "", val kind: String = "", val room: String = "", val role: String = "",
    val current: Boolean = false,
)

@Serializable
private data class UserWire(val name: String = "")

@Serializable
private data class ServerWire(val version: String = "")

@Serializable
private data class ErrorWire(val error: String = "", val expected: String = "", val detail: String = "")

@Serializable
private data class DevicesWire(val devices: LenientList<DeviceWire> = emptyList())

/**
 * Pairing with a Dana server (server `docs/devices.md`): redeem a code shown on
 * the Devices page for this device's own token, read this device back, and
 * remove it. [pair] needs no token; the others use the paired one.
 */
class PairingClient(private val client: OkHttpClient = HttpClients.base) {

    /** `POST /api/pair {code, kind, name}` on [serverUrl] (normalized); never throws for a refusal or a dead server. */
    suspend fun pair(serverUrl: String, code: String, kind: String, name: String): PairOutcome {
        val base = ServerConfig.normalize(serverUrl)
        if (base.isEmpty()) return PairOutcome.Failed(PairError.Unreachable("no address"))
        val body = buildJsonObject {
            put("code", PairingCode.normalize(code)); put("kind", kind); put("name", name.take(60))
        }.toString().toRequestBody(JSON)
        val request = try {
            Request.Builder().url("$base/api/pair").post(body).build()
        } catch (e: IllegalArgumentException) {
            return PairOutcome.Failed(PairError.Unreachable("not an address"))
        }
        return try {
            withContext(Dispatchers.IO) {
                client.newCall(request).await().use { response ->
                    val text = response.body.string()
                    if (response.isSuccessful) {
                        val answer = ApiJson.decodeFromString(PairAnswer.serializer(), text.ifBlank { "{}" })
                        if (answer.token.isBlank()) PairOutcome.Failed(PairError.Http(response.code))
                        else PairOutcome.Paired(answer.token, answer.device.toModel(answer.user.name), answer.server.version)
                    } else {
                        val error = runCatching { ApiJson.decodeFromString(ErrorWire.serializer(), text) }.getOrNull() ?: ErrorWire()
                        PairOutcome.Failed(errorFor(response.code, error, response.header("Retry-After")))
                    }
                }
            }
        } catch (e: IOException) {
            PairOutcome.Failed(PairError.Unreachable(e.message.orEmpty().take(80)))
        }
    }

    /** This device as the server sees it now (`GET /api/devices`, the `current` one), or null. */
    suspend fun current(config: ServerConfig, userName: String): PairedDevice? = withContext(Dispatchers.IO) {
        val request = Request.Builder().url("${config.url}/api/devices").bearerAuth(config.token).build()
        client.newCall(request).await().use { response ->
            if (!response.isSuccessful) return@use null
            ApiJson.decodeFromString(DevicesWire.serializer(), response.body.string().ifBlank { "{}" })
                .devices.firstOrNull { it.current }?.toModel(userName)
        }
    }

    /** `DELETE /api/devices/{id}` with this device's own token: its token stops working. True when the server removed it. */
    suspend fun remove(config: ServerConfig, deviceId: String): Boolean = withContext(Dispatchers.IO) {
        val request = Request.Builder().url("${config.url}/api/devices/$deviceId").bearerAuth(config.token).delete().build()
        client.newCall(request).await().use { it.isSuccessful }
    }

    private fun DeviceWire.toModel(userName: String) = PairedDevice(id, name, kind, room, role, userName)

    private fun errorFor(code: Int, error: ErrorWire, retryAfter: String?): PairError = when {
        code == 429 -> PairError.RateLimited(retryAfter?.trim()?.toLongOrNull())
        error.error == "invalid_code" -> PairError.InvalidCode
        error.error == "wrong_kind" -> PairError.WrongKind(error.expected)
        error.error == "invalid" -> PairError.Invalid(error.detail)
        else -> PairError.Http(code)
    }

    private companion object {
        val JSON = "application/json".toMediaType()
    }
}

/** The 8-character pairing codes the server draws (`devices.py`: no 0/O, 1/I). */
object PairingCode {
    const val LENGTH = 8
    const val ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

    /** What was typed, upper case, without spaces or dashes ("k7qm-4xpz" → "K7QM4XPZ"). */
    fun normalize(raw: String): String = raw.uppercase().filter { !it.isWhitespace() && it != '-' }

    /** Eight characters of the alphabet. */
    fun isComplete(code: String): Boolean = code.length == LENGTH && code.all { it in ALPHABET }

    /** "K7QM4XPZ" → "K7QM 4XPZ" for showing. */
    fun grouped(code: String): String = if (code.length > 4) code.take(4) + " " + code.drop(4) else code
}
