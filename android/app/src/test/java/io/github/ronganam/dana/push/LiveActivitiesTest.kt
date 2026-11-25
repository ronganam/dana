package io.github.ronganam.dana.push

import io.github.ronganam.dana.data.models.LiveAction
import io.github.ronganam.dana.data.models.LiveActivity
import io.github.ronganam.dana.data.models.LiveChoice
import io.github.ronganam.dana.data.models.LiveCountdown
import io.github.ronganam.dana.data.models.LiveFinal
import io.github.ronganam.dana.data.network.LiveApi
import io.github.ronganam.dana.push.LiveActivities.Open
import io.github.ronganam.dana.push.LiveActivities.Shown
import io.github.ronganam.dana.push.LiveActivities.Step
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LiveActivitiesTest {
    private fun activity(id: String = "a", rev: Int = 1, alert: String = "", ended: Boolean = false, final: LiveFinal? = null) =
        LiveActivity(id = id, addon = "x", source = "X", rev = rev, title = "T", alert = alert, ended = ended, final = final)

    private val train = LiveActivity(
        id = "t", title = "Departs 17:46", text = "Platform 3", context = "A → B", icon = "train-front", tone = "warn",
        countdown = LiveCountdown("2026-11-25T17:46:00+02:00", "Departs"), open = "/israel_rail",
        actions = listOf(LiveAction("prev", "Earlier", "prev"), LiveAction("next", "Next train", "next"), LiveAction("stop", "Stop", "stop")),
        choices = listOf(
            LiveChoice("404"),
            LiveChoice("406", title = "Departs 17:52", tone = "good", countdown = LiveCountdown("2026-11-25T17:52:00+02:00", "Departs")),
            LiveChoice("408", text = "Platform 2"),
        ),
    )

    // plan()

    @Test
    fun runningIsRepostedSilently() {
        val a = activity()
        assertEquals(listOf<Step>(Step.Live(a, alert = false)), LiveActivities.plan(mapOf("a" to Shown(1, "", true)), listOf(a)))
        assertEquals(listOf<Step>(Step.Live(a, alert = false)), LiveActivities.plan(emptyMap(), listOf(a)))
    }

    @Test
    fun aNewAlertAlertsOnce() {
        val a = activity(rev = 2, alert = "delay:4")
        assertEquals(listOf<Step>(Step.Live(a, alert = true)), LiveActivities.plan(mapOf("a" to Shown(1, "", true)), listOf(a)))
        val shown = LiveActivities.nextShown(mapOf("a" to Shown(1, "", true)), listOf(a))
        assertEquals(Shown(2, "delay:4", true), shown["a"])
        // Same alert on a later rev: silent.
        val later = activity(rev = 3, alert = "delay:4")
        assertEquals(listOf<Step>(Step.Live(later, alert = false)), LiveActivities.plan(shown, listOf(later)))
        // A different one alerts again; an empty one never alerts and keeps the last.
        assertEquals(listOf<Step>(Step.Live(activity(rev = 4, alert = "delay:9"), alert = true)),
            LiveActivities.plan(shown, listOf(activity(rev = 4, alert = "delay:9"))))
        assertEquals("delay:4", LiveActivities.nextShown(shown, listOf(activity(rev = 5)))["a"]!!.alert)
    }

    @Test
    fun aFinalIsPostedOncePerRevThenForgotten() {
        val done = activity(rev = 5, ended = true, final = LiveFinal("Done"))
        assertEquals(listOf<Step>(Step.Final(done)), LiveActivities.plan(mapOf("a" to Shown(4, "", true)), listOf(done)))
        // Never seen running (the phone was offline): still posted.
        assertEquals(listOf<Step>(Step.Final(done)), LiveActivities.plan(emptyMap(), listOf(done)))
        val shown = LiveActivities.nextShown(emptyMap(), listOf(done))
        assertEquals(Shown(5, "", false), shown["a"])
        assertEquals(emptyList<Step>(), LiveActivities.plan(shown, listOf(done)))
        // Gone from the list: the posted final stays, the id is forgotten.
        assertEquals(emptyList<Step>(), LiveActivities.plan(shown, emptyList()))
        assertEquals(emptyMap<String, Shown>(), LiveActivities.nextShown(shown, emptyList()))
    }

    @Test
    fun missingOrEndedWithoutFinalIsCancelled() {
        val shown = mapOf("a" to Shown(1, "", true), "b" to Shown(2, "", true))
        assertEquals(listOf<Step>(Step.Cancel("a"), Step.Cancel("b")),
            LiveActivities.plan(shown, listOf(activity(id = "a", rev = 2, ended = true))))
        assertEquals(emptyList<Step>(), LiveActivities.plan(emptyMap(), listOf(activity(ended = true))))
    }

    // The display merge

    @Test
    fun theSelectedChoiceOverridesTheTopLevel() {
        val first = train.look()
        assertEquals("Departs 17:46", first.title)
        assertEquals("warn", first.tone)
        val second = train.copy(selected = "406").look()
        assertEquals("Departs 17:52", second.title)
        assertEquals("good", second.tone)
        assertEquals("2026-11-25T17:52:00+02:00", second.countdown!!.to)
        // Fields the choice leaves out stay the activity's.
        assertEquals("Platform 3", second.text)
        assertEquals("A → B", second.context)
        assertEquals("/israel_rail", second.open)
        assertEquals("Platform 2", train.copy(selected = "408").look().text)
        // An unknown key falls back to the first choice.
        assertEquals("404", train.copy(selected = "999").selectedKey)
        assertEquals("", activity().selectedKey)
    }

    // next / prev

    @Test
    fun nextAndPrevStopAtTheEnds() {
        assertNull(LiveActivities.neighbour(train, -1))
        assertEquals("406", LiveActivities.neighbour(train, 1))
        assertEquals("404", LiveActivities.neighbour(train.copy(selected = "406"), -1))
        assertNull(LiveActivities.neighbour(train.copy(selected = "408"), 1))
        assertNull(LiveActivities.neighbour(activity(), 1))
    }

    @Test
    fun nextIsHiddenOnTheLastChoiceAndPrevOnTheFirst() {
        assertEquals(listOf("next", "stop"), LiveActivities.visibleActions(train).map { it.id })
        assertEquals(listOf("prev", "next", "stop"), LiveActivities.visibleActions(train.copy(selected = "406")).map { it.id })
        assertEquals(listOf("prev", "stop"), LiveActivities.visibleActions(train.copy(selected = "408")).map { it.id })
        assertEquals(listOf("stop"), LiveActivities.visibleActions(train.copy(choices = emptyList())).map { it.id })
    }

    // Tap targets, icons

    @Test
    fun openGoesToHttpsAScreenOrTheWebApp() {
        val server = "https://dana.example.org/"
        assertEquals(Open.Web("https://trains.example.org/x"), LiveActivities.openTarget("https://trains.example.org/x", server))
        assertEquals(Open.Screen("money"), LiveActivities.openTarget("/money/plan", server))
        assertEquals(Open.Web("https://dana.example.org/israel_rail"), LiveActivities.openTarget("/israel_rail", server))
        assertEquals(Open.Home, LiveActivities.openTarget("", server))
        assertEquals(Open.Home, LiveActivities.openTarget("http://plain.example.org", server))
        assertEquals(Open.Home, LiveActivities.openTarget("/israel_rail", ""))
    }

    @Test
    fun everyServerIconHasADrawable() {
        val server = listOf("train-front", "tram-front", "bus", "car", "bike", "footprints", "plane", "ship", "timer", "clock",
            "bot", "terminal", "package", "truck", "chef-hat", "utensils", "shopping-cart", "dumbbell", "sprout",
            "droplets", "flame", "bell", "map-pin", "house", "sparkles", "zap")
        assertEquals(server.toSet(), LiveActivities.ICONS.keys)
        assertEquals(io.github.ronganam.dana.R.drawable.ic_launcher_monochrome, LiveActivities.icon("unicorn"))
    }

    // The server's payload (contract)

    @Test
    fun parsesTheServerFixture() {
        val text = LiveActivitiesTest::class.java.classLoader!!.getResource("live_web.json")!!.readText()
        val list = LiveApi.parse(text)
        assertTrue(list.isNotEmpty())
        list.forEach { a ->
            assertTrue(a.id.isNotBlank())
            assertTrue(a.title.isNotBlank() || a.final != null)
            assertTrue(a.tone in LiveActivities.TONES)
            a.choices.forEach { assertTrue(it.key.isNotBlank()) }
            assertTrue(a.selected.isEmpty() || a.choices.any { it.key == a.selected })
            assertTrue(a.icon.isEmpty() || a.icon in LiveActivities.ICONS)
            a.progress?.let { p -> assertTrue(p.tracker.isEmpty() || p.tracker in LiveActivities.ICONS) }
            assertTrue(a.actions.size <= 3)
            if (a.ended) assertNotNull(a.final)
            assertNotNull(a.updatedMs)
            if (a.countdown != null) assertNotNull(a.countdown!!.toMs)
            if (a.endsAt.isNotEmpty()) assertNotNull(a.endsAtMs)
        }
        // Something to show for every kind of field the renderer reads.
        val running = list.first { !it.ended && it.choices.size > 1 }
        assertNotNull(running.look().countdown?.toMs)
        assertNotNull(running.progress)
        assertTrue(running.actions.any { it.kind == "next" })
        val other = LiveActivities.neighbour(running, 1)!!
        assertFalse(running.copy(selected = other).look() == running.look())
        val timer = list.first { it.since.isNotEmpty() }
        assertNotNull(timer.look().sinceMs)
        // A cached copy reads back the same.
        assertEquals(list, LiveApi.parse(LiveApi.encode(list)))
    }

    @Test
    fun parsingIsLenient() {
        val list = LiveApi.parse("""{"activities": [{"id": "a", "title": "T", "new_field": {"x": 1}, "tone": "good"}, {"title": "no id"}, 7]}""")
        assertEquals(1, list.size)
        assertEquals("good", list[0].tone)
        assertTrue(list[0].actions.isEmpty())
        assertNull(list[0].progress)
        assertEquals(emptyList<LiveActivity>(), LiveApi.parse("not json"))
    }
}
