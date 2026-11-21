package io.github.ronganam.dana

import io.github.ronganam.dana.data.network.ServerConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** How a typed server address is stored: a scheme, no trailing slash, "" when unset. */
class ServerConfigTest {
    @Test fun addsHttpsAndDropsTrailingSlashes() {
        assertEquals("https://dana.example.com", ServerConfig.normalize(" dana.example.com// "))
        assertEquals("https://dana.example.com/base", ServerConfig.normalize("https://dana.example.com/base/"))
    }

    @Test fun keepsAnExplicitScheme() {
        assertEquals("http://10.0.2.2:8080", ServerConfig.normalize("http://10.0.2.2:8080/"))
    }

    @Test fun blankOrBareSchemeIsUnset() {
        assertEquals("", ServerConfig.normalize(""))
        assertEquals("", ServerConfig.normalize("https://"))
        assertEquals("", ServerConfig.normalize("  http:// "))
    }

    @Test fun configuredNeedsBoth() {
        assertTrue(ServerConfig("https://x", "t").isConfigured)
        assertFalse(ServerConfig("", "t").isConfigured)
        assertFalse(ServerConfig("https://x", " ").isConfigured)
    }
}
