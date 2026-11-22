package io.github.ronganam.dana.protocol

import kotlinx.serialization.Serializable
import java.net.URI
import java.net.URLDecoder

/**
 * Pairing an app with a Dana server (server docs/devices.md): what the phone
 * and the watch both need to turn a code into their own device token. The
 * phone reads a [PairLink] from the web Devices page's QR code (or a typed
 * address + code), and both apps send `POST /api/pair {code, kind, name}` and
 * read a [PairAnswer]; a refusal becomes a sentence with [pairFailureMessage].
 */

/** The characters a pairing code is drawn from (no 0/O, 1/I), as the server draws them. */
const val PAIR_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

/** A pairing code's length. */
const val PAIR_CODE_LENGTH = 8

/** A server address and a code, from a `dana://pair?server=…&code=…` link (the QR code) or typed. */
data class PairLink(val server: String, val code: String) {
    companion object {
        /** The link in [text] when it is a `dana://pair` link with both parts; else null. */
        fun parse(text: String?): PairLink? {
            val uri = runCatching { URI(text?.trim().orEmpty()) }.getOrNull() ?: return null
            if (!uri.scheme.equals("dana", ignoreCase = true) || !uri.host.equals("pair", ignoreCase = true)) return null
            val params = (uri.rawQuery ?: "").split('&').mapNotNull { part ->
                val eq = part.indexOf('=')
                if (eq <= 0) null else decode(part.substring(0, eq)) to decode(part.substring(eq + 1))
            }.toMap()
            val server = params["server"]?.trim().orEmpty()
            val code = normalizePairCode(params["code"].orEmpty())
            return if (server.isEmpty() || code.isEmpty()) null else PairLink(server, code)
        }

        private fun decode(s: String): String = runCatching { URLDecoder.decode(s, "UTF-8") }.getOrDefault(s)
    }
}

/**
 * A typed code as the server matches it: upper case, spaces and dashes
 * dropped ("k7qm-4xpz" → "K7QM4XPZ"). Any other character is kept, so a
 * wrong one is still sent and refused by the server, not silently changed.
 */
fun normalizePairCode(raw: String): String = raw.uppercase().filterNot { it == ' ' || it == '-' }

/** Whether [code] (normalized) has the shape of a pairing code: 8 characters of [PAIR_CODE_ALPHABET]. */
fun isPairCodeShape(code: String): Boolean =
    code.length == PAIR_CODE_LENGTH && code.all { it in PAIR_CODE_ALPHABET }

/** `POST /api/pair`'s body. */
@Serializable
data class PairRequest(val code: String, val kind: String, val name: String)

/** The device a pairing made (`device` in [PairAnswer]). */
@Serializable
data class PairDevice(val id: String = "", val name: String = "", val kind: String = "")

/** Whose device it is (`user` in [PairAnswer]). */
@Serializable
data class PairUser(val id: String = "", val name: String = "")

/** `POST /api/pair`'s answer: the new device's token (shown once), the device and its user. */
@Serializable
data class PairAnswer(val token: String = "", val device: PairDevice = PairDevice(), val user: PairUser = PairUser()) {
    fun encode(): String = WearJson.encodeToString(serializer(), this)

    companion object {
        /** The answer in [text]; null when it has no token. */
        fun decode(text: String): PairAnswer? = serializer().decodeOrNull(text)?.takeIf { it.token.isNotBlank() }
    }
}

/**
 * The sentence to show for a refused pairing: [status] and the server's
 * `error` code ([error]), [retryAfterSeconds] from a 429's `Retry-After`.
 */
fun pairFailureMessage(status: Int, error: String, retryAfterSeconds: Long? = null): String = when {
    error == "invalid_code" -> "That code isn't right, or it has expired. Codes last five minutes; make a new one on the Devices page."
    error == "wrong_kind" -> "That code was made for another kind of device. Make a new one for this device on the Devices page."
    error == "rate_limited" || status == 429 -> {
        val wait = retryAfterSeconds?.takeIf { it > 0 }
        if (wait == null) "Too many wrong codes. Wait a few minutes and try again."
        else "Too many wrong codes. Try again in ${waitText(wait)}."
    }
    error == "setup_required" -> "This Dana server isn't set up yet. Open the setup link from its logs first."
    error == "invalid" -> "The server refused the request. Make sure the app is up to date."
    status == 404 -> "There's no Dana server at that address."
    else -> "Couldn't pair (HTTP $status)."
}

private fun waitText(seconds: Long): String {
    if (seconds < 60) return "$seconds seconds"
    val minutes = (seconds + 59) / 60
    return if (minutes == 1L) "a minute" else "$minutes minutes"
}

/** [WearPaths.PAIR_WATCH]: the watch asks the phone for a pairing code; [name] is the watch's model. */
@Serializable
data class WearPairRequest(val name: String = "") {
    fun encode(): String = WearJson.encodeToString(serializer(), this)

    companion object {
        fun decode(text: String): WearPairRequest? = serializer().decodeOrNull(text)
    }
}

/**
 * The phone's answer to [WearPairRequest]: a code of kind `watch` for the
 * phone's [server] (the watch redeems it itself with `POST /api/pair`), or a
 * sentence in [error] (the phone isn't paired, the server refused).
 */
@Serializable
data class WearPairOffer(val server: String = "", val code: String = "", val error: String? = null) {
    fun encode(): String = WearJson.encodeToString(serializer(), this)

    companion object {
        fun decode(text: String): WearPairOffer? = serializer().decodeOrNull(text)
    }
}
