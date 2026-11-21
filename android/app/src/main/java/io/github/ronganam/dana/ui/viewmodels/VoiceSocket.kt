package io.github.ronganam.dana.ui.viewmodels

import android.util.Log
import io.github.ronganam.dana.data.network.ChatApi
import io.github.ronganam.dana.data.network.HttpClients
import io.github.ronganam.dana.data.network.RunEvent
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString.Companion.toByteString

/**
 * One push-to-talk utterance over `/api/assistant/voice_stream`: PCM goes up,
 * the run's events (the same as the SSE stream, plus `no_speech`) come down.
 * Frames and failures of a socket that was already replaced are dropped.
 */
internal class VoiceSocket(
    private val onEvent: (RunEvent) -> Unit,
    private val onFailure: (Throwable) -> Unit
) {
    @Volatile private var socket: WebSocket? = null

    val isOpen: Boolean get() = socket != null

    fun open(request: Request) {
        lateinit var opened: WebSocket
        opened = HttpClients.webSocket.newWebSocket(request, object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                // OkHttp delivers frames in order on one thread; handle them
                // synchronously so the run's events keep that order.
                if (socket !== webSocket) return
                ChatApi.parseEvent(text)?.let(onEvent)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.e(TAG, "Voice WebSocket failure: ${t::class.simpleName}: ${t.message}", t)
                // Ignore late failures from a socket we already moved on from.
                if (socket !== webSocket) return
                socket = null
                onFailure(t)
            }
        })
        socket = opened
    }

    /** The current socket, to tell whether a later check still concerns the same utterance. */
    fun current(): Any? = socket

    fun send(chunk: ByteArray) {
        socket?.send(chunk.toByteString())
    }

    fun send(text: String) {
        socket?.send(text)
    }

    fun close(reason: String) {
        try {
            socket?.close(1000, reason)
        } catch (e: Exception) {
            Log.w(TAG, "Error closing voice socket", e)
        }
        socket = null
    }

    fun cancel() {
        socket?.cancel()
        socket = null
    }

    companion object {
        private const val TAG = "VoiceSocket"
    }
}
