package io.github.ronganam.dana.wear.data

import android.content.Context
import androidx.core.net.toUri
import android.os.Build
import android.util.Log
import com.google.android.gms.wearable.MessageClient
import com.google.android.gms.wearable.Wearable
import io.github.ronganam.dana.protocol.PairAnswer
import io.github.ronganam.dana.protocol.WearPairOffer
import io.github.ronganam.dana.protocol.WearPairRequest
import io.github.ronganam.dana.protocol.WearPaths
import io.github.ronganam.dana.protocol.isPairCodeShape
import io.github.ronganam.dana.protocol.normalizePairCode
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.io.IOException

/** Where the watch's own pairing stands (docs/wear.md → Pairing). */
sealed interface WatchPairState {
    /** Paired: its own device [deviceName] on [server]. */
    data class Paired(val server: String, val deviceName: String) : WatchPairState
    /** Asking the phone for a code, or redeeming one. */
    data object Working : WatchPairState
    /** Not paired and the phone couldn't help: the code entry ([reason]: why, null when nothing was tried). */
    data class NeedsCode(val reason: NeedsCodeReason?, val message: String? = null) : WatchPairState
}

/** Why the watch shows the code entry. */
enum class NeedsCodeReason { NO_PHONE, PHONE_SILENT, PHONE_REFUSED, SERVER_REFUSED, UNREACHABLE, BAD_CODE }

/**
 * The pairing steps without Android: [askPhone] sends a [WearPairRequest] and
 * waits for the phone's [WearPairOffer] (null: no phone, or no answer in
 * time; [phoneConnected] tells which), [redeem] is `POST /api/pair`, [save]
 * keeps what it gave. Unit-tested (WatchPairingFlowTest).
 */
class WatchPairingFlow(
    private val name: String,
    private val phoneConnected: suspend () -> Boolean,
    private val askPhone: suspend (WearPairRequest) -> WearPairOffer?,
    private val redeem: (server: String, code: String, name: String) -> PairOutcome,
    private val save: (server: String, answer: PairAnswer) -> Unit,
) {
    /** Ask the phone for a code of kind `watch` and redeem it here. */
    suspend fun throughPhone(): WatchPairState {
        if (!phoneConnected()) return WatchPairState.NeedsCode(NeedsCodeReason.NO_PHONE)
        val offer = askPhone(WearPairRequest(name)) ?: return WatchPairState.NeedsCode(NeedsCodeReason.PHONE_SILENT)
        offer.error?.let { return WatchPairState.NeedsCode(NeedsCodeReason.PHONE_REFUSED, it) }
        if (offer.server.isBlank() || offer.code.isBlank()) return WatchPairState.NeedsCode(NeedsCodeReason.PHONE_SILENT)
        return withCode(offer.server, offer.code)
    }

    /** Redeem a code typed on the watch (or offered by the phone). */
    fun withCode(server: String, code: String): WatchPairState {
        val clean = normalizePairCode(code)
        if (!isPairCodeShape(clean)) return WatchPairState.NeedsCode(NeedsCodeReason.BAD_CODE)
        return try {
            when (val outcome = redeem(server, clean, name)) {
                is PairOutcome.Paired -> {
                    save(outcome.server, outcome.answer)
                    WatchPairState.Paired(outcome.server, outcome.answer.device.name)
                }
                is PairOutcome.Refused -> WatchPairState.NeedsCode(NeedsCodeReason.SERVER_REFUSED, outcome.message)
            }
        } catch (_: IOException) {
            WatchPairState.NeedsCode(NeedsCodeReason.UNREACHABLE)
        }
    }
}

/**
 * The watch's own device on its user's server: the phone starts a pairing of
 * kind `watch` ([WearPaths.PAIR_WATCH]) and the watch redeems the code, so it
 * is listed and revocable on the web Devices page. Without a phone the watch
 * takes a code typed on it. Runs again when the phone's server changes
 * ([ensure] from the config listener).
 */
object WatchPairing {
    private const val TAG = "WatchPairing"
    private const val PHONE_ANSWER_MS = 20_000L

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val running = Mutex()
    private val _state = MutableStateFlow<WatchPairState>(WatchPairState.NeedsCode(null))
    val state: StateFlow<WatchPairState> = _state.asStateFlow()

    /** The state from what is stored (call when a screen opens). */
    fun load(context: Context) {
        if (_state.value == WatchPairState.Working) return
        val prefs = WearPrefs(context)
        if (prefs.isPaired()) _state.value = WatchPairState.Paired(prefs.serverUrl, prefs.deviceName)
    }

    /**
     * Pair through the phone unless the watch is already paired with the
     * phone's server (or the phone hasn't said its server yet and the watch is paired).
     */
    fun ensure(context: Context) {
        val prefs = WearPrefs(context)
        load(context)
        val phoneServer = prefs.phoneServerUrl
        if (prefs.isPaired() && (phoneServer.isEmpty() || phoneServer == prefs.serverUrl)) return
        pairThroughPhone(context)
    }

    /** Ask the phone for a code (the Settings / pairing screen's "Ask the phone"). */
    fun pairThroughPhone(context: Context) = run(context) { it.throughPhone() }

    /** Redeem a code typed on the watch. */
    fun pairWithCode(context: Context, server: String, code: String) = run(context) { it.withCode(server, code) }

    private fun run(context: Context, step: suspend (WatchPairingFlow) -> WatchPairState) {
        val app = context.applicationContext
        scope.launch {
            if (!running.tryLock()) return@launch
            try {
                _state.value = WatchPairState.Working
                val result = step(flow(app))
                _state.value = result
                Log.i(TAG, "Pairing: ${result::class.simpleName}")
            } finally {
                running.unlock()
            }
        }
    }

    private fun flow(app: Context): WatchPairingFlow {
        val prefs = WearPrefs(app)
        return WatchPairingFlow(
            name = Build.MODEL.orEmpty().ifBlank { "Watch" },
            phoneConnected = { PhoneNode.await(app) != null },
            askPhone = { request -> askPhone(app, request) },
            redeem = WatchPairClient::redeem,
            save = prefs::savePairing,
        )
    }

    /** [WearPaths.PAIR_WATCH] to the phone; its [WearPairOffer] on the response path, or null after [PHONE_ANSWER_MS]. */
    private suspend fun askPhone(app: Context, request: WearPairRequest): WearPairOffer? {
        val node = PhoneNode.await(app) ?: return null
        val answer = CompletableDeferred<WearPairOffer?>()
        val client = Wearable.getMessageClient(app)
        val listener = MessageClient.OnMessageReceivedListener { event ->
            if (event.path == WearPaths.response(WearPaths.PAIR_WATCH)) answer.complete(WearPairOffer.decode(String(event.data)))
        }
        val path = "wear://*${WearPaths.response(WearPaths.PAIR_WATCH)}".toUri()
        return try {
            withContext(Dispatchers.Main) { client.addListener(listener, path, MessageClient.FILTER_LITERAL) }
            client.sendMessage(node, WearPaths.PAIR_WATCH, request.encode().toByteArray())
                .addOnFailureListener { PhoneNode.invalidate(); answer.complete(null) }
            withTimeoutOrNull(PHONE_ANSWER_MS) { answer.await() }
        } finally {
            client.removeListener(listener)
        }
    }
}
