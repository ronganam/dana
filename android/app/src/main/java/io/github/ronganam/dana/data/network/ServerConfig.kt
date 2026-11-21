package io.github.ronganam.dana.data.network

import android.content.Context
import android.content.SharedPreferences
import io.github.ronganam.dana.BuildConfig

/**
 * The Dana server this app talks to and its API token — the one place that
 * reads or writes them. [url] is normalized ([normalize]): a scheme, no
 * trailing slash, "" when not set. Both live in the app's preferences file
 * ([prefs]), which also holds small flags (never transcripts).
 *
 * Every flavor uses it: the phone's Settings, the kiosk and TV settings, and
 * background code (workers, receivers, services) through [load] → [api].
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

        /** The saved server, else the build's default (`SERVER_URL` in your site's android.env). */
        fun load(context: Context): ServerConfig {
            val p = prefs(context)
            val url = normalize(p.getString(KEY_URL, "").orEmpty()).ifEmpty { normalize(BuildConfig.DEFAULT_SERVER_URL) }
            return ServerConfig(url, p.getString(KEY_TOKEN, "").orEmpty().trim())
        }

        /** Save a server and token as typed in a settings screen; returns what was stored. */
        fun save(context: Context, url: String, token: String): ServerConfig {
            val config = ServerConfig(normalize(url), token.trim())
            prefs(context).edit().putString(KEY_URL, config.url).putString(KEY_TOKEN, config.token).apply()
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
