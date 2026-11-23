package io.github.ronganam.dana.ui.screens.now

import io.github.ronganam.dana.data.models.NowAction
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class NowWebLinkTest {
    private fun action(name: String, key: String, value: String) =
        NowAction(name, "Fix", buildJsonObject { put(key, JsonPrimitive(value)) }, client = true)

    @Test
    fun webTargets() {
        val base = "https://dana.example.com/"
        assertEquals("https://dana.example.com/#addons/homeassistant",
            NowWebLink.target(base, action("connect", "addon", "homeassistant")))
        assertEquals("https://dana.example.com/devices", NowWebLink.target(base, action("screen", "screen", "devices")))
        assertEquals("https://example.org/help", NowWebLink.target(base, action("url", "url", "https://example.org/help")))
    }

    @Test
    fun refusesWhatIsNotAWebFix() {
        val base = "https://dana.example.com"
        assertNull(NowWebLink.target(base, action("url", "url", "http://example.org/")))
        assertNull(NowWebLink.target(base, action("url", "url", "javascript:alert(1)")))
        assertNull(NowWebLink.target(base, action("screen", "screen", "../admin")))
        assertNull(NowWebLink.target(base, action("connect", "addon", "Home Assistant")))
        assertNull(NowWebLink.target(base, action("open", "x", "y")))
    }
}
