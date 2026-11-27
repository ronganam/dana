package io.github.ronganam.dana.now

import io.github.ronganam.dana.data.network.LiveApi
import io.github.ronganam.dana.ui.screens.now.LiveGlyph
import io.github.ronganam.dana.ui.screens.now.LiveTrailing
import io.github.ronganam.dana.ui.screens.now.NowLive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId

/** Now's live part: which activities need you, what a row shows at its end. */
class NowLiveTest {

    private val payload = """
    {"activities":[
      {"id":"phone:claude:s1","addon":"phone","source":"Phone","title":"Tidy the trip planner","text":"Running the tests · step 2/4",
       "context":"Claude Code · trip-planner","icon":"bot","tone":"neutral","since":"2026-11-27T13:29:20+02:00",
       "progress":{"segments":[{"length":1},{"length":1},{"length":1},{"length":1}],"value":2},
       "actions":[{"id":"stop","label":"Hide","kind":"stop"}]},
      {"id":"watches:run:x:1","addon":"watches","title":"Reply to the courier","text":"Send a WhatsApp","context":"Watch · needs your OK",
       "icon":"bot","tone":"warn","open":"/chat/abc",
       "actions":[{"id":"approve","label":"Approve"},{"id":"deny","label":"Deny"}]},
      {"id":"israel_rail:t1","addon":"israel_rail","title":"17:46 to Haifa","text":"3 min late","icon":"train-front",
       "countdown":{"to":"2026-11-27T17:49:00+02:00","label":"Departs"}},
      {"id":"phone:claude:s2","addon":"phone","title":"Needs you · Fix the build","context":"Codex · dana","icon":"bot","tone":"warn"},
      {"id":"x:old","title":"Arrived","ended":true,"final":{"title":"Arrived"}},
      {"id":"x:run","title":"Indexing","icon":"bot","context":"Watch · Dana is working","progress":{"indeterminate":true}}
    ]}
    """

    private val rows = NowLive.rows(LiveApi.parse(payload))

    @Test
    fun waitingFirstEndedLeftOut() {
        assertEquals(listOf("watches:run:x:1", "phone:claude:s2", "phone:claude:s1", "israel_rail:t1", "x:run"), rows.map { it.id })
        assertTrue(rows[0].waiting)
        assertTrue(rows[1].waiting)
        assertFalse(rows[2].waiting)
    }

    @Test
    fun aWaitingSessionDropsItsPrefixAndKeepsOnlyServerButtons() {
        assertEquals("Fix the build", rows[1].title)
        // No text: the context is the line.
        assertEquals("Codex · dana", rows[1].line)
        assertEquals(listOf("approve", "deny"), rows[0].actions.map { it.id })
        assertTrue(rows[2].actions.isEmpty())
        assertEquals("/chat/abc", rows[0].open)
    }

    @Test
    fun glyphsByIconAndContext() {
        assertEquals(LiveGlyph.CODE, rows[2].glyph)
        assertEquals(LiveGlyph.CODE, rows[1].glyph)
        assertEquals(LiveGlyph.RUN, rows[0].glyph)
        assertEquals(LiveGlyph.TRAIN, rows[3].glyph)
    }

    @Test
    fun trailingStepsCountdownSinceOrNothing() {
        assertEquals(LiveTrailing.Steps(2, 4), rows[2].trailing)
        val at = rows[3].trailing as LiveTrailing.At
        assertEquals("17:49", NowLive.clock(at.toMs, ZoneId.of("Asia/Jerusalem")))
        assertEquals(LiveTrailing.None, rows[4].trailing)
        assertEquals(LiveTrailing.None, rows[0].trailing)
    }

    @Test
    fun elapsedInPlainWords() {
        assertEquals("now", NowLive.elapsed(0, 30_000))
        assertEquals("14 min", NowLive.elapsed(0, 14 * 60_000L))
        assertEquals("2 h", NowLive.elapsed(0, 120 * 60_000L))
        assertEquals("2 h 5 min", NowLive.elapsed(0, 125 * 60_000L))
    }
}
