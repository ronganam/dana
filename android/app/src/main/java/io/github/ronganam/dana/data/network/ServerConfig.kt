package io.github.ronganam.dana.data.network

import android.content.Context
import android.content.SharedPreferences
import androidx.core.content.edit
import io.github.ronganam.dana.protocol.PairAnswer

/**
 * The Dana server this app talks to and its own device token — the one place
 * that reads or writes them. [url] is normalized ([normalize]): a scheme, no
 * trailing slash, "" when not set. The phone gets both by pairing
 * ([savePaired]: [deviceId] / [deviceName] / [userName] are the device the
 * server made for it); there is no built-in server. They live in the app's
 * preferences file ([prefs]), which also holds small flags (never transcripts).
 *
 * Every flavor uses it: the phone's pairing screen and Settings, the kiosk and
 * TV settings, and background code (workers, receivers, services) through
 * [load] → [api].
 */
data class ServerConfig(
    val url: String,
    val token: String,
    val deviceId: String = "",
    val deviceName: String = "",
    val userName: String = "",
) {

    /** A server and a token are both set. */
    val isConfigured: Boolean get() = url.isNotEmpty() && token.isNotBlank()

    /** A client for this server (shares [HttpClients]' pool). */
    fun api(): ApiService = ApiService(url, token)

    companion object {
        private const val PREFS_FILE = "DanaPrefs"
        private const val KEY_URL = "server_url"
        private const val KEY_TOKEN = "api_token"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_DEVICE_NAME = "device_name"
        private const val KEY_USER_NAME = "user_name"

        /** The app's preferences file. */
        fun prefs(context: Context): SharedPreferences =
            context.getSharedPreferences(PREFS_FILE, Context.MODE_PRIVATE)

        /** The saved server and device ("" fields when this app isn't paired). */
        fun load(context: Context): ServerConfig {
            val p = prefs(context)
            return ServerConfig(
                url = normalize(p.getString(KEY_URL, "").orEmpty()),
                token = p.getString(KEY_TOKEN, "").orEmpty().trim(),
                deviceId = p.getString(KEY_DEVICE_ID, "").orEmpty(),
                deviceName = p.getString(KEY_DEVICE_NAME, "").orEmpty(),
                userName = p.getString(KEY_USER_NAME, "").orEmpty(),
            )
        }

        /** What a pairing with [url] gave ([answer]: the device's token, the device, its user). */
        fun paired(url: String, answer: PairAnswer): ServerConfig =
            ServerConfig(normalize(url), answer.token.trim(), answer.device.id, answer.device.name, answer.user.name)

        /** Store [config] (from [paired]) as this app's server and device; returns it. */
        fun savePaired(context: Context, config: ServerConfig): ServerConfig {
            prefs(context).edit {
                putString(KEY_URL, config.url)
                putString(KEY_TOKEN, config.token)
                putString(KEY_DEVICE_ID, config.deviceId)
                putString(KEY_DEVICE_NAME, config.deviceName)
                putString(KEY_USER_NAME, config.userName)
            }
            return config
        }

        /** Forget the server and the device (unpaired); returns the empty config. */
        fun clear(context: Context): ServerConfig {
            prefs(context).edit {
                listOf(KEY_URL, KEY_TOKEN, KEY_DEVICE_ID, KEY_DEVICE_NAME, KEY_USER_NAME).forEach(::remove)
            }
            return ServerConfig("", "")
        }

        /** Save a server and token typed into the kiosk / TV settings; returns what was stored. */
        fun save(context: Context, url: String, token: String): ServerConfig {
            val config = ServerConfig(normalize(url), token.trim())
            prefs(context).edit { putString(KEY_URL, config.url); putString(KEY_TOKEN, config.token) }
            return config
        }

        /**
         * "dana.example.com/" → "https://dana.example.com"; an explicit http://
         * stays (release builds only reach it if the build allows cleartext:
         * docs/release.md); blank or a bare scheme → "".
         */
        fun normalize(raw: String): String {
            var s = raw.trim()
            if (s.isEmpty()) return ""
            if (!s.contains("://")) s = "https://$s"
            s = s.trimEnd('/')
            return if (s.substringAfter("://", "").isEmpty()) "" else s
        }
    }
}
