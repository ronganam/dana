package io.github.ronganam.dana.discovery

import io.github.ronganam.dana.data.discovery.DiscoveredServer
import io.github.ronganam.dana.data.discovery.FoundService
import io.github.ronganam.dana.data.discovery.NsdBrowser
import io.github.ronganam.dana.data.discovery.ServerDiscovery
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** `_dana._tcp` discovery: resolved services become server addresses (TXT path / version), lost ones go away. */
class ServerDiscoveryTest {
    private class FakeBrowser : NsdBrowser {
        var listener: NsdBrowser.Listener? = null
        var starts = 0
        var stops = 0
        override fun start(listener: NsdBrowser.Listener) { this.listener = listener; starts++ }
        override fun stop() { listener = null; stops++ }
    }

    private fun service(name: String, host: String?, port: Int = 8080, vararg attrs: Pair<String, String>) =
        FoundService(name, host, port, mapOf(*attrs))

    @Test fun foundServicesBecomeServersSortedAndLostOnesLeave() {
        val browser = FakeBrowser()
        val discovery = ServerDiscovery(browser)
        discovery.start(); discovery.start()
        assertEquals(1, browser.starts)

        browser.listener!!.onFound(service("Dana", "192.168.1.5", 8080, "path" to "/", "version" to "10.1.0"))
        browser.listener!!.onFound(service("Cabin", "192.168.7.2", 8080))
        assertEquals(listOf(
            DiscoveredServer("Cabin", "http://192.168.7.2:8080", null),
            DiscoveredServer("Dana", "http://192.168.1.5:8080", "10.1.0"),
        ), discovery.servers.value)

        browser.listener!!.onFound(service("Dana", "192.168.1.6", 8080))
        assertEquals("a re-resolved service replaces itself", "http://192.168.1.6:8080",
            discovery.servers.value.single { it.name == "Dana" }.url)

        browser.listener!!.onLost("Cabin")
        assertEquals(listOf("Dana"), discovery.servers.value.map { it.name })

        discovery.stop()
        assertEquals(1, browser.stops)
        assertTrue(discovery.servers.value.isEmpty())
    }

    @Test fun addressesFromHostPortAndPath() {
        fun url(s: FoundService) = ServerDiscovery.toServer(s)?.url
        assertEquals("http://192.168.1.5:8080/dana", url(service("a", "192.168.1.5", 8080, "path" to "/dana/")))
        assertEquals("http://192.168.1.5:8080/dana", url(service("a", "192.168.1.5", 8080, "path" to "dana")))
        assertEquals("http://192.168.1.5", url(service("a", "/192.168.1.5", 80)))
        assertEquals("https://192.168.1.5", url(service("a", "192.168.1.5", 443)))
        assertEquals("http://[fe80::1]:8080", url(service("a", "fe80::1%wlan0", 8080)))
    }

    @Test fun servicesWithoutHostOrPortAreSkipped() {
        assertNull(ServerDiscovery.toServer(service("a", null)))
        assertNull(ServerDiscovery.toServer(service("a", " ")))
        assertNull(ServerDiscovery.toServer(service("a", "192.168.1.5", 0)))
        val browser = FakeBrowser()
        val discovery = ServerDiscovery(browser).apply { start() }
        browser.listener!!.onFound(service("a", null))
        assertTrue(discovery.servers.value.isEmpty())
    }

    @Test fun twoNamesForOneAddressShowOnce() {
        val browser = FakeBrowser()
        val discovery = ServerDiscovery(browser).apply { start() }
        browser.listener!!.onFound(service("Dana", "192.168.1.5"))
        browser.listener!!.onFound(service("Dana (2)", "192.168.1.5"))
        assertEquals(1, discovery.servers.value.size)
        assertEquals("a nameless service is named by its host", "192.168.1.5", ServerDiscovery.toServer(service("", "192.168.1.5"))!!.name)
    }
}
