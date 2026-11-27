package io.github.ronganam.dana.ui

import io.github.ronganam.dana.ui.theme.CategoryHues
import io.github.ronganam.dana.ui.theme.DanaHues
import io.github.ronganam.dana.ui.theme.Hue
import androidx.compose.ui.graphics.toArgb
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class HuesTest {
    private val dark = DanaHues(dark = true)
    private val light = DanaHues(dark = false)

    @Test
    fun storedHuesComeBackInTheThemesValue() {
        // A picked hue is stored as the value the web and the watch share; it reads back as the theme's.
        CategoryHues.forEach { hue ->
            assertEquals(hue.dark, dark.fromHex(hue.storedHex))
            assertEquals(hue.light, light.fromHex(hue.storedHex))
        }
        // Colours stored before Circuit still map: the old ember / plum dark and light values.
        assertEquals(Hue.EMBER.dark, dark.fromHex("#FF6A3D"))
        assertEquals(Hue.PLUM.light, light.fromHex("#7044b8"))
        // Today's theme values map too.
        assertEquals(Hue.SKY.dark, dark.fromHex(String.format("#%06x", 0x8DB6FF)))
    }

    @Test
    fun otherColoursPassThrough() {
        assertEquals(0xFF123456.toInt(), dark.fromHex("#123456")!!.toArgb())
        assertNull(dark.fromHex("nope"))
        assertNull(dark.fromHex(""))
    }

    @Test
    fun storedHexIsTheSharedValue() {
        assertEquals("#f0709a", Hue.ROSE.storedHex)
        assertEquals("#5aa7f0", Hue.SKY.storedHex)
    }
}
