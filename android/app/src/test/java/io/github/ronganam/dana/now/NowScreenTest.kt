package io.github.ronganam.dana.now

import io.github.ronganam.dana.data.models.NowBlock
import io.github.ronganam.dana.data.network.NowApi
import io.github.ronganam.dana.ui.screens.now.DayColumn
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
    fun dayTimelineColumnsPastAndNext() {
        val slots = NowApi.parse(slotsJson).daySlots
        val cols = NowDay.columns(slots, LocalTime.of(14, 30))
        assertEquals(listOf("now", "15", "19", "home"), cols.map { it.key })
        assertEquals(listOf(DayColumn.Type.NOW, DayColumn.Type.HOUR, DayColumn.Type.HOUR, DayColumn.Type.PLACE), cols.map { it.type })
        // Before the first hour slot: under the now marker, by time; earlier than now = past.
        val now = cols[0].entries
        assertEquals(listOf("Coffee", "Call the plumber"), now.map { it.title })
        assertTrue(now[0].past)
        assertTrue(now[1].next)
        assertEquals(1, cols.flatMap { it.entries }.count { it.next })
        assertEquals(listOf("Pick up the parcel"), cols[1].entries.map { it.title })
        assertEquals("israel_rail", cols[2].entries.single().kind)
        assertEquals("t3", cols[3].entries.single().taskId)
    }

    @Test
    fun emptyDayHasNoTimeline() {
        val empty = NowApi.parse("""{"day":{"slots":[{"key":"15","label":"15:00","items":[]},{"key":"home","label":"At home","items":[]}]}}""").daySlots
        assertTrue(NowDay.columns(empty, LocalTime.of(14, 0)).isEmpty())
        assertTrue(NowDay.columns(emptyList(), LocalTime.of(14, 0)).isEmpty())
    }

    @Test
    fun nightAndUntimedItems() {
        // 01:00: hour slots start at 06:00; a 02:30 reminder sits under the marker and is next.
        val night = NowApi.parse(
            """{"day":{"slots":[{"key":"6","label":"06:00","items":[{"title":"Early","kind":"reminder","time":"02:30"},{"title":"Run","kind":"event","time":"06:15"}]}]}}"""
        ).daySlots
        val cols = NowDay.columns(night, LocalTime.of(1, 0))
        assertEquals(listOf("Early"), cols[0].entries.map { it.title })
        assertTrue(cols[0].entries[0].next)
        assertEquals(listOf("Run"), cols[1].entries.map { it.title })
        assertEquals(null, NowDay.minutesOf("soon"))
        assertEquals(19 * 60 + 5, NowDay.minutesOf("19:05"))
    }
}
