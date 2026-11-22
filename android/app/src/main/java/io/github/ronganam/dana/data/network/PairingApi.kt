package io.github.ronganam.dana.data.network

import io.github.ronganam.dana.data.network.ApiClient.Companion.enc
import io.github.ronganam.dana.data.network.ApiClient.Companion.jsonBody
import io.github.ronganam.dana.protocol.PairAnswer
import io.github.ronganam.dana.protocol.PairRequest
import io.github.ronganam.dana.protocol.pairFailureMessage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** A refused pairing; [message] is the sentence to show. */
class PairingException(message: String, val status: Int = 0) : Exception(message)

/** A pairing this device started for another one: the [code] to hand over, valid until [expiresAt]. */
@Serializable
data class StartedPairing(val id: String = "", val code: String = "", val expiresAt: String = "", val kind: String = "")

/**
 * Devices and pairing (server docs/devices.md): [pair] turns a code into
 * this device's own token (public, so the client needs no token), [startPairing]
 * makes a code for another device of the same user (the watch), [revoke]
 * removes a device and its token.
 */
class PairingApi(private val http: ApiClient) {

    /**
     * `POST /api/pair {code, kind, name}`: the new device's token and device.
     *
     * @throws PairingException the server refused (wrong code, another kind, too many tries)
     * @throws java.io.IOException the server couldn't be reached
     */
    suspend fun pair(code: String, kind: String, name: String): PairAnswer = withContext(Dispatchers.IO) {
        val body = jsonBody(PairRequest.serializer(), PairRequest(code, kind, name))
        http.execute(http.request("/api/pair") { post(body) }).use { response ->
            val text = response.body.string()
            if (response.isSuccessful) {
                PairAnswer.decode(text) ?: throw PairingException("The server's answer had no token.", response.code)
            } else {
                val error = HttpResult(response.code, text).error
                val retryAfter = response.header("Retry-After")?.trim()?.toLongOrNull()
                throw PairingException(pairFailureMessage(response.code, error, retryAfter), response.code)
            }
        }
    }

    /** `POST /api/devices/pairings {kind, name}` for the signed-in user: a code for another device. */
    suspend fun startPairing(kind: String, name: String = ""): StartedPairing {
        val body = buildJsonObject { put("kind", kind); if (name.isNotBlank()) put("name", name) }
        return http.jsonOrThrow(http.request("/api/devices/pairings") { post(jsonBody(body)) },
            "Couldn't start a pairing", StartedPairing.serializer())
    }

    /** `DELETE /api/devices/{id}`: the device and its token are gone; returns the HTTP status. */
    suspend fun revoke(deviceId: String): Int = http.sendForCode(http.request("/api/devices/${enc(deviceId)}") { delete() })
}
