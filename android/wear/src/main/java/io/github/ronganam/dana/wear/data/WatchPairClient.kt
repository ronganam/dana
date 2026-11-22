package io.github.ronganam.dana.wear.data

import io.github.ronganam.dana.protocol.PairAnswer
import io.github.ronganam.dana.protocol.PairRequest
import io.github.ronganam.dana.protocol.PairError
import io.github.ronganam.dana.protocol.WearJson
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/** What redeeming a code gave: the watch's device and token, or a sentence to show. */
sealed interface PairOutcome {
    data class Paired(val server: String, val answer: PairAnswer) : PairOutcome
    data class Refused(val message: String) : PairOutcome
}

/**
 * `POST /api/pair {code, kind: "watch", name}` from the watch itself (the
 * watch has no OkHttp; one request doesn't need it). Pure JVM, so the unit
 * tests run it against a local HTTP server.
 */
object WatchPairClient {
    private const val TIMEOUT_MS = 15_000

    /**
     * Redeem [code] at [server] for a device of kind `watch` named [name].
     *
     * @throws IOException the server couldn't be reached
     */
    fun redeem(server: String, code: String, name: String): PairOutcome {
        val base = normalizeServer(server)
        if (base.isEmpty()) return PairOutcome.Refused("Type your Dana server's address.")
        val connection = try {
            URL("$base/api/pair").openConnection() as HttpURLConnection
        } catch (_: java.net.MalformedURLException) {
            return PairOutcome.Refused("That isn't a server address.")
        }
        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = TIMEOUT_MS
            connection.readTimeout = TIMEOUT_MS
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            val body = WearJson.encodeToString(PairRequest.serializer(), PairRequest(code, "watch", name.take(60)))
            connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            val status = connection.responseCode
            val text = (if (status in 200..299) connection.inputStream else connection.errorStream)
                ?.use { it.readBytes().toString(Charsets.UTF_8) }.orEmpty()
            if (status in 200..299) {
                val answer = PairAnswer.decode(text) ?: return PairOutcome.Refused("The server's answer had no token.")
                return PairOutcome.Paired(base, answer)
            }
            return PairOutcome.Refused(PairError.from(status, text, connection.getHeaderField("Retry-After")).message)
        } finally {
            connection.disconnect()
        }
    }

    /** "dana.example.com/" → "https://dana.example.com"; an explicit http:// stays; blank → "". */
    fun normalizeServer(raw: String): String {
        var s = raw.trim()
        if (s.isEmpty()) return ""
        if (!s.contains("://")) s = "https://$s"
        s = s.trimEnd('/')
        return if (s.substringAfter("://", "").isEmpty()) "" else s
    }
}
