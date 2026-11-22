package io.github.ronganam.dana.protocol

import kotlinx.serialization.Serializable
import java.net.URI
import java.net.URLDecoder

/**
 * Pairing an app with a Dana server (server docs/devices.md): the pure parts
 * every app shares (the phone, kiosk and TV in `:app`, the watch in `:wear`).
 * A code ([PairingCode]) goes to `POST /api/pair` as a [PairRequest]; the
 * answer is a [PairAnswer] or a refusal read as a [PairError]. The phone also
 * reads a [PairLink] from the web Devices page's QR code, and asks for the
 * watch's code over the Data Layer ([WearPairRequest] → [WearPairOffer]).
 */

/** The 8-character pairing codes the server draws (`devices.py`: no 0/O, 1/I). */
object PairingCode {
    const val LENGTH = 8
    const val ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

    /** What was typed, upper case, without spaces or dashes ("k7qm-4xpz" → "K7QM4XPZ"). */
    fun normalize(raw: String): String = raw.uppercase().filter { !it.isWhitespace() && it != '-' }

    /** Eight characters of the alphabet. */
    fun isComplete(code: String): Boolean = code.length == LENGTH && code.all { it in ALPHABET }

    /** "K7QM4XPZ" → "K7QM 4XPZ" for showing. */
    fun grouped(code: String): String = if (code.length > 4) code.take(4) + " " + code.drop(4) else code
}

/** A server address and a code, from a `dana://pair?server=…&code=…` link (the QR code). */
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
            val code = PairingCode.normalize(params["code"].orEmpty())
            return if (server.isEmpty() || code.isEmpty()) null else PairLink(server, code)
        }

        private fun decode(s: String): String = runCatching { URLDecoder.decode(s, "UTF-8") }.getOrDefault(s)
    }
}

/** `POST /api/pair`'s body. */
@Serializable
data class PairRequest(val code: String, val kind: String, val name: String)

/** The device a pairing made (`device` in [PairAnswer]). */
@Serializable
data class PairAnswerDevice(
    val id: String = "",
    val name: String = "",
    val kind: String = "",
    val room: String = "",
    val role: String = "",
)

/** Whose device it is (`user` in [PairAnswer]). */
@Serializable
data class PairAnswerUser(val id: String = "", val name: String = "")

/** The server's version (`server` in [PairAnswer]). */
@Serializable
data class PairAnswerServer(val version: String = "")

/** `POST /api/pair`'s answer: the new device's token (shown once), the device, its user, the server. */
@Serializable
data class PairAnswer(
    val token: String = "",
    val device: PairAnswerDevice = PairAnswerDevice(),
    val user: PairAnswerUser = PairAnswerUser(),
    val server: PairAnswerServer = PairAnswerServer(),
) {
    companion object {
        /** The answer in [text]; null when it has no token. */
        fun decode(text: String): PairAnswer? = serializer().decodeOrNull(text.ifBlank { "{}" })?.takeIf { it.token.isNotBlank() }
    }
}

/** Why a pairing failed; [message] is plain words for the screen. */
sealed interface PairError {
    val message: String

    data object InvalidCode : PairError {
        override val message = "That code isn't valid. Check it, or make a new one on Dana's Devices page."
    }
    data class WrongKind(val expected: String) : PairError {
        override val message get() = "That code is for a ${expected.ifBlank { "different" }} device. Make a code for this kind of device."
    }
    data class RateLimited(val retryAfterSeconds: Long?) : PairError {
        override val message get() = retryAfterSeconds?.let { s ->
            "Too many wrong codes. Try again in ${if (s >= 90) "${(s + 59) / 60} minutes" else "$s seconds"}."
        } ?: "Too many wrong codes. Try again in a few minutes."
    }
    data class Invalid(val detail: String) : PairError {
        override val message get() = detail.ifBlank { "The server refused the request." }
    }
    data class Unreachable(val detail: String) : PairError {
        override val message get() = "Can't reach the server${if (detail.isNotBlank()) " ($detail)" else ""}. Check the address."
    }
    data class Http(val code: Int) : PairError {
        override val message get() = "The server answered HTTP $code. Is this a Dana server?"
    }

    companion object {
        /** A refused `POST /api/pair`: its [status], the JSON [body] (`error`, `expected`, `detail`) and `Retry-After`. */
        fun from(status: Int, body: String, retryAfter: String?): PairError {
            val wire = ErrorWire.serializer().decodeOrNull(body.ifBlank { "{}" }) ?: ErrorWire()
            return when {
                status == 429 -> RateLimited(retryAfter?.trim()?.toLongOrNull())
                wire.error == "invalid_code" -> InvalidCode
                wire.error == "wrong_kind" -> WrongKind(wire.expected)
                wire.error == "invalid" -> Invalid(wire.detail)
                else -> Http(status)
            }
        }
    }
}

@Serializable
private data class ErrorWire(val error: String = "", val expected: String = "", val detail: String = "")

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
