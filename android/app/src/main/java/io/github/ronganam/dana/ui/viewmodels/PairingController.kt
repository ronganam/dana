package io.github.ronganam.dana.ui.viewmodels

import io.github.ronganam.dana.data.network.ApiService
import io.github.ronganam.dana.data.network.PairingException
import io.github.ronganam.dana.data.network.ServerConfig
import io.github.ronganam.dana.protocol.PairLink
import io.github.ronganam.dana.protocol.isPairCodeShape
import io.github.ronganam.dana.protocol.normalizePairCode
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.IOException

/**
 * The pairing screen's form: the server address and the code (typed, or
 * from a `dana://pair` link), whether a pairing is running, the last error.
 * [requested]: the screen is open although the phone is paired ("Pair again",
 * a link opened while paired).
 */
data class PairingState(
    val server: String = "",
    val code: String = "",
    val busy: Boolean = false,
    val error: String? = null,
    val requested: Boolean = false,
)

/**
 * Pairs this phone with a Dana server (server docs/devices.md): the code from
 * the web Devices page goes to `POST /api/pair` with kind `phone` and
 * [deviceName]; the answer (the device's own token) becomes the app's
 * [ServerConfig] through [onPaired]. Pairing again replaces the old device,
 * which is then removed on its server; [unpair] removes this device and
 * forgets the server ([onUnpaired]). [current] is the config in use.
 */
class PairingController(
    private val scope: CoroutineScope,
    private val deviceName: String,
    private val current: () -> ServerConfig,
    private val onPaired: (ServerConfig) -> Unit,
    private val onUnpaired: () -> Unit,
    private val notify: (String) -> Unit,
    private val apiFor: (server: String, token: String) -> ApiService = ::ApiService,
) {
    private val _state = MutableStateFlow(PairingState())
    val state: StateFlow<PairingState> = _state.asStateFlow()

    fun setServer(value: String) = _state.update { it.copy(server = value, error = null) }

    fun setCode(value: String) = _state.update { it.copy(code = value.uppercase().take(12), error = null) }

    /**
     * A `dana://pair` link (the QR code, opened by a camera app): fills the
     * form; pairs at once when the phone isn't paired yet. Returns false when
     * [uri] isn't a pairing link.
     */
    fun openLink(uri: String?): Boolean {
        val link = PairLink.parse(uri) ?: return false
        _state.update { it.copy(server = link.server, code = link.code, error = null, requested = true) }
        if (!current().isConfigured) pair()
        return true
    }

    /** Settings → "Pair again": the form, with the server in use filled in. */
    fun pairAgain() = _state.update { PairingState(server = current().url, requested = true) }

    /** Close the form opened while paired. */
    fun close() = _state.update { PairingState() }

    /** Pair with the form's server and code. */
    fun pair(): Job? {
        val form = _state.value
        if (form.busy) return null
        val server = ServerConfig.normalize(form.server)
        val code = normalizePairCode(form.code)
        val problem = when {
            server.isEmpty() -> "Type your Dana server's address."
            !isPairCodeShape(code) -> "A pairing code has 8 letters and digits, like K7QM 4XPZ."
            else -> null
        }
        if (problem != null) {
            _state.update { it.copy(error = problem) }
            return null
        }
        _state.update { it.copy(busy = true, error = null) }
        return scope.launch {
            val before = current()
            val outcome = try {
                Result.success(apiFor(server, "").pairing.pair(code, "phone", deviceName))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Result.failure(e)
            }
            outcome.onSuccess { answer ->
                val config = ServerConfig.paired(server, answer)
                onPaired(config)
                _state.value = PairingState()
                if (before.isConfigured && before.deviceId.isNotEmpty() && before.deviceId != config.deviceId) {
                    revokeQuietly(before)
                }
            }.onFailure { e ->
                _state.update { it.copy(busy = false, error = failureText(e, server)) }
            }
        }
    }

    /**
     * Settings → "Unpair": remove this device on its server (its token stops
     * working), then forget the server. Unpairs even when the server can't be
     * reached, and says the device is still listed then.
     */
    fun unpair(): Job = scope.launch {
        val config = current()
        val removed = config.deviceId.isEmpty() || revoke(config)
        onUnpaired()
        _state.value = PairingState()
        if (!removed) notify("Unpaired. The server couldn't be reached: remove this phone on Dana's Devices page.")
    }

    /** Remove [config]'s device with its own token; true when it is gone (or was already). */
    private suspend fun revoke(config: ServerConfig): Boolean = try {
        apiFor(config.url, config.token).pairing.revoke(config.deviceId).let { it in 200..299 || it == 401 || it == 404 }
    } catch (e: CancellationException) {
        throw e
    } catch (_: Exception) {
        false
    }

    private fun revokeQuietly(config: ServerConfig) {
        scope.launch {
            if (!revoke(config)) notify("Paired. The old pairing couldn't be removed: remove it on Dana's Devices page.")
        }
    }

    private fun failureText(e: Throwable, server: String): String = when (e) {
        is PairingException -> e.message ?: "Couldn't pair."
        is IOException -> "Couldn't reach a Dana server at $server. Check the address, and that this phone can reach it (Wi-Fi, VPN)."
        is IllegalArgumentException -> "That isn't a server address."
        else -> "Couldn't pair: ${e.message ?: e.javaClass.simpleName}"
    }
}
