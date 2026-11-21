package app.ganam.assistant.data.network

import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit

/**
 * Process-wide OkHttp clients.
 *
 * Every client derives from [base] via `newBuilder()`, so they share one
 * connection pool and dispatcher. Each `ApiService` used to build its own
 * client, and a fresh one on every server-config change.
 *
 * [base] carries [ServerStatusInterceptor], so ordinary API traffic also
 * feeds the "server unreachable" indicator.
 */
object HttpClients {

    /**
     * Host lookup for every client. The TV replaces it before the first request
     * (a router with DNS rebind protection can hide LAN answers; see the tv flavor's LanDns).
     */
    @Volatile
    var dns: okhttp3.Dns = okhttp3.Dns.SYSTEM

    val base: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .dns(object : okhttp3.Dns {
                override fun lookup(hostname: String) = dns.lookup(hostname)
            })
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            .addInterceptor(ServerStatusInterceptor)
            .build()
    }

    /**
     * Assistant endpoints stream Server-Sent Events and can run for many
     * minutes while the agent works. The server writes a `: heartbeat` frame
     * every 5s while idle, so a read gap of [STREAM_SILENCE_TIMEOUT_S] means
     * the connection is dead (VPN flap, Wi-Fi to mobile data). Without a read
     * timeout the chat would show the spinner forever.
     */
    val streaming: OkHttpClient by lazy {
        base.newBuilder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(STREAM_SILENCE_TIMEOUT_S, TimeUnit.SECONDS)
            .build()
    }

    /**
     * Call-recording uploads. The server transcribes and analyses the call
     * before it answers, which takes minutes for a long call, so the 30s read
     * timeout of [base] reported "failed" for uploads that actually worked.
     */
    val upload: OkHttpClient by lazy {
        base.newBuilder()
            .writeTimeout(5, TimeUnit.MINUTES)
            .readTimeout(15, TimeUnit.MINUTES)
            .build()
    }

    const val STREAM_SILENCE_TIMEOUT_S = 45L

    /** Voice / realtime WebSockets (same timeouts the ViewModel used before). */
    val webSocket: OkHttpClient by lazy {
        base.newBuilder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(10, TimeUnit.SECONDS)
            .build()
    }

    /** Watch voice proxy WebSocket: the server can take a while to answer. */
    val wearProxyWebSocket: OkHttpClient by lazy {
        base.newBuilder()
            .connectTimeout(20, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS)
            .build()
    }

    /** APK downloads. */
    val download: OkHttpClient by lazy {
        base.newBuilder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(5, TimeUnit.MINUTES)
            .build()
    }

    /**
     * Health probe: short timeouts so an unreachable server is detected
     * quickly. It skips the status interceptor because the monitor records
     * probe results itself.
     */
    val probe: OkHttpClient by lazy {
        base.newBuilder()
            .apply { interceptors().clear() }
            .connectTimeout(4, TimeUnit.SECONDS)
            .readTimeout(4, TimeUnit.SECONDS)
            .callTimeout(6, TimeUnit.SECONDS)
            .retryOnConnectionFailure(false)
            .build()
    }
}
