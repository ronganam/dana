package io.github.ronganam.dana.data.discovery

import io.github.ronganam.dana.data.network.ServerConfig
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/** A Dana server found on the home network: its advertised [name], the [url] to use, its [version] if it said. */
data class DiscoveredServer(val name: String, val url: String, val version: String?)

/** One resolved `_dana._tcp` service: instance name, host address, port and TXT attributes. */
data class FoundService(val name: String, val host: String?, val port: Int, val attributes: Map<String, String>)

/**
 * The seam over the platform's service discovery ([AndroidNsdBrowser] on a
 * device, a fake in tests): reports resolved services and lost ones.
 */
interface NsdBrowser {
    interface Listener {
        fun onFound(service: FoundService)
        fun onLost(name: String)
    }

    fun start(listener: Listener)
    fun stop()
}

/**
 * Finds Dana servers on the LAN: the server (or a sidecar) advertises
 * `_dana._tcp` with TXT `path` (default `/`) and `version`. [servers] holds what
 * is visible now, sorted by name; a pairing screen fills its address from it.
 */
class ServerDiscovery(private val browser: NsdBrowser) {
    private val found = MutableStateFlow<Map<String, DiscoveredServer>>(emptyMap())
    private val _servers = MutableStateFlow<List<DiscoveredServer>>(emptyList())
    val servers: StateFlow<List<DiscoveredServer>> = _servers.asStateFlow()
    private var running = false

    private val listener = object : NsdBrowser.Listener {
        override fun onFound(service: FoundService) {
            val server = toServer(service) ?: return
            synchronized(found) { found.update { it + (service.name to server) }; publish() }
        }

        override fun onLost(name: String) {
            synchronized(found) { found.update { it - name }; publish() }
        }
    }

    fun start() {
        if (running) return
        running = true
        browser.start(listener)
    }

    fun stop() {
        if (!running) return
        running = false
        browser.stop()
        synchronized(found) { found.value = emptyMap(); publish() }
    }

    private fun publish() {
        _servers.value = found.value.values.distinctBy { it.url }.sortedWith(compareBy({ it.name.lowercase() }, { it.url }))
    }

    companion object {
        const val SERVICE_TYPE = "_dana._tcp."

        /**
         * The address a service stands for: `http://<host>:<port><path>` (https on
         * port 443), IPv6 hosts in brackets, normalized like a typed address.
         * Null without a host or a port.
         */
        fun toServer(service: FoundService): DiscoveredServer? {
            val host = service.host?.trim()?.removePrefix("/")?.substringBefore('%')?.takeIf { it.isNotEmpty() } ?: return null
            if (service.port !in 1..65535) return null
            val scheme = if (service.port == 443) "https" else "http"
            val hostPart = if (':' in host) "[$host]" else host
            val portPart = if ((scheme == "https" && service.port == 443) || (scheme == "http" && service.port == 80)) "" else ":${service.port}"
            val path = service.attributes["path"]?.trim().orEmpty().let { if (it.isEmpty() || it.startsWith("/")) it else "/$it" }
            val url = ServerConfig.normalize("$scheme://$hostPart$portPart$path")
            if (url.isEmpty()) return null
            val version = service.attributes["version"]?.trim()?.takeIf { it.isNotEmpty() }
            return DiscoveredServer(service.name.ifBlank { host }, url, version)
        }
    }
}
