package app.ganam.assistant.tv

import android.util.Log
import app.ganam.assistant.data.network.HttpClients
import okhttp3.Dns
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.UnknownHostException
import java.nio.ByteBuffer
import java.util.concurrent.ConcurrentHashMap
import kotlin.random.Random

/** Dana TV's process: installs [LanDns] before any request is made. */
class DanaTvApplication : app.ganam.assistant.GanamApp() {
    override fun onCreate() {
        super.onCreate()
        HttpClients.dns = LanDns
        TvConfig.load(this)
        // Tearing down mic-session effects wedges this TV's audio stack (see MicHub).
        app.ganam.assistant.wake.MicHub.effectsAllowed = false
        // Measured from the couch (after gain): silence 25–40, quiet speech 65–100.
        app.ganam.assistant.wake.GainStream.speechRatio = 1.6
        app.ganam.assistant.wake.GainStream.minThreshold = 50.0
    }
}

/**
 * System DNS first; when it has no answer, ask the LAN resolver (Pi-hole) and
 * then Cloudflare directly.
 *
 * A router with DNS rebind protection answers names that point at a LAN address
 * with nothing, and the TV asks the router first, so the server and the camera
 * hosts "don't exist" for it. The LAN resolvers come from the build
 * (`dana.lanDnsServers` in local.properties). HTTPS keeps the hostname, so TLS
 * still verifies; RTSP gets the address.
 */
object LanDns : Dns {
    private const val TAG = "LanDns"
    private val FALLBACK_SERVERS = app.ganam.assistant.BuildConfig.LAN_DNS_SERVERS.split(',')
        .map { it.trim() }.filter { it.isNotEmpty() } + "1.1.1.1"
    private const val TTL_MS = 10 * 60_000L
    private val cache = ConcurrentHashMap<String, Pair<Long, List<InetAddress>>>()

    override fun lookup(hostname: String): List<InetAddress> {
        val system = runCatching { Dns.SYSTEM.lookup(hostname) }.getOrNull()
        if (!system.isNullOrEmpty()) return system
        cache[hostname]?.let { (at, addrs) -> if (System.currentTimeMillis() - at < TTL_MS) return addrs }
        for (server in FALLBACK_SERVERS) {
            val addrs = runCatching { queryA(hostname, server) }.getOrNull()
            if (!addrs.isNullOrEmpty()) {
                Log.i(TAG, "$hostname -> ${addrs.joinToString { it.hostAddress ?: "?" }} (via $server)")
                cache[hostname] = System.currentTimeMillis() to addrs
                return addrs
            }
        }
        throw UnknownHostException("No address for $hostname")
    }

    /** The IPv4 address for [host] as a string, for URLs that need it (RTSP). Blocking. */
    fun resolveToIp(host: String): String =
        runCatching { lookup(host).firstOrNull { it.address.size == 4 }?.hostAddress }.getOrNull() ?: host

    /** A plain UDP DNS query for A records. */
    private fun queryA(name: String, server: String): List<InetAddress> {
        val id = Random.nextInt(0, 0xFFFF)
        val q = ByteBuffer.allocate(512)
        q.putShort(id.toShort()).putShort(0x0100).putShort(1).putShort(0).putShort(0).putShort(0)
        name.trimEnd('.').split('.').forEach { label ->
            val b = label.toByteArray(); q.put(b.size.toByte()).put(b)
        }
        q.put(0).putShort(1).putShort(1)
        val out = q.array().copyOf(q.position())

        DatagramSocket().use { sock ->
            sock.soTimeout = 2000
            sock.send(DatagramPacket(out, out.size, InetSocketAddress(server, 53)))
            val buf = ByteArray(512)
            val p = DatagramPacket(buf, buf.size)
            sock.receive(p)
            val r = ByteBuffer.wrap(buf, 0, p.length)
            if ((r.getShort(0).toInt() and 0xFFFF) != id) return emptyList()
            val qd = r.getShort(4).toInt() and 0xFFFF
            val an = r.getShort(6).toInt() and 0xFFFF
            var pos = 12
            fun skipName() {
                while (true) {
                    val len = buf[pos].toInt() and 0xFF
                    if (len == 0) { pos += 1; return }
                    if (len and 0xC0 == 0xC0) { pos += 2; return }
                    pos += len + 1
                }
            }
            repeat(qd) { skipName(); pos += 4 }
            val result = mutableListOf<InetAddress>()
            repeat(an) {
                skipName()
                val type = r.getShort(pos).toInt() and 0xFFFF
                val rdLen = r.getShort(pos + 8).toInt() and 0xFFFF
                pos += 10
                if (type == 1 && rdLen == 4) result += InetAddress.getByAddress(name, buf.copyOfRange(pos, pos + 4))
                pos += rdLen
            }
            return result
        }
    }
}
