package io.github.ronganam.dana.data.discovery

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Build
import android.util.Log
import java.net.Inet4Address
import java.net.InetAddress

/**
 * [NsdBrowser] over Android's [NsdManager]: browses [ServerDiscovery.SERVICE_TYPE]
 * and resolves each service found, one at a time (before Android 14 a second
 * resolve while one runs fails with FAILURE_ALREADY_ACTIVE).
 */
class AndroidNsdBrowser(context: Context) : NsdBrowser {
    private val nsd = context.applicationContext.getSystemService(NsdManager::class.java)
    private val lock = Any()
    private var listener: NsdBrowser.Listener? = null
    private var discovery: NsdManager.DiscoveryListener? = null
    private val queue = ArrayDeque<NsdServiceInfo>()
    private var resolving = false

    override fun start(listener: NsdBrowser.Listener) {
        val manager = nsd ?: return
        synchronized(lock) {
            if (discovery != null) return
            this.listener = listener
            val d = object : NsdManager.DiscoveryListener {
                override fun onDiscoveryStarted(serviceType: String) = Unit
                override fun onDiscoveryStopped(serviceType: String) = Unit
                override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
                    Log.w(TAG, "Discovery didn't start ($errorCode)")
                    synchronized(lock) { if (discovery === this) discovery = null }
                }
                override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) = Unit
                override fun onServiceFound(info: NsdServiceInfo) = enqueue(info)
                override fun onServiceLost(info: NsdServiceInfo) {
                    synchronized(lock) { this@AndroidNsdBrowser.listener }?.onLost(info.serviceName)
                }
            }
            discovery = d
            runCatching { manager.discoverServices(ServerDiscovery.SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, d) }
                .onFailure { Log.w(TAG, "Discovery failed: ${it.message}"); discovery = null }
        }
    }

    override fun stop() {
        val d = synchronized(lock) {
            listener = null
            queue.clear()
            discovery.also { discovery = null }
        } ?: return
        runCatching { nsd?.stopServiceDiscovery(d) }
    }

    private fun enqueue(info: NsdServiceInfo) {
        synchronized(lock) {
            if (listener == null) return
            queue.addLast(info)
            if (resolving) return
            resolving = true
        }
        resolveNext()
    }

    private fun resolveNext() {
        val next = synchronized(lock) {
            queue.removeFirstOrNull().also { if (it == null) resolving = false }
        } ?: return
        val manager = nsd ?: return
        @Suppress("DEPRECATION")  // registerServiceInfoCallback is Android 14+; resolveService works on every version
        val started = runCatching {
            manager.resolveService(next, object : NsdManager.ResolveListener {
                override fun onResolveFailed(info: NsdServiceInfo, errorCode: Int) {
                    Log.w(TAG, "Couldn't resolve ${info.serviceName} ($errorCode)")
                    resolveNext()
                }

                override fun onServiceResolved(info: NsdServiceInfo) {
                    synchronized(lock) { listener }?.onFound(info.toFound())
                    resolveNext()
                }
            })
        }
        if (started.isFailure) resolveNext()
    }

    private fun NsdServiceInfo.toFound(): FoundService {
        val addresses: List<InetAddress> = if (Build.VERSION.SDK_INT >= 34) hostAddresses
            else @Suppress("DEPRECATION") listOfNotNull(host)
        val address = addresses.firstOrNull { it is Inet4Address } ?: addresses.firstOrNull()
        val attrs = attributes.orEmpty().mapValues { (_, v) -> v?.toString(Charsets.UTF_8).orEmpty() }
        return FoundService(serviceName.orEmpty(), address?.hostAddress, port, attrs)
    }

    private companion object {
        const val TAG = "ServerDiscovery"
    }
}
