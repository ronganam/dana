package io.github.ronganam.dana.data.network

import java.net.URLEncoder

/**
 * How a device's WebView (the kiosk's and the TV's dashboard) reaches a server
 * page signed in: it trades the device token at `POST /login` for the web
 * session cookie (server `core/auth.py`) once, then loads pages with the cookie.
 */
object WebSignIn {
    /** The web session cookie `POST /login` sets. */
    const val SESSION_COOKIE = "dana_session"

    /** What the WebView should load next. */
    sealed interface Load {
        /** A plain navigation to [url] (the session cookie is already there). */
        data class Get(val url: String) : Load

        /** `POST` [body] (form-encoded) to [url]: the token for a session cookie, then a redirect to the page. */
        data class Post(val url: String, val body: String) : Load
    }

    /** Where a page that landed on `/login` leaves the device. */
    enum class LoginLanding {
        /** Not the login page. */
        NONE,

        /** The session cookie went stale: forget it and sign in with the token again. */
        SIGN_IN_AGAIN,

        /** `/login` refused the token: the device was removed or its token revoked. */
        REFUSED,
    }

    /** True when [cookies] (`CookieManager.getCookie`) hold the session cookie. */
    fun hasSession(cookies: String?): Boolean =
        cookies?.split(';')?.any { it.trim().startsWith("$SESSION_COOKIE=") } == true

    /**
     * Load [path] (`/dashboard?zoom=98`) on [server] (normalized, no trailing slash):
     * straight away with a session cookie, else sign in with [token] first. A device
     * without a token just loads the page.
     */
    fun load(server: String, token: String, path: String, cookies: String?): Load =
        if (token.isNotEmpty() && !hasSession(cookies)) {
            val body = "token=" + URLEncoder.encode(token, "UTF-8") + "&next=" + URLEncoder.encode(path, "UTF-8")
            Load.Post("$server/login", body)
        } else {
            Load.Get(server + path)
        }

    /**
     * What it means that the WebView finished loading [url] on [server]: the login
     * page with `error=` is `/login` refusing the token; without it, the page asked
     * for a sign-in because the cookie is no longer valid.
     */
    fun landing(server: String, url: String): LoginLanding {
        if (!url.startsWith(server)) return LoginLanding.NONE
        val rest = url.removePrefix(server)
        val path = rest.substringBefore('?').substringBefore('#')
        if (path != "/login") return LoginLanding.NONE
        val query = rest.substringAfter('?', "").substringBefore('#')
        val refused = query.split('&').any { it.startsWith("error=") }
        return if (refused) LoginLanding.REFUSED else LoginLanding.SIGN_IN_AGAIN
    }
}
