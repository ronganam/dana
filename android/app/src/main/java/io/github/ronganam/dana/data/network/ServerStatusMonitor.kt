package io.github.ronganam.dana.data.network

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.Interceptor
import okhttp3.Request
import okhttp3.Response
import java.io.IOException
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLException

enum class Reachability { UNKNOWN, NOT_CONFIGURED, ONLINE, OFFLINE }

data class ServerStatus(
    val reachability: Reachability = Reachability.UNKNOWN,
    /** Human-readable reason when [reachability] is OFFLINE. */
    val detail: String? = null,
    /** True while a health probe is in flight. */
    val checking: Boolean = false,
    /** Wall-clock time of the last probe or observed request, 0 if never. */
    val lastCheckedAt: Long = 0L
) {
    val isOffline: Boolean get() = reachability == Reachability.OFFLINE
}

/**
 * Tracks whether the assistant server can be reached.
 *
 * The server is reached over a VPN, so "the server is down" usually means
 * "the VPN is off". Every screen can show that before the user types or
 * speaks.
 *
 * Signals:
 *  - **Active probes** of `GET /api/health`: periodic while the UI is in the
 *    foreground ([start]/[stop] are ref-counted), and immediate on any network
 *    change (VPN up/down switches the default network).
 *  - **Passive observations** from [ServerStatusInterceptor]: any response from
 *    the server host marks it online, and a transport failure triggers a probe.
 *
 * One instance per process; the `:session` voice process configures its own.
 */
object ServerStatusMonitor {
    private const val TAG = "ServerStatus"
    private const val POLL_ONLINE_MS = 60_000L
    private const val POLL_OFFLINE_MS = 10_000L
    private const val NETWORK_SETTLE_MS = 750L

    private val _status = MutableStateFlow(ServerStatus())
    val status: StateFlow<ServerStatus> = _status.asStateFlow()

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val lock = Any()

    @Volatile private var baseUrl: HttpUrl? = null
    @Volatile private var appContext: Context? = null
    private var probeJob: Job? = null
    private var pollJob: Job? = null
    private var foregroundCount = 0
    private var networkCallback: ConnectivityManager.NetworkCallback? = null

    /** Point the monitor at a (possibly new) server URL and re-check. */
    fun configure(context: Context, serverUrl: String) {
        appContext = context.applicationContext
        val parsed = serverUrl.trim().trimEnd('/').toHttpUrlOrNull()
            ?.takeIf { it.host.isNotBlank() }
        val changed = parsed != baseUrl
        baseUrl = parsed
        if (parsed == null) {
            _status.value = ServerStatus(Reachability.NOT_CONFIGURED)
            return
        }
        if (changed) {
            _status.value = ServerStatus(Reachability.UNKNOWN)
        }
        checkNow()
    }

    /** UI became visible: poll and listen for network changes. Ref-counted. */
    fun start(context: Context) {
        appContext = context.applicationContext
        synchronized(lock) {
            foregroundCount++
            if (foregroundCount > 1) return
            registerNetworkCallback()
            pollJob?.cancel()
            pollJob = scope.launch {
                while (isActive) {
                    probe()
                    val wait = if (_status.value.isOffline) POLL_OFFLINE_MS else POLL_ONLINE_MS
                    delay(wait)
                }
            }
        }
    }

    /** UI went away. Polling stops once every [start] has been balanced. */
    fun stop() {
        synchronized(lock) {
            if (foregroundCount == 0) return
            foregroundCount--
            if (foregroundCount > 0) return
            pollJob?.cancel()
            pollJob = null
            unregisterNetworkCallback()
        }
    }

    /** Probe now unless a probe is already in flight. */
    fun checkNow(): Job = synchronized(lock) {
        probeJob?.takeIf { it.isActive } ?: scope.launch { probe() }.also { probeJob = it }
    }

    /**
     * Probe now and wait for the result (bounded by [timeoutMs]).
     * Returns true when the server answered.
     */
    suspend fun ensureReachable(timeoutMs: Long = 7_000L): Boolean {
        if (baseUrl == null) return false
        withTimeoutOrNull(timeoutMs) { checkNow().join() }
        return _status.value.reachability == Reachability.ONLINE
    }

    // --- Passive signals from the interceptor ---

    internal fun onResponse(url: HttpUrl, code: Int) {
        if (!isServerHost(url)) return
        if (code in 502..504) {
            // The ingress answered but the backend did not.
            checkNow()
            return
        }
        markOnline()
    }

    internal fun onTransportFailure(url: HttpUrl, e: IOException) {
        if (!isServerHost(url)) return
        Log.d(TAG, "Request to ${url.encodedPath} failed (${e.javaClass.simpleName}); probing")
        checkNow()
    }

    // --- Internals ---

    private fun isServerHost(url: HttpUrl): Boolean {
        val base = baseUrl ?: return false
        return url.host.equals(base.host, ignoreCase = true) && url.port == base.port
    }

    private fun markOnline() {
        val current = _status.value
        if (current.reachability != Reachability.ONLINE || current.detail != null) {
            Log.i(TAG, "Server reachable")
        }
        _status.value = ServerStatus(Reachability.ONLINE, lastCheckedAt = System.currentTimeMillis())
    }

    private fun markOffline(detail: String) {
        if (_status.value.reachability != Reachability.OFFLINE) {
            Log.w(TAG, "Server unreachable: $detail")
        }
        _status.value = ServerStatus(Reachability.OFFLINE, detail, lastCheckedAt = System.currentTimeMillis())
    }

    private suspend fun probe() {
        val base = baseUrl ?: return
        _status.value = _status.value.copy(checking = true)
        try {
            var failure = probeOnce(base)
            // A single blip right after being online is common on mobile
            // networks. Retry once before telling the user the server is gone.
            if (failure != null && _status.value.reachability == Reachability.ONLINE) {
                delay(1_500)
                failure = probeOnce(base)
            }
            if (failure == null) markOnline() else markOffline(failure)
        } finally {
            if (_status.value.checking) _status.value = _status.value.copy(checking = false)
        }
    }

    /** Returns null when the server answered, otherwise a short reason. */
    private fun probeOnce(base: HttpUrl): String? {
        val url = base.newBuilder().addPathSegments("api/health").build()
        val request = Request.Builder().url(url).get().build()
        return try {
            HttpClients.probe.newCall(request).execute().use { response ->
                // Any answer from the app means it is reachable, including a
                // 404 from a server older than the health endpoint. Gateway
                // errors mean the backend behind the ingress is down.
                if (response.code in 502..504) "Server is down (HTTP ${response.code})" else null
            }
        } catch (e: IOException) {
            describe(e)
        }
    }

    private fun describe(e: IOException): String {
        if (!hasNetwork()) return "No network connection"
        return when (e) {
            is UnknownHostException -> "Can't resolve server — is the VPN on?"
            is SocketTimeoutException, is ConnectException, is NoRouteToHostException ->
                "Server not responding — is the VPN on?"
            is SSLException -> "Secure connection failed"
            else -> "Server unreachable — is the VPN on?"
        }
    }

    private fun hasNetwork(): Boolean {
        val cm = appContext?.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            ?: return true
        return try {
            val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return false
            caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
        } catch (e: Exception) {
            true
        }
    }

    private fun registerNetworkCallback() {
        val cm = appContext?.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager ?: return
        if (networkCallback != null) return
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) = onNetworkChanged()
            override fun onLost(network: Network) = onNetworkChanged()
            override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
                // Fires often (signal strength etc.). Only probe when the
                // current verdict could be wrong.
                if (_status.value.reachability != Reachability.ONLINE) onNetworkChanged()
            }
        }
        try {
            cm.registerDefaultNetworkCallback(callback)
            networkCallback = callback
        } catch (e: Exception) {
            Log.w(TAG, "Could not register network callback", e)
        }
    }

    private fun unregisterNetworkCallback() {
        val cm = appContext?.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
        val callback = networkCallback ?: return
        networkCallback = null
        try {
            cm?.unregisterNetworkCallback(callback)
        } catch (e: Exception) {
            Log.w(TAG, "Could not unregister network callback", e)
        }
    }

    private var networkChangeJob: Job? = null

    private fun onNetworkChanged() {
        synchronized(lock) {
            networkChangeJob?.cancel()
            networkChangeJob = scope.launch {
                // Let routes (and the VPN tunnel) settle before probing.
                delay(NETWORK_SETTLE_MS)
                checkNow()
            }
        }
    }
}

/**
 * Feeds ordinary API traffic into [ServerStatusMonitor]: a response proves
 * the server is reachable, and a transport failure triggers a probe.
 */
object ServerStatusInterceptor : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        try {
            val response = chain.proceed(request)
            ServerStatusMonitor.onResponse(request.url, response.code)
            return response
        } catch (e: IOException) {
            if (!chain.call().isCanceled()) {
                ServerStatusMonitor.onTransportFailure(request.url, e)
            }
            throw e
        }
    }
}
