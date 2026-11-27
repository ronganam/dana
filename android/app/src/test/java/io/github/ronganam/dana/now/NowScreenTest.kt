package io.github.ronganam.dana.now

import io.github.ronganam.dana.data.models.NowBlock
import io.github.ronganam.dana.data.network.NowApi
import io.github.ronganam.dana.ui.screens.now.DayLineNode
import io.github.ronganam.dana.ui.screens.now.NewCards
import io.github.ronganam.dana.ui.screens.now.calmLine
import io.github.ronganam.dana.ui.screens.now.placeLabel
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
        assertEquals("Good morning", NowModel.greeting(9))
        assertEquals("Good afternoon", NowModel.greeting(13))
        assertEquals("Good evening", NowModel.greeting(20))
        assertEquals("Good evening", NowModel.greeting(2))
        val t = 1_000_000_000L
        assertEquals("Updated just now", NowModel.updatedAgo(t, t + 30_000))
        assertEquals("Updated 2 min ago", NowModel.updatedAgo(t, t + 150_000))
        assertEquals("Updated 3 h ago", NowModel.updatedAgo(t, t + 3 * 3_600_000L))
    }

    @Test
    fun calmLineIsTheLedeElseWhatIsKeptQuiet() {
        assertEquals("Standup at 09:30.", calmLine(" Standup at 09:30. ", 4))
        assertEquals("Dana is keeping 4 notifications quiet.", calmLine(null, 4))
        assertEquals("Dana is keeping 1 notification quiet.", calmLine("", 1))
        assertNull(calmLine(null, 0))
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
    fun placeEntriesCarryTheirSlotKeyAndAShortLabel() {
        val up = NowDay.upcoming(NowApi.parse(slotsJson).daySlots, LocalTime.of(14, 30))
        assertEquals("home", up[3].placeKey)
        assertEquals("Home", placeLabel(up[3]))
        assertNull(up[0].placeKey)
    }

    @Test
    fun emptyDayHasNothingUpcoming() {
        val empty = NowApi.parse("""{"day":{"slots":[{"key":"15","label":"15:00","items":[]},{"key":"home","label":"At home","items":[]}]}}""").daySlots
        assertTrue(NowDay.upcoming(empty, LocalTime.of(14, 0)).isEmpty())
    }

    @Test
    fun nightAndUntimedItems() {
        // 01:00: a 02:30 reminder is next; an untimed one takes its slot's hour as its time.
        val night = NowApi.parse(
            """{"day":{"slots":[{"key":"6","label":"06:00","items":[{"title":"Early","kind":"reminder","time":"02:30"},{"title":"Run","kind":"event"}]}]}}"""
        ).daySlots
        val up = NowDay.upcoming(night, LocalTime.of(1, 0))
        assertEquals(listOf("Early", "Run"), up.map { it.title })
        assertEquals("06:00", up[1].time)
        assertTrue(up[0].next)
        assertEquals(null, NowDay.minutesOf("soon"))
        assertEquals(19 * 60 + 5, NowDay.minutesOf("19:05"))
    }

    private fun task(id: String, status: String, updated: String?) =
        io.github.ronganam.dana.data.models.Task(id = id, title = "T$id", status = status, updatedAt = updated)

    @Test
    fun doneTodayReadsTheServersUtcTimesInTheLocalZone() {
        val zone = java.time.ZoneId.of("Asia/Jerusalem") // +02:00 in November
        val today = java.time.LocalDate.of(2026, 11, 27)
        val done = NowDay.doneToday(
            listOf(
                task("1", "completed", "2026-11-27 07:30:00"), // 09:30 local
                task("2", "completed", "2026-11-26 23:10:00"), // 01:10 local, today
                task("3", "completed", "2026-11-26 21:00:00"), // 23:00 yesterday
                task("4", "needs_action", "2026-11-27 08:00:00"),
                task("5", "completed", "2026-11-27T10:15:00+02:00"),
                task("6", "completed", null)
            ),
            today, zone
        )
        assertEquals(listOf("5", "1", "2"), done.map { it.taskId })
        assertEquals(LocalTime.of(9, 30), done[1].at)
    }

    @Test
    fun calmDayLinePlacesDoneNowAndAheadByTime() {
        // Done 09:00, now 13:00, ahead 16:00: the line runs 08:00–16:00.
        val nodes = NowDay.dayLine(listOf(9 * 60), listOf(16 * 60, 10 * 60), 13 * 60)
        assertEquals(listOf(DayLineNode.Kind.START, DayLineNode.Kind.DONE, DayLineNode.Kind.NOW, DayLineNode.Kind.NEXT), nodes.map { it.kind })
        assertEquals(0f, nodes[0].fraction, 0.001f)
        assertEquals(0.125f, nodes[1].fraction, 0.001f)
        assertEquals(0.625f, nodes[2].fraction, 0.001f)
        assertEquals(1f, nodes[3].fraction, 0.001f)
        // 08:00 and 09:00 are too close for both labels: the first placed keeps it.
        assertEquals(listOf("08:00", null, "Now", "16:00"), nodes.map { it.label })
        // A time close to Now loses its label; one on top of Now leaves the line; nothing at all → no line.
        val close = NowDay.dayLine(listOf(12 * 60 + 40), emptyList(), 13 * 60)
        assertNull(close.first { it.kind == DayLineNode.Kind.DONE }.label)
        val onTop = NowDay.dayLine(listOf(13 * 60), listOf(18 * 60), 13 * 60)
        assertTrue(onTop.none { it.kind == DayLineNode.Kind.DONE })
        assertTrue(NowDay.dayLine(emptyList(), listOf(9 * 60), 13 * 60).isEmpty())
    }
}
