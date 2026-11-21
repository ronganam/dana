package io.github.ronganam.dana

import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.unit.dp
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.sin

/*
 * The alarm's snooze dial without Compose: the menu (time, place, car,
 * waking up), what releasing on a leaf means, and the geometry that both
 * draws and hit-tests the fan (unit-tested in SnoozeDialTest).
 */

sealed class MenuNode {
    abstract val label: String

    data class Parent(
        override val label: String,
        val children: List<MenuNode>
    ) : MenuNode()

    data class Leaf(
        override val label: String,
        val snoozeType: String,
        val minutes: Int = 0,
        val location: String = "",
        val event: String = "",
        val action: String = ""
    ) : MenuNode()
}

data class VisualNode(
    val node: MenuNode,
    val x: Float,
    val y: Float,
    val radiusPx: Float,
    val isHighlighted: Boolean,
    val label: String,
    val parentX: Float = 0f,
    val parentY: Float = 0f
)

/** Time snoozes, shared by the quick row and the dial. */
internal val QUICK_SNOOZES = listOf(
    MenuNode.Leaf("15 min", "time", minutes = 15),
    MenuNode.Leaf("1 hour", "time", minutes = 60),
    MenuNode.Leaf("3 hours", "time", minutes = 180),
    MenuNode.Leaf("1 day", "time", minutes = 1440)
)

internal val WAKE_LEAF = MenuNode.Leaf("When I wake up", "wake")

/** The dial's menu: time, place (each zone → arriving / leaving), car, waking up. */
fun buildSnoozeTree(zones: List<String>): MenuNode.Parent = MenuNode.Parent(
    "Root", listOf(
        MenuNode.Parent("Time based", QUICK_SNOOZES),
        MenuNode.Parent("Location based", zones.map { zone ->
            MenuNode.Parent(zone, listOf(
                MenuNode.Leaf("Arriving", "location", location = zone, event = "arrive"),
                MenuNode.Leaf("Leaving", "location", location = zone, event = "leave")
            ))
        }),
        MenuNode.Parent("Car based", listOf(
            MenuNode.Leaf("Entering", "car", action = "enter"),
            MenuNode.Leaf("Exiting", "car", action = "leave")
        )),
        WAKE_LEAF
    )
)

/** What releasing on [leaf] does, as a sentence ("When I arrive at Home"). */
fun snoozeSentence(leaf: MenuNode.Leaf): String = when (leaf.snoozeType) {
    "time" -> "In ${leaf.label}"
    "location" -> if (leaf.event == "leave") "When I leave ${leaf.location}" else "When I arrive at ${leaf.location}"
    "car" -> if (leaf.action == "leave") "When I get out of the car" else "When I get in the car"
    "wake" -> "When I wake up"
    else -> leaf.label
}

/** Readout headline + hint for the current drag path. */
fun snoozeReadout(path: List<MenuNode>): Pair<String, String> {
    val last = path.lastOrNull()
    return when {
        path.size <= 1 -> "Drag to an option" to "Let go here to cancel"
        last is MenuNode.Leaf -> snoozeSentence(last) to "Let go to snooze"
        path[1].label == "Time based" -> "Snooze for…" to "Keep going out"
        path[1].label == "Location based" && path.size == 2 -> "When I get to a place…" to "Keep going out"
        path[1].label == "Location based" -> "${last?.label}: arrive or leave?" to "Keep going out"
        path[1].label == "Car based" -> "When the car…" to "Keep going out"
        else -> (last?.label ?: "") to "Keep going out"
    }
}

/**
 * Where the dial's rings sit, in px relative to the dial centre. Angles are in
 * screen degrees (0° = right, −90° = straight up). Everything fans across the
 * upper half so the hand holding the dial never covers an option; rings scale
 * to the room around the dial (≈ 245 dp reach on a Fold cover).
 *
 * The same geometry drives drawing and hit-testing, so what you see is what
 * you select.
 */
class SnoozeDialGeometry(
    val r1: Float,
    val r2: Float,
    val r3: Float,
    density: Float,
    /** Vertical stretch: rings are ovals k× taller than wide, using the room above the dial. */
    val k: Float = 1f
) {
    /** A finger offset mapped into the round space the rings are defined in. */
    fun normalize(offset: Offset) = Offset(offset.x, offset.y / k)

    /** Leave the centre this far to pick a category… */
    val t1 = r1 * 0.45f
    /** …this far to step into the category's options… */
    val t2 = r1 + (r2 - r1) * 0.3f
    /** …and this far for a place's arrive / leave. */
    val t3 = r2 + (r3 - r2) * 0.3f

    val node1 = 32f * density
    val node2 = 30f * density
    val node3 = 30f * density

    /** One ring of options: [count] slots of [step]° starting at [start]°. */
    data class Ring(val start: Float, val step: Float, val count: Int, val radius: Float, val k: Float = 1f) {
        fun angleOf(i: Int) = start + step * (i + 0.5f)
        fun indexAt(angle: Float) = floor((angle - start) / step).toInt().coerceIn(0, count - 1)
        /** Where slot [i] is drawn, px from the dial centre (on the oval). */
        fun position(i: Int): Offset {
            val rad = Math.toRadians(angleOf(i).toDouble())
            return Offset((radius * cos(rad)).toFloat(), (k * radius * sin(rad)).toFloat())
        }
    }

    /** Categories: evenly across the upper half. */
    fun ring1(count: Int) = Ring(-180f, 180f / count.coerceAtLeast(1), count.coerceAtLeast(1), r1, k)

    /** Options of the category at [parentAngle]. */
    fun ring2(parentAngle: Float, count: Int) = fan(parentAngle, count, r2, node2)

    /** Arrive / leave of the place at [parentAngle]. */
    fun ring3(parentAngle: Float, count: Int) = fan(parentAngle, count, r3, node3)

    /** A fan centred on [center], wide enough that nodes don't touch, kept in the upper half. */
    private fun fan(center: Float, count: Int, radius: Float, nodeRadius: Float): Ring {
        val n = count.coerceAtLeast(1)
        val minStep = Math.toDegrees((nodeRadius * 2.3f / radius).toDouble()).toFloat()
        val step = maxOf(22f, minStep).coerceAtMost(MAX_FAN / n)
        val span = step * n
        var start = center - span / 2f
        if (start < FAN_MIN) start = FAN_MIN
        if (start + span > FAN_MAX) start = FAN_MAX - span
        return Ring(start, step, n, radius, k)
    }

    companion object {
        /** Fans may dip a little below horizontal at the sides, never further. */
        const val FAN_MIN = -195f
        const val FAN_MAX = 15f
        const val MAX_FAN = FAN_MAX - FAN_MIN

        /**
         * Fit the rings to the space around [origin] (dial centre, window px):
         * as far out as the window's sides allow (190–270 dp), then stretched
         * upward (up to 1.4×) into the room below the readout. The inner ring
         * always clears the dial.
         */
        fun fit(origin: Offset, width: Float, topLimit: Float, density: Float): SnoozeDialGeometry {
            val margin = 6f * density
            val nodeR = 32f * density
            val up = origin.y - topLimit - nodeR
            val side = minOf(origin.x - margin, width - origin.x - margin) - nodeR
            val reach = minOf(side, up).coerceIn(190f * density, 270f * density)
            val k = (up / reach).coerceIn(1f, 1.4f)
            val r1 = maxOf(reach * 0.42f, 100f * density)
            val r2 = maxOf(reach * 0.71f, r1 + 66f * density)
            val r3 = maxOf(reach, r2 + 64f * density)
            return SnoozeDialGeometry(r1, r2, r3, density, k)
        }

        /** atan2 angle folded into (−270°, 90°] so left-side fans stay continuous. */
        fun screenAngle(offset: Offset): Float {
            var a = Math.toDegrees(atan2(offset.y.toDouble(), offset.x.toDouble())).toFloat()
            if (a > 90f) a -= 360f
            return a
        }
    }
}

/**
 * The menu path for a finger at [offset] (px from the dial centre). Deeper
 * levels lock their parent, so sweeping sideways along an outer ring changes
 * only that ring's choice.
 */
fun selectSnoozePath(
    offset: Offset,
    root: MenuNode.Parent,
    geometry: SnoozeDialGeometry,
    previous: List<MenuNode>
): List<MenuNode> {
    val round = geometry.normalize(offset)
    val distance = round.getDistance()
    val angle = SnoozeDialGeometry.screenAngle(round)
    val path = mutableListOf<MenuNode>(root)
    if (distance < geometry.t1 || root.children.isEmpty()) return path

    val l1Nodes = root.children
    val ring1 = geometry.ring1(l1Nodes.size)
    val prevL1 = previous.getOrNull(1)
    val i1 = if (distance >= geometry.t2 && prevL1 != null && prevL1 in l1Nodes) l1Nodes.indexOf(prevL1)
             else ring1.indexAt(angle)
    val l1 = l1Nodes[i1]
    path.add(l1)
    if (distance < geometry.t2 || l1 !is MenuNode.Parent || l1.children.isEmpty()) return path

    val ring2 = geometry.ring2(ring1.angleOf(i1), l1.children.size)
    val prevL2 = previous.getOrNull(2)
    val i2 = if (distance >= geometry.t3 && prevL1 == l1 && prevL2 != null && prevL2 in l1.children) l1.children.indexOf(prevL2)
             else ring2.indexAt(angle)
    val l2 = l1.children[i2]
    path.add(l2)
    if (distance < geometry.t3 || l2 !is MenuNode.Parent || l2.children.isEmpty()) return path

    val ring3 = geometry.ring3(ring2.angleOf(i2), l2.children.size)
    path.add(l2.children[ring3.indexAt(angle)])
    return path
}

/** Nodes to draw for [path]: px from the dial centre, plus the parent they hang from. */
fun layoutSnoozeFan(root: MenuNode.Parent, geometry: SnoozeDialGeometry, path: List<MenuNode>): List<VisualNode> {
    val out = mutableListOf<VisualNode>()
    val l1Nodes = root.children
    if (l1Nodes.isEmpty()) return out
    val ring1 = geometry.ring1(l1Nodes.size)
    val l1 = path.getOrNull(1)
    l1Nodes.forEachIndexed { i, node ->
        // Once a category is open, only it stays on the inner ring.
        if (path.size <= 2 || node == l1) {
            val p = ring1.position(i)
            out.add(VisualNode(node, p.x, p.y, geometry.node1, node == l1, node.label))
        }
    }
    if (l1 !is MenuNode.Parent || l1.children.isEmpty()) return out
    val i1 = l1Nodes.indexOf(l1)
    val p1 = ring1.position(i1)
    val ring2 = geometry.ring2(ring1.angleOf(i1), l1.children.size)
    val l2 = path.getOrNull(2)
    l1.children.forEachIndexed { i, node ->
        if (path.size <= 3 || node == l2) {
            val p = ring2.position(i)
            out.add(VisualNode(node, p.x, p.y, geometry.node2, node == l2, node.label, p1.x, p1.y))
        }
    }
    if (l2 !is MenuNode.Parent || l2.children.isEmpty()) return out
    val i2 = l1.children.indexOf(l2)
    val p2 = ring2.position(i2)
    val ring3 = geometry.ring3(ring2.angleOf(i2), l2.children.size)
    val l3 = path.getOrNull(3)
    l2.children.forEachIndexed { i, node ->
        val p = ring3.position(i)
        out.add(VisualNode(node, p.x, p.y, geometry.node3, node == l3, node.label, p2.x, p2.y))
    }
    return out
}
