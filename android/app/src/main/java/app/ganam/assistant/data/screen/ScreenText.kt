package app.ganam.assistant.data.screen

import org.json.JSONObject

/**
 * One node of the app under the assistant sheet, flattened from
 * `AssistStructure.ViewNode` (see `AssistCapture`) so the description can be
 * built and tested without Android.
 *
 * Positions are the platform's: [left] / [top] relative to the parent, and
 * the parent's [scrollX] / [scrollY] shift its children. A size of -1 means
 * unknown (never clipped); 0 means the node takes no room on screen.
 */
data class ScreenNode(
    val text: String? = null,
    val contentDescription: String? = null,
    /** `View.VISIBLE` and not fully transparent. */
    val visible: Boolean = true,
    val left: Int = 0,
    val top: Int = 0,
    val width: Int = -1,
    val height: Int = -1,
    val scrollX: Int = 0,
    val scrollY: Int = 0,
    /** A password field: its text never leaves the phone. */
    val password: Boolean = false,
    val children: List<ScreenNode> = emptyList()
)

/**
 * What goes to the server as `device_screen` (server `docs/agent-actions.md`):
 * every field optional, blanks left out.
 */
data class DeviceScreen(
    val app: String? = null,
    val packageName: String? = null,
    val title: String? = null,
    val url: String? = null,
    val text: String? = null
) {
    val hasText: Boolean get() = !text.isNullOrBlank()

    fun toJson(): JSONObject = JSONObject().apply {
        app?.takeIf { it.isNotBlank() }?.let { put("app", it) }
        packageName?.takeIf { it.isNotBlank() }?.let { put("package", it) }
        title?.takeIf { it.isNotBlank() }?.let { put("title", it) }
        url?.takeIf { it.isNotBlank() }?.let { put("url", it) }
        text?.takeIf { it.isNotBlank() }?.let { put("text", it) }
    }
}

/** Builds the visible text of a screen, in reading order, trimmed and capped. */
object ScreenText {
    /** The most text sent with one message (characters). */
    const val MAX_TEXT = 6000
    /** One node's text is cut here: a long article paragraph still leaves room for the rest. */
    const val MAX_LINE = 600
    /** Nodes looked at, at most (a huge web page must not stall the sheet). */
    const val MAX_NODES = 5000

    private val WHITESPACE = Regex("\\s+")

    /**
     * The visible text under [root] in reading order: siblings top-to-bottom
     * then left-to-right, and pieces that sit on one row next to each other
     * (a web paragraph split into links and spans) joined into one line.
     *
     * Skipped: hidden, transparent, zero-size (or 1–2 px collapsed) nodes and
     * their children, password fields, and off-screen nodes when
     * [viewportWidth] / [viewportHeight] are known. Clipping stops under a
     * node wider than the viewport (web content reports its own, scaled,
     * coordinates and already sends only what is near the screen). A content
     * description is used only where nothing inside has text (containers
     * repeat their children). If clipping leaves nothing, the unclipped text
     * is used instead.
     */
    fun lines(root: ScreenNode, viewportWidth: Int = -1, viewportHeight: Int = -1): List<String> {
        val clipped = collect(root, viewportWidth, viewportHeight)
        if (clipped.isNotEmpty() || viewportWidth <= 0 || viewportHeight <= 0) return clipped
        return collect(root, -1, -1)
    }

    /** [lines] of several windows (dialogs over an activity), joined and capped at [maxChars]. */
    fun text(lineGroups: List<List<String>>, maxChars: Int = MAX_TEXT): String {
        val out = StringBuilder()
        var previous: String? = null
        for (line in lineGroups.flatten()) {
            if (line == previous) continue
            previous = line
            val extra = (if (out.isEmpty()) 0 else 1) + line.length
            if (out.length + extra > maxChars) {
                val room = maxChars - out.length - (if (out.isEmpty()) 0 else 1) - 1
                if (room > 20) {
                    if (out.isNotEmpty()) out.append('\n')
                    out.append(line.take(room).trimEnd()).append('…')
                } else if (out.isNotEmpty() && out.length < maxChars) {
                    out.append('…')
                }
                break
            }
            if (out.isNotEmpty()) out.append('\n')
            out.append(line)
        }
        return out.toString()
    }

    /** One node's text: whitespace collapsed, long text cut. Null when there is nothing to say. */
    fun clean(raw: CharSequence?): String? {
        val s = raw?.toString()?.replace(WHITESPACE, " ")?.trim().orEmpty()
        if (s.isEmpty()) return null
        return if (s.length > MAX_LINE) s.take(MAX_LINE - 1).trimEnd() + "…" else s
    }

    /**
     * A window title worth sending: not blank, not the activity's component
     * name (`com.app/com.app.MainActivity`) or the package itself.
     */
    fun cleanTitle(raw: CharSequence?, packageName: String?): String? {
        val t = clean(raw) ?: return null
        if (packageName != null && (t == packageName || t.startsWith("$packageName/"))) return null
        if (Regex("^[a-zA-Z0-9_]+(\\.[a-zA-Z0-9_]+)+(/[\\w.$]+)?$").matches(t)) return null
        return t
    }

    private class Walk(var nodes: Int = 0)

    /** One node's text and where it sits (absolute; -1 sizes unknown). */
    private class Piece(val text: String, val x: Int, val y: Int, val w: Int, val h: Int)

    private fun collect(root: ScreenNode, vw: Int, vh: Int): List<String> {
        val pieces = ArrayList<Piece>()
        walk(root, 0, 0, vw > 0 && vh > 0, vw, vh, pieces, Walk())
        val lines = ArrayList<String>()
        val line = StringBuilder()
        var prev: Piece? = null
        fun flush() {
            val t = line.toString().trim()
            if (t.isNotEmpty()) lines.add(if (t.length > MAX_LINE) t.take(MAX_LINE - 1).trimEnd() + "…" else t)
            line.setLength(0)
        }
        for (p in pieces) {
            val last = prev
            if (last != null && sameRow(last, p)) {
                // Adjacent pieces of one run of text keep their own spacing;
                // separate labels side by side get a space.
                val gap = p.x - (last.x + last.w)
                val needsSpace = gap > maxOf(2, minOf(last.h, p.h) / 40) &&
                    !line.endsWith(" ") && !p.text.startsWith(" ")
                if (needsSpace) line.append(' ')
            } else {
                flush()
            }
            line.append(p.text)
            prev = p
        }
        flush()
        // A label said twice in a row (a row and its title) is said once.
        return lines.filterIndexed { i, s -> i == 0 || s != lines[i - 1] }
    }

    /**
     * [b] continues [a]'s row: about the same top, to its right, sizes known,
     * and of a similar height (a container's own label doesn't run into the
     * first line inside it).
     */
    private fun sameRow(a: Piece, b: Piece): Boolean {
        if (a.h <= 0 || b.h <= 0 || a.w <= 0) return false
        if (a.h > 3 * b.h || b.h > 3 * a.h) return false
        if (kotlin.math.abs(a.y - b.y) * 2 >= minOf(a.h, b.h)) return false
        return b.x >= a.x
    }

    /** Appends the node's pieces; returns whether any text was found in it. */
    private fun walk(
        node: ScreenNode, parentX: Int, parentY: Int, clip: Boolean, vw: Int, vh: Int,
        out: MutableList<Piece>, w: Walk
    ): Boolean {
        if (++w.nodes > MAX_NODES) return false
        if (!node.visible) return false
        if (node.width == 0 || node.height in 0..2) return false
        val x = parentX + node.left
        val y = parentY + node.top
        if (clip && node.width > 0 && node.height > 0) {
            val outside = x + node.width <= 0 || y + node.height <= 0 || x >= vw || y >= vh
            if (outside) return false
        }
        val start = out.size
        val own = if (node.password) null else collapse(node.text)
        if (own != null) out.add(Piece(own, x, y, node.width, node.height))
        var found = own != null
        if (node.children.isNotEmpty()) {
            val childX = x - node.scrollX
            val childY = y - node.scrollY
            val childClip = clip && !(node.width > 0 && node.width > vw + vw / 10)
            val ordered = readingOrder(node.children)
            for (child in ordered) {
                if (walk(child, childX, childY, childClip, vw, vh, out, w)) found = true
            }
        }
        if (!found && !node.password) {
            val desc = collapse(node.contentDescription)
            if (desc != null) {
                out.add(start, Piece(desc, x, y, node.width, node.height))
                found = true
            }
        }
        return found
    }

    /**
     * Siblings in reading order: the app's own order (a web page's DOM order,
     * the view order), except that a node lying wholly above another comes
     * before it (a toolbar added after the content). Nodes on the same rows
     * keep their order — sorting by exact top would scatter a line of text.
     */
    internal fun readingOrder(children: List<ScreenNode>): List<ScreenNode> {
        if (children.size < 2) return children
        val placed = ArrayList<ScreenNode>(children.size)
        for (c in children) {
            val at = if (c.height > 0) placed.indexOfFirst { q -> q.height > 0 && c.top + c.height <= q.top } else -1
            if (at < 0) placed.add(c) else placed.add(at, c)
        }
        return placed
    }

    /** Whitespace collapsed but kept at the ends (web text runs carry their spaces). Null when blank. */
    private fun collapse(raw: CharSequence?): String? {
        val s = raw?.toString()?.replace(WHITESPACE, " ").orEmpty()
        return if (s.isBlank()) null else s
    }
}

/**
 * A window that blocks capture (FLAG_SECURE: banking apps, Chrome Incognito) still hands the assistant a
 * full-size screenshot, painted black. That is not a picture of anything, so it counts as no screenshot.
 */
object BlankFrame {
    /**
     * True when at least 99.5% of the frame, minus a band at the top and bottom where the system bars may
     * still draw, is near-black. A dark app with any text on it stays well under that.
     */
    fun isBlank(pixels: IntArray, width: Int, height: Int): Boolean {
        if (width <= 0 || height <= 0 || pixels.size < width * height) return false
        val band = height * 8 / 100
        var total = 0
        var lit = 0
        for (y in band until height - band) {
            val row = y * width
            for (x in 0 until width) {
                val p = pixels[row + x]
                val r = (p shr 16) and 0xFF
                val g = (p shr 8) and 0xFF
                val b = p and 0xFF
                total++
                if (r > 8 || g > 8 || b > 8) lit++
            }
        }
        return total > 0 && lit * 1000 <= total * 5
    }
}
