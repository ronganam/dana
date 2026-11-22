package io.github.ronganam.dana.data.wear

import io.github.ronganam.dana.data.network.ApiException
import io.github.ronganam.dana.data.network.ServerConfig
import io.github.ronganam.dana.data.pairing.PairingClient
import io.github.ronganam.dana.protocol.WearPairOffer
import io.github.ronganam.dana.protocol.WearPairRequest
import kotlinx.coroutines.CancellationException
import java.io.IOException

/**
 * The phone's half of pairing the watch ([io.github.ronganam.dana.protocol.WearPaths.PAIR_WATCH]):
 * with its own token the phone starts a pairing of kind `watch` for its user
 * (`POST /api/devices/pairings`), and hands the code and its server to the
 * watch, which redeems it itself, so the watch becomes its own device on the
 * Devices page. Never fails: a refusal is a sentence in [WearPairOffer.error].
 */
object WatchPairingOffer {
    suspend fun offer(config: ServerConfig, request: WearPairRequest, client: PairingClient = PairingClient()): WearPairOffer {
        if (!config.isConfigured) return WearPairOffer(error = "Pair the phone with Dana first.")
        return try {
            val code = client.startPairing(config, "watch", request.name)
            if (code.isBlank()) WearPairOffer(error = "The server sent no code.")
            else WearPairOffer(server = config.url, code = code)
        } catch (e: CancellationException) {
            throw e
        } catch (e: ApiException) {
            WearPairOffer(error = "The server refused: ${e.message}")
        } catch (_: IOException) {
            WearPairOffer(error = "The phone can't reach Dana right now.")
        }
    }
}
