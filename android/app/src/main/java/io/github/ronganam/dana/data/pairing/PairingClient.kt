package io.github.ronganam.dana.data.pairing

import io.github.ronganam.dana.data.network.ApiException
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.HttpResult
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
import io.github.ronganam.dana.protocol.PairAnswer
import io.github.ronganam.dana.protocol.PairError
import io.github.ronganam.dana.protocol.PairRequest
import io.github.ronganam.dana.protocol.PairingCode
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

/** What `POST /api/pair` gave: a token and the device it belongs to, or why not. */
sealed interface PairOutcome {
    data class Paired(val token: String, val device: PairedDevice, val serverVersion: String) : PairOutcome
    data class Failed(val error: PairError) : PairOutcome
}

@Serializable
private data class DeviceWire(
    val id: String = "", val name: String = "", val kind: String = "", val room: String = "", val role: String = "",
    val current: Boolean = false,
)

@Serializable
private data class StartedWire(val code: String = "")

@Serializable
private data class DevicesWire(val devices: LenientList<DeviceWire> = emptyList())

/**
 * Pairing with a Dana server (server `docs/devices.md`): redeem a code shown on
 * the Devices page for this device's own token, read this device back,
 * remove it, and start a pairing for another device (the watch). [pair] needs
 * no token; the others use the paired one. The code's shape, the wire and the
 * refusals ([PairError]) are `:protocol`'s, shared with the watch.
 */
class PairingClient(private val client: OkHttpClient = HttpClients.base) {

    /** `POST /api/pair {code, kind, name}` on [serverUrl] (normalized); never throws for a refusal or a dead server. */
    suspend fun pair(serverUrl: String, code: String, kind: String, name: String): PairOutcome {
        val base = ServerConfig.normalize(serverUrl)
        if (base.isEmpty()) return PairOutcome.Failed(PairError.Unreachable("no address"))
        val body = ApiJson.encodeToString(PairRequest.serializer(), PairRequest(PairingCode.normalize(code), kind, name.take(60)))
            .toRequestBody(JSON)
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
                        val answer = PairAnswer.decode(text)
                        if (answer == null) PairOutcome.Failed(PairError.Http(response.code))
                        else PairOutcome.Paired(answer.token, answer.device.let {
                            PairedDevice(it.id, it.name, it.kind, it.room, it.role, answer.user.name)
                        }, answer.server.version)
                    } else {
                        PairOutcome.Failed(PairError.from(response.code, text, response.header("Retry-After")))
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

    /**
     * `POST /api/devices/pairings {kind, name}` with [config]'s token: a code for
     * another device of the same user (the phone makes the watch's). Returns the code.
     *
     * @throws ApiException the server refused (its `error` as the message)
     * @throws IOException the server couldn't be reached
     */
    suspend fun startPairing(config: ServerConfig, kind: String, name: String = ""): String = withContext(Dispatchers.IO) {
        val body = buildJsonObject { put("kind", kind); if (name.isNotBlank()) put("name", name.take(60)) }
            .toString().toRequestBody(JSON)
        val request = Request.Builder().url("${config.url}/api/devices/pairings").bearerAuth(config.token).post(body).build()
        client.newCall(request).await().use { response ->
            val result = HttpResult(response.code, response.body.string())
            if (!result.ok) throw ApiException(response.code, result.error.ifBlank { "HTTP ${response.code}" })
            ApiJson.decodeFromString(StartedWire.serializer(), result.body.ifBlank { "{}" }).code
        }
    }

    private fun DeviceWire.toModel(userName: String) = PairedDevice(id, name, kind, room, role, userName)

    private companion object {
        val JSON = "application/json".toMediaType()
    }
}
