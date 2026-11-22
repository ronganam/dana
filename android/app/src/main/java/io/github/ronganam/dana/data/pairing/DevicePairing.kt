package io.github.ronganam.dana.data.pairing

import android.content.Context
import android.os.Build
import android.util.Log
import androidx.core.content.edit
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.ServerConfig

/**
 * This app's paired device: after a pairing, the server and token go to
 * [ServerConfig] and the device (name, room, role, whose) is kept here, in the
 * same preferences file, so settings can show it and Unpair can remove it.
 */
object DevicePairing {
    private const val TAG = "DevicePairing"
    private const val KEY_DEVICE = "paired_device"

    /** The name a new device offers the server (the Devices page can rename it). */
    fun deviceName(): String = listOf(Build.MANUFACTURER, Build.MODEL)
        .filter { it.isNotBlank() }
        .let { parts -> if (parts.size == 2 && parts[1].startsWith(parts[0], ignoreCase = true)) listOf(parts[1]) else parts }
        .joinToString(" ")
        .ifBlank { "Android" }

    /** The paired device, or null when this app is not paired. */
    fun device(context: Context): PairedDevice? =
        ServerConfig.prefs(context).getString(KEY_DEVICE, null)?.let { text ->
            runCatching { ApiJson.decodeFromString(PairedDevice.serializer(), text) }.getOrNull()
        }

    /** Keep a successful pairing: [serverUrl] + its token in [ServerConfig], the device here. */
    fun store(context: Context, serverUrl: String, paired: PairOutcome.Paired): ServerConfig {
        val config = ServerConfig.save(context, serverUrl, paired.token)
        remember(context, paired.device)
        return config
    }

    /** Replace the kept device (e.g. after reading its room / role back from the server). */
    fun remember(context: Context, device: PairedDevice) {
        ServerConfig.prefs(context).edit { putString(KEY_DEVICE, ApiJson.encodeToString(PairedDevice.serializer(), device)) }
    }

    /** Read this device back from the server (its room or role may have changed) and keep it; null when that fails. */
    suspend fun refresh(context: Context, client: PairingClient = PairingClient()): PairedDevice? {
        val config = ServerConfig.load(context)
        if (!config.isConfigured) return null
        val known = device(context)
        val fresh = runCatching { client.current(config, known?.userName.orEmpty()) }
            .onFailure { Log.w(TAG, "Couldn't read this device back: ${it.message}") }
            .getOrNull() ?: return null
        remember(context, fresh)
        return fresh
    }

    /**
     * Unpair: remove this device on the server with its own token (best effort:
     * an unreachable server still forgets it here), then drop the token and the
     * device. The server address stays, so pairing again starts from it.
     */
    suspend fun unpair(context: Context, client: PairingClient = PairingClient()) {
        val config = ServerConfig.load(context)
        val id = device(context)?.id
        if (config.isConfigured && !id.isNullOrBlank()) {
            runCatching { client.remove(config, id) }
                .onFailure { Log.w(TAG, "Couldn't remove this device on the server: ${it.message}") }
        }
        ServerConfig.save(context, config.url, "")
        ServerConfig.prefs(context).edit { remove(KEY_DEVICE) }
    }
}
