package io.github.ronganam.dana.data.pairing

import io.github.ronganam.dana.data.discovery.DiscoveredServer
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** What a pairing screen shows. */
data class PairingState(
    val server: String = "",
    val code: String = "",
    /** Servers found on the home network (several → the screen lets the user pick). */
    val found: List<DiscoveredServer> = emptyList(),
    /** The address was typed or picked by the user, so discovery no longer fills it. */
    val serverChosen: Boolean = false,
    val busy: Boolean = false,
    val error: String? = null,
    val paired: PairedDevice? = null,
) {
    val canSubmit: Boolean get() = !busy && server.isNotBlank() && PairingCode.isComplete(code)
}

/**
 * The first-run pairing of a kiosk tablet or a TV (and anything else that
 * pairs with a typed code): the server address (typed, or filled from
 * discovery), the code, and the call. [pair] is `PairingClient.pair` bound to
 * this device's kind and name; [onPaired] keeps the result
 * ([DevicePairing.store]) and moves on.
 */
class PairingFlow(
    private val scope: CoroutineScope,
    initialServer: String,
    private val pair: suspend (server: String, code: String) -> PairOutcome,
    private val onPaired: (server: String, paired: PairOutcome.Paired) -> Unit,
) {
    private val _state = MutableStateFlow(PairingState(server = initialServer, serverChosen = initialServer.isNotBlank()))
    val state: StateFlow<PairingState> = _state.asStateFlow()

    fun setServer(value: String) = _state.update { it.copy(server = value.trim(), serverChosen = true, error = null) }

    /** A found server picked from the list. */
    fun pick(server: DiscoveredServer) = setServer(server.url)

    /** Typed or pressed characters: normalized, letters the code never uses dropped, at most 8. */
    fun setCode(value: String) = _state.update {
        it.copy(code = PairingCode.normalize(value).filter { c -> c in PairingCode.ALPHABET }.take(PairingCode.LENGTH), error = null)
    }

    /** One character from an on-screen keypad. */
    fun type(c: Char) = setCode(_state.value.code + c)

    fun backspace() = _state.update { it.copy(code = it.code.dropLast(1), error = null) }

    /**
     * What discovery sees now. While the user has not typed or picked an
     * address, one server fills it, and none or several leave it empty for a pick.
     */
    fun discovered(servers: List<DiscoveredServer>) = _state.update { s ->
        when {
            s.serverChosen -> s.copy(found = servers)
            servers.size == 1 -> s.copy(found = servers, server = servers.single().url)
            else -> s.copy(found = servers, server = if (servers.any { it.url == s.server }) s.server else "")
        }
    }

    fun submit() {
        val s = _state.value
        val error = when {
            s.busy -> return
            s.server.isBlank() -> "Enter the server's address, or pick one found on this network."
            !PairingCode.isComplete(s.code) -> "The code has ${PairingCode.LENGTH} letters and digits."
            else -> null
        }
        if (error != null) { _state.update { it.copy(error = error) }; return }
        _state.update { it.copy(busy = true, error = null) }
        scope.launch {
            when (val outcome = pair(s.server, s.code)) {
                is PairOutcome.Paired -> {
                    onPaired(s.server, outcome)
                    _state.update { it.copy(busy = false, paired = outcome.device, code = "") }
                }
                is PairOutcome.Failed -> _state.update {
                    it.copy(busy = false, error = outcome.error.message,
                        code = if (outcome.error is PairError.InvalidCode) "" else it.code)
                }
            }
        }
    }
}
