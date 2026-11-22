package io.github.ronganam.dana.data.network

import android.content.Context
import android.content.SharedPreferences
import androidx.core.content.edit

/**
 * The Dana server this app talks to and its own device token — the one place
 * that reads or writes them. [url] is normalized ([normalize]): a scheme, no
 * trailing slash, "" when not set. Apps get both by pairing
 * (`data/pairing/DevicePairing`, which keeps the device next to them); no
 * server is built in. Both live in the app's preferences file ([prefs]),
 * which also holds small flags (never transcripts).
 *
 * Every flavor uses it: the pairing screens, settings, and background code
 * (workers, receivers, services) through [load] → [api].
 */
data class ServerConfig(val url: String, val token: String) {

    /** A server and a token are both set. */
    val isConfigured: Boolean get() = url.isNotEmpty() && token.isNotBlank()

    /** A client for this server (shares [HttpClients]' pool). */
    fun api(): ApiService = ApiService(url, token)

    companion object {
        private const val PREFS_FILE = "DanaPrefs"
        private const val KEY_URL = "server_url"
        private const val KEY_TOKEN = "api_token"

        /** The app's preferences file. */
        fun prefs(context: Context): SharedPreferences =
            context.getSharedPreferences(PREFS_FILE, Context.MODE_PRIVATE)

        /** The saved server and token ("" when this app isn't paired). */
        fun load(context: Context): ServerConfig {
            val p = prefs(context)
            return ServerConfig(normalize(p.getString(KEY_URL, "").orEmpty()), p.getString(KEY_TOKEN, "").orEmpty().trim())
        }

        /** Save a server and token (a pairing's: [io.github.ronganam.dana.data.pairing.DevicePairing.store]); returns what was stored. */
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
