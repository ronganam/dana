package io.github.ronganam.dana.screen

import io.github.ronganam.dana.data.screen.BlankFrame
import io.github.ronganam.dana.data.screen.DeviceScreen
import io.github.ronganam.dana.data.screen.ScreenNode
import io.github.ronganam.dana.data.screen.ScreenText
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ScreenTextTest {

    private fun box(left: Int, top: Int, w: Int, h: Int, text: String? = null, desc: String? = null, vararg kids: ScreenNode) =
        ScreenNode(text = text, contentDescription = desc, left = left, top = top, width = w, height = h, children = kids.toList())

    @Test
    fun siblingsReadTopToBottomButKeepTheAppOrderOnARow() {
        // The app added its toolbar after the content: the toolbar is wholly above, so it comes first.
        val root = box(
            0, 0, 1000, 2000, null, null,
            box(0, 200, 1000, 100, "First paragraph"),
            box(0, 400, 1000, 100, "Second paragraph"),
            box(0, 0, 1000, 150, "Inbox")
        )
        assertEquals(listOf("Inbox", "First paragraph", "Second paragraph"), ScreenText.lines(root, 1000, 2000))
    }

    @Test
    fun piecesOfOneLineJoinWithTheirOwnSpacingAndLabelsGetASpace() {
        // A web paragraph split into a text run, a link and another run (adjacent boxes).
        val para = box(
            0, 0, 1000, 100, null, null,
            box(0, 0, 300, 50, "Not to be confused with "),
            box(300, 0, 200, 50, "Nespresso"),
            box(500, 0, 30, 50, ".")
        )
        // Two labels side by side with a gap (a name and a time in a chat row).
        val row = box(0, 100, 1000, 60, null, null, box(0, 0, 200, 60, "Mom"), box(800, 0, 150, 60, "3:42 PM"))
        val root = box(0, 0, 1000, 2000, null, null, para, row)
        assertEquals(listOf("Not to be confused with Nespresso.", "Mom 3:42 PM"), ScreenText.lines(root, 1000, 2000))
    }

    @Test
    fun hiddenTransparentEmptyAndOffScreenNodesAreSkipped() {
        val root = box(
            0, 0, 1000, 2000, null, null,
            ScreenNode(text = "hidden", visible = false, left = 0, top = 0, width = 100, height = 50,
                children = listOf(box(0, 0, 100, 50, "child of hidden"))),
            box(0, 100, 0, 0, "zero size"),
            box(0, 150, 100, 2, "collapsed"),
            box(0, 200, 100, 50, "   "),
            box(0, 5000, 100, 50, "below the screen"),
            box(-500, 300, 100, 50, "left of the screen"),
            box(0, 400, 100, 50, "visible")
        )
        assertEquals(listOf("visible"), ScreenText.lines(root, 1000, 2000))
    }

    @Test
    fun scrolledContentIsClippedByItsScrollOffset() {
        val list = ScreenNode(
            left = 0, top = 0, width = 1000, height = 2000, scrollY = 3000,
            children = listOf(box(0, 100, 1000, 100, "scrolled away"), box(0, 3100, 1000, 100, "on screen now"))
        )
        assertEquals(listOf("on screen now"), ScreenText.lines(box(0, 0, 1000, 2000, null, null, list), 1000, 2000))
    }

    @Test
    fun webContentWiderThanTheWindowIsNotClipped() {
        // Chrome reports page coordinates at its own scale: 2810 wide in a 1248 px window.
        val page = box(0, 0, 2810, 3900, "Espresso - Wikipedia", null, box(80, 2875, 2600, 96, "Espresso is a concentrated form of coffee"))
        val lines = ScreenText.lines(box(0, 0, 1248, 1972, null, null, box(0, 112, 1248, 1860, null, "Web View", page)), 1248, 1972)
        assertEquals(listOf("Espresso - Wikipedia", "Espresso is a concentrated form of coffee"), lines)
    }

    @Test
    fun passwordsNeverAndDescriptionsOnlyWhenNothingInsideHasText() {
        val root = box(
            0, 0, 1000, 2000, null, null,
            ScreenNode(text = "hunter2", password = true, left = 0, top = 0, width = 500, height = 80),
            // A row that repeats its children in its description: the children win.
            box(0, 100, 1000, 120, null, "Mom, Running late, 3:42 PM", box(0, 0, 600, 60, "Mom"), box(0, 60, 600, 60, "Running late")),
            // An icon button: only its description says what it is.
            box(0, 300, 100, 100, null, "Send")
        )
        assertEquals(listOf("Mom", "Running late", "Send"), ScreenText.lines(root, 1000, 2000))
    }

    @Test
    fun whitespaceCollapsesAndRepeatsAreSaidOnce() {
        val root = box(
            0, 0, 1000, 2000, null, null,
            box(0, 0, 1000, 100, "  Hello\n\n   world  "),
            box(0, 200, 1000, 100, "Hello world"),
            box(0, 400, 1000, 100, "Next")
        )
        assertEquals(listOf("Hello world", "Next"), ScreenText.lines(root, 1000, 2000))
    }

    @Test
    fun badPositionsFallBackToTheUnclippedText() {
        // Everything reported far off the window (an app with odd coordinates).
        val root = box(0, 0, 1000, 2000, null, null, box(0, 90_000, 500, 50, "still useful"))
        assertEquals(listOf("still useful"), ScreenText.lines(root, 1000, 2000))
    }

    @Test
    fun capsLinesAndTotal() {
        val long = "x".repeat(ScreenText.MAX_LINE + 50)
        val line = ScreenText.lines(box(0, 0, 1000, 2000, null, null, box(0, 0, 1000, 100, long)), 1000, 2000).single()
        assertEquals(ScreenText.MAX_LINE, line.length)
        assertTrue(line.endsWith("…"))

        val many = (1..400).map { "Line number $it with some words" }
        val text = ScreenText.text(listOf(many), maxChars = 1000)
        assertTrue("capped: ${text.length}", text.length <= 1000)
        assertTrue(text.startsWith("Line number 1 "))
        assertTrue(text.endsWith("…"))
        // Windows join in order; the same line twice across windows is said once.
        assertEquals("a\nb\nc", ScreenText.text(listOf(listOf("a", "b"), listOf("b", "c"))))
    }

    @Test
    fun nodeBudgetStopsAHugeTree() {
        val kids = (0 until ScreenText.MAX_NODES + 500).map { box(0, it * 100, 10, 50, "n$it") }
        val lines = ScreenText.lines(ScreenNode(children = kids))
        // The root counts too.
        assertEquals(ScreenText.MAX_NODES - 1, lines.size)
        assertEquals("n0", lines.first())
    }

    @Test
    fun titlesThatAreComponentNamesAreDropped() {
        assertNull(ScreenText.cleanTitle("com.whatsapp/com.whatsapp.Conversation", "com.whatsapp"))
        assertNull(ScreenText.cleanTitle("com.android.chrome", "com.android.chrome"))
        assertNull(ScreenText.cleanTitle("org.chromium.chrome.browser.ChromeTabbedActivity", null))
        assertNull(ScreenText.cleanTitle("   ", "x"))
        assertEquals("Inbox – Gmail", ScreenText.cleanTitle(" Inbox –  Gmail ", "com.google.android.gm"))
        assertEquals("מתכון לעוגה", ScreenText.cleanTitle("מתכון לעוגה", "com.app"))
    }

    @Test
    fun aBlackedOutSecureFrameCountsAsNoScreenshot() {
        val w = 100
        val h = 200
        val black = IntArray(w * h) { 0xFF000000.toInt() }
        assertTrue(BlankFrame.isBlank(black, w, h))
        // The status bar still draws on top of a secure window.
        val withBars = black.copyOf().also { for (i in 0 until w * 10) it[i] = 0xFFFFFFFF.toInt() }
        assertTrue(BlankFrame.isBlank(withBars, w, h))
        // A dark app with a few lines of text is a real picture.
        val darkApp = black.copyOf().also { for (y in 60 until 64) for (x in 10 until 90) it[y * w + x] = 0xFFE0E0E0.toInt() }
        assertFalse(BlankFrame.isBlank(darkApp, w, h))
        assertFalse(BlankFrame.isBlank(IntArray(w * h) { 0xFF1E1E1E.toInt() }, w, h))
        assertFalse(BlankFrame.isBlank(IntArray(0), 0, 0))
    }

    @Test
    fun deviceScreenJsonLeavesBlanksOut() {
        val json = DeviceScreen(app = "Chrome", packageName = "com.android.chrome", title = " ", url = null, text = "Hi").toJson()
        assertEquals(setOf("app", "package", "text"), json.keys().asSequence().toSet())
        assertEquals("com.android.chrome", json.getString("package"))
        assertFalse(DeviceScreen(app = "X").hasText)
        assertEquals(0, DeviceScreen().toJson().length())
    }
}
