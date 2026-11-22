package io.github.ronganam.dana.ui.viewmodels

import io.github.ronganam.dana.data.network.ServerConfig
import io.github.ronganam.dana.data.pairing.PairOutcome
import io.github.ronganam.dana.data.pairing.PairedDevice
import io.github.ronganam.dana.data.pairing.PairingFlow
import io.github.ronganam.dana.protocol.PairLink
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * The phone's pairing (android docs/phone.md → Pairing) over the shared
 * [PairingFlow] (the form, discovery, the call with kind `phone`):
 * a `dana://pair` link from the Devices page's QR code fills it and pairs an
 * unpaired phone at once; [requested] keeps the screen open while paired
 * ("Pair again", a link). Pairing again removes the old device on its server
 * afterwards; [unpair] removes this one and forgets it.
 *
 * The Android parts come in as functions: [pair] is `PairingClient.pair`,
 * [store] `DevicePairing.store`, [device] the kept device, [remove]
 * `PairingClient.remove`, [forget] `DevicePairing.unpair` (true when the
 * server was told); [reload] points the app at the config now saved.
 */
class PairingController(
    private val scope: CoroutineScope,
    private val current: () -> ServerConfig,
    private val device: () -> PairedDevice?,
    pair: suspend (server: String, code: String) -> PairOutcome,
    private val store: (server: String, paired: PairOutcome.Paired) -> ServerConfig,
    private val remove: suspend (config: ServerConfig, deviceId: String) -> Boolean,
    private val forget: suspend () -> Boolean,
    private val reload: () -> Unit,
    private val notify: (String) -> Unit,
) {
    /** The form: server, code, servers found on this network, busy, error. */
    val flow = PairingFlow(scope, current().url, pair) { server, paired -> paired(server, paired) }

    private val _requested = MutableStateFlow(false)
    /** The pairing screen is open although the phone is paired. */
    val requested: StateFlow<Boolean> = _requested.asStateFlow()

    /**
     * A `dana://pair` link (the QR code, opened by a camera app): fills the
     * form; pairs at once when the phone isn't paired yet. False when [uri]
     * isn't a pairing link.
     */
    fun openLink(uri: String?): Boolean {
        val link = PairLink.parse(uri) ?: return false
        flow.setServer(link.server)
        flow.setCode(link.code)
        _requested.value = true
        if (!current().isConfigured) flow.submit()
        return true
    }

    /** Settings → "Pair again": the form, with the server in use filled in. */
    fun pairAgain() {
        flow.setServer(current().url)
        flow.setCode("")
        _requested.value = true
    }

    /** Close the form opened while paired. */
    fun close() {
        _requested.value = false
    }

    private fun paired(server: String, paired: PairOutcome.Paired) {
        val before = current()
        val old = device()
        store(server, paired)
        reload()
        _requested.value = false
        if (before.isConfigured && old != null && old.id.isNotBlank() && old.id != paired.device.id) {
            scope.launch {
                val gone = try {
                    remove(before, old.id)
                } catch (e: CancellationException) {
                    throw e
                } catch (_: Exception) {
                    false
                }
                if (!gone) notify("Paired. The old pairing couldn't be removed: remove it on Dana's Devices page.")
            }
        }
    }

    /**
     * Settings → "Unpair": remove this device on its server (its token stops
     * working), then forget it. Unpairs even when the server can't be reached,
     * and says the device is still listed then.
     */
    fun unpair(): Job = scope.launch {
        val told = forget()
        reload()
        _requested.value = false
        flow.setCode("")
        if (!told) notify("Unpaired. The server couldn't be reached: remove this phone on Dana's Devices page.")
    }
}
