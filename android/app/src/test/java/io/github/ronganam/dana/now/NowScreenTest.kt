package io.github.ronganam.dana.now

import io.github.ronganam.dana.data.models.NowBlock
import io.github.ronganam.dana.data.network.NowApi
import io.github.ronganam.dana.ui.screens.now.NewCards
import io.github.ronganam.dana.ui.screens.now.NowDay
import io.github.ronganam.dana.ui.screens.now.NowModel
import io.github.ronganam.dana.ui.theme.Hue
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalTime

/** The Now screen's pure rules: hues by kind, swipes, the header, the day timeline, which cards rise in, unknown kinds. */
class NowScreenTest {

    private fun item(json: String) = NowApi.parse("""{"items":[$json]}""").items.single()

    @Test
    fun hueComesFromTheKindOnly() {
        assertEquals(Hue.EMBER, NowModel.hueOf("approval", "thread"))
        assertEquals(Hue.TEAL, NowModel.hueOf("notification", "notification"))
        assertEquals(Hue.PLUM, NowModel.hueOf("suggestion", "notification"))
        assertEquals(Hue.SAGE, NowModel.hueOf("suggestion", "call"))
        assertEquals(Hue.AMBER, NowModel.hueOf("reminder", "task"))
        assertEquals(Hue.ROSE, NowModel.hueOf("money", "transaction"))
        assertEquals(Hue.SAGE, NowModel.hueOf("money", "saving"))
        assertEquals(Hue.ROSE, NowModel.hueOf("check", "check"))
        assertEquals(Hue.SKY, NowModel.hueOf("review", "review"))
        // An addon's own kind (trains) and anything new: sky.
        assertEquals(Hue.SKY, NowModel.hueOf("israel_rail", "train"))
        assertEquals(Hue.SKY, NowModel.hueOf("something_new", null))
        assertEquals(Hue.AMBER, NowModel.dayHue("reminder"))
        assertEquals(Hue.SKY, NowModel.dayHue("event"))
        assertEquals(Hue.SKY, NowModel.dayHue("israel_rail"))
    }

    @Test
    fun unknownKindsRenderTheirFactsAndActions() {
        val train = item(
            """{"id":"israel_rail:x1","kind":"israel_rail","source":"Israel Railways","title":"Next train to work · 08:12",
               "why":"Busy · arrives 08:50","primary":{"action":"watch","label":"Watch this train"},
               "secondary":[{"action":"dismiss","label":"Hide"}],"ref":{"type":"train","id":"x1"},
               "blocks":[{"type":"facts","label":"Trip","items":["A → B","38 min, direct"]},{"type":"text","label":"Notice","text":"Works"}],
               "train":{"depart":"2026-11-22T08:12:00+02:00"}}"""
        )
        assertEquals("israel_rail", train.kind)
        assertEquals(listOf("A → B", "38 min, direct"), (train.blocks[0] as NowBlock.Facts).items)
        assertEquals("Works", (train.blocks[1] as NowBlock.Text).text)
        val a = NowModel.rowActions(train)
        assertEquals("watch", a.primary?.action)
        assertEquals("Hide", a.dismiss?.label)
        // No snooze of its own: swiping that way does nothing (never an invented snooze).
        assertNull(a.snooze)
    }

    @Test
    fun swipesUseTheCardsOwnSnoozeAndDismiss() {
        val reminder = item(
            """{"id":"task:1","kind":"reminder","title":"x","primary":{"action":"done","label":"Done"},
               "secondary":[{"action":"snooze","label":"1 h","args":{"minutes":60}},{"action":"snooze","label":"Tonight","args":{"until":"2026-11-22T20:00"}},{"action":"dismiss","label":"Dismiss"}]}"""
        )
        val a = NowModel.rowActions(reminder)
        assertEquals("1 h", a.snooze?.label)
        assertEquals("dismiss", a.dismiss?.action)
        val approval = item("""{"id":"approval:main","kind":"approval","title":"x","primary":{"action":"open","label":"Review","client":true}}""")
        assertNull(NowModel.rowActions(approval).snooze)
        assertNull(NowModel.rowActions(approval).dismiss)
    }

    @Test
    fun headerLines() {
        assertEquals("Nothing needs you right now.", NowModel.ledeFallback(9, queueEmpty = true))
        assertEquals("Good morning.", NowModel.ledeFallback(9, queueEmpty = false))
        assertEquals("Good evening.", NowModel.ledeFallback(20, queueEmpty = false))
        assertEquals("Late night.", NowModel.ledeFallback(2, queueEmpty = false))
        val t = 1_000_000_000L
        assertEquals("Updated just now", NowModel.updatedAgo(t, t + 30_000))
        assertEquals("Updated 2 min ago", NowModel.updatedAgo(t, t + 150_000))
        assertEquals("Updated 3 h ago", NowModel.updatedAgo(t, t + 3 * 3_600_000L))
        assertEquals("17 notifications · 1 needs you · 11 noise", NowModel.notificationsLine(17, 1, 11))
        assertEquals("1 notification", NowModel.notificationsLine(1, 0, 0))
        assertEquals("4 notifications · 2 need you", NowModel.notificationsLine(4, 2, 0))
    }

    @Test
    fun onlyNewCardsRiseIn() {
        val n = NewCards()
        assertEquals(emptySet<String>(), n.fresh(emptyList()))
        // The first look (cache or first answer) has the arrival animation instead.
        assertEquals(emptySet<String>(), n.fresh(listOf("a", "b")))
        assertEquals(setOf("c"), n.fresh(listOf("a", "b", "c")))
        // "b" acted on, then back after Undo: not new.
        assertEquals(emptySet<String>(), n.fresh(listOf("a", "c")))
        assertEquals(emptySet<String>(), n.fresh(listOf("a", "b", "c")))
    }

    private val slotsJson = """
    {"day":{"calendar":true,"slots":[
      {"key":"15","label":"15:00","items":[
         {"title":"Call the plumber","kind":"reminder","task_id":"t1","time":"14:40"},
         {"title":"Coffee","kind":"event","event_id":"e1","time":"14:10"},
         {"title":"Pick up the parcel","kind":"reminder","task_id":"t2","time":"15:20"}]},
      {"key":"16","label":"16:00","items":[]},
      {"key":"19","label":"19:00","items":[{"title":"Train 19:05 A → B","kind":"israel_rail","time":"19:05"}]},
      {"key":"home","label":"At home","items":[{"title":"Take out the trash","kind":"reminder","task_id":"t3"}]},
      {"key":"wake","label":"On waking","items":[]}]}}
    """

    @Test
    fun dayUpcomingInOrderWithNext() {
        val slots = NowApi.parse(slotsJson).daySlots
        val up = NowDay.upcoming(slots, LocalTime.of(14, 30))
        // Coffee (14:10) is gone; timed ones by time, then the place slot's.
        assertEquals(listOf("Call the plumber", "Pick up the parcel", "Train 19:05 A → B", "Take out the trash"), up.map { it.title })
        assertTrue(up[0].next)
        assertEquals(1, up.count { it.next })
        assertEquals("israel_rail", up[2].kind)
        assertEquals("At home", up[3].place)
        assertEquals("t3", up[3].taskId)
        assertNull(up[0].place)
    }

    @Test
    fun dayStripHoursPastAndBusy() {
        val slots = NowApi.parse(slotsJson).daySlots
        val seg = NowDay.segments(slots, LocalTime.of(14, 30))
        // 07:00 to 23:00, one per hour; before 14:00 past (never busy).
        assertEquals((7..23).toList(), seg.map { it.hour })
        assertEquals((7..13).toList(), seg.filter { it.past }.map { it.hour })
        // Busy by the item's own time (14:40 → 14, 15:20 → 15, 19:05 → 19); 14:10 is earlier but still in the current hour.
        assertEquals(listOf(14, 15, 19), seg.filter { it.busy }.map { it.hour })
        assertTrue(seg.none { it.past && it.busy })
    }

    @Test
    fun emptyDayStillHasItsStrip() {
        val empty = NowApi.parse("""{"day":{"slots":[{"key":"15","label":"15:00","items":[]},{"key":"home","label":"At home","items":[]}]}}""").daySlots
        assertTrue(NowDay.upcoming(empty, LocalTime.of(14, 0)).isEmpty())
        val seg = NowDay.segments(emptyList(), LocalTime.of(23, 5))
        assertEquals(17, seg.size)
        assertEquals(16, seg.count { it.past })
        assertTrue(seg.none { it.busy })
    }

    @Test
    fun nightAndUntimedItems() {
        // 01:00: the strip starts at 01:00; a 02:30 reminder is next, an untimed one counts in its slot's hour.
        val night = NowApi.parse(
            """{"day":{"slots":[{"key":"6","label":"06:00","items":[{"title":"Early","kind":"reminder","time":"02:30"},{"title":"Run","kind":"event"}]}]}}"""
        ).daySlots
        val up = NowDay.upcoming(night, LocalTime.of(1, 0))
        assertEquals(listOf("Early", "Run"), up.map { it.title })
        assertTrue(up[0].next)
        val seg = NowDay.segments(night, LocalTime.of(1, 0))
        assertEquals(1, seg.first().hour)
        assertEquals(listOf(2, 6), seg.filter { it.busy }.map { it.hour })
        assertEquals(null, NowDay.minutesOf("soon"))
        assertEquals(19 * 60 + 5, NowDay.minutesOf("19:05"))
    }
}
