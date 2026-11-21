package io.github.ronganam.dana

import androidx.compose.ui.geometry.Offset
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.cos
import kotlin.math.sin

/** The alarm screen's snooze dial: geometry fits the Fold screens, and hit-testing matches what's drawn. */
class SnoozeDialTest {

    private val d = 2.25f // the Fold's 360 dpi
    private val zones = listOf("Home", "Work", "Parent's home", "Gym", "בית של אמא")
    private val tree = buildSnoozeTree(zones)

    /** Fold 8 cover at 360 dpi: ≈ 555×876 dp, dial low in the column. */
    private val coverWidth = 1248f
    private val coverOrigin = Offset(coverWidth / 2f, 647f * d)
    private val coverTop = (28f + 128f) * d
    private val cover = SnoozeDialGeometry.fit(coverOrigin, coverWidth, coverTop, d)

    private fun at(angleDeg: Float, distanceDp: Float): Offset {
        val rad = Math.toRadians(angleDeg.toDouble())
        return Offset((distanceDp * d * cos(rad)).toFloat(), (distanceDp * d * sin(rad)).toFloat())
    }

    private fun labels(path: List<MenuNode>) = path.drop(1).map { it.label }

    /** Every node of every open state, for every category and zone, inside the window and below the readout. */
    private fun assertFits(name: String, origin: Offset, width: Float, top: Float) {
        val geo = SnoozeDialGeometry.fit(origin, width, top, d)
        val paths = mutableListOf<List<MenuNode>>()
        for (l1 in tree.children) {
            paths += listOf(tree, l1)
            if (l1 is MenuNode.Parent) for (l2 in l1.children) {
                paths += listOf(tree, l1, l2)
                if (l2 is MenuNode.Parent) for (l3 in l2.children) paths += listOf(tree, l1, l2, l3)
            }
        }
        for (p in paths) for (vn in layoutSnoozeFan(tree, geo, p)) {
            val x = origin.x + vn.x
            val y = origin.y + vn.y
            assertTrue("$name: ${vn.label} off the left edge ($x)", x - vn.radiusPx >= 0f)
            assertTrue("$name: ${vn.label} off the right edge ($x)", x + vn.radiusPx <= width)
            assertTrue("$name: ${vn.label} under the readout ($y)", y - vn.radiusPx >= top)
            // Upward fan: nothing hangs below the dial's centre line by more than a node.
            assertTrue("$name: ${vn.label} below the dial", vn.y <= vn.radiusPx)
        }
        // Inner ring clears the 108 dp dial.
        assertTrue("$name: inner ring hits the dial", geo.r1 - geo.node1 >= 54f * d)
    }

    @Test
    fun fanFitsEveryFoldScreen() {
        assertFits("cover", coverOrigin, coverWidth, coverTop)
        // Inner, portrait (≈ 821×1088 dp): dial low and centred.
        assertFits("inner portrait", Offset(1848f / 2f, 860f * d), 1848f, (28f + 128f) * d)
        // Inner, landscape (≈ 1088×821 dp): dial in the right pane.
        assertFits("inner landscape", Offset(1848f * 1.0f, 640f * d), 2448f, (28f + 128f) * d)
    }

    @Test
    fun coverReachIsUsable() {
        assertTrue("reach ${cover.r3 / d}dp", cover.r3 / d in 200f..270f)
    }

    @Test
    fun picksCategoriesAcrossTheUpperHalf() {
        assertEquals(listOf("Time based"), labels(selectSnoozePath(at(-160f, 80f), tree, cover, emptyList())))
        assertEquals(listOf("Location based"), labels(selectSnoozePath(at(-110f, 80f), tree, cover, emptyList())))
        assertEquals(listOf("Car based"), labels(selectSnoozePath(at(-70f, 80f), tree, cover, emptyList())))
        assertEquals(listOf("When I wake up"), labels(selectSnoozePath(at(-20f, 80f), tree, cover, emptyList())))
        // Dragging down past horizontal clamps to the side categories.
        assertEquals(listOf("Time based"), labels(selectSnoozePath(at(160f, 80f), tree, cover, emptyList())))
        assertEquals(listOf("When I wake up"), labels(selectSnoozePath(at(20f, 80f), tree, cover, emptyList())))
        // Inside the dead zone: nothing yet.
        assertEquals(emptyList<String>(), labels(selectSnoozePath(at(-90f, 10f), tree, cover, emptyList())))
    }

    @Test
    fun drawnNodeCentresSelectThemselves() {
        // Walk to every node by putting the finger exactly where it's drawn.
        fun walk(target: List<MenuNode>) {
            var path: List<MenuNode> = listOf(tree)
            for (depth in 1 until target.size) {
                val vn = layoutSnoozeFan(tree, cover, target.take(depth + 1)).first { it.node == target[depth] }
                path = selectSnoozePath(Offset(vn.x, vn.y), tree, cover, path)
                assertEquals("finger on ${vn.label}", target.take(depth + 1), path)
            }
        }
        for (l1 in tree.children) {
            walk(listOf(tree, l1))
            if (l1 is MenuNode.Parent) for (l2 in l1.children) {
                walk(listOf(tree, l1, l2))
                if (l2 is MenuNode.Parent) for (l3 in l2.children) walk(listOf(tree, l1, l2, l3))
            }
        }
    }

    @Test
    fun outerRingLocksTheParentWhileSweeping() {
        val location = tree.children[1] as MenuNode.Parent
        val home = location.children[0] as MenuNode.Parent
        val atHome = layoutSnoozeFan(tree, cover, listOf(tree, location, home)).first { it.node == home }
        var path = selectSnoozePath(Offset(atHome.x, atHome.y), tree, cover, listOf(tree, location))
        assertEquals(listOf("Location based", "Home"), labels(path))
        // Sweep far to the right on the outer ring: still Home, now picking arrive/leave.
        path = selectSnoozePath(at(-30f, cover.r3 / d), tree, cover, path)
        assertEquals("Home", path[2].label)
        assertEquals(3, labels(path).size)
    }

    @Test
    fun readoutSpellsOutTheResult() {
        val leaving = MenuNode.Leaf("Leaving", "location", location = "Work", event = "leave")
        assertEquals("When I leave Work", snoozeReadout(listOf(tree, tree.children[1], leaving)).first)
        assertEquals("In 15 min", snoozeSentence(MenuNode.Leaf("15 min", "time", minutes = 15)))
        assertEquals("Let go here to cancel", snoozeReadout(listOf(tree)).second)
    }
}
