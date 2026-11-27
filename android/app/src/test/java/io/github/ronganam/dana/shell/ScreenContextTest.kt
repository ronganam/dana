package io.github.ronganam.dana.shell

import io.github.ronganam.dana.ui.viewmodels.MoreItem
import io.github.ronganam.dana.ui.viewmodels.Place
import io.github.ronganam.dana.ui.viewmodels.ScreenContext
import io.github.ronganam.dana.ui.viewmodels.ScreenRef
import io.github.ronganam.dana.ui.viewmodels.ShellController
import io.github.ronganam.dana.ui.viewmodels.ShellSnapshot
import io.github.ronganam.dana.ui.viewmodels.ThreadContext
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ScreenContextTest {

    @Test
    fun nowItemsMapToKinds() {
        assertEquals("suggestion" to "12", ScreenContext.fromNowItem("sug:12", null, null))
        assertEquals("task" to "t1", ScreenContext.fromNowItem("task:t1", "task", "t1"))
        assertEquals("notification" to "88", ScreenContext.fromNowItem("notif:88", "notification", "88"))
        assertEquals("call" to "c7", ScreenContext.fromNowItem("x:1", "call", "c7"))
        // Approvals (their thread) and checks carry nothing.
        assertNull(ScreenContext.fromNowItem("approval:main", "thread", "main"))
        assertNull(ScreenContext.fromNowItem("check:push", "check", "push"))
    }

    @Test
    fun shellStateMapsToScreen() {
        assertEquals(
            ScreenRef("suggestion", "12", "Call the garage"),
            ScreenContext.forShell(ShellSnapshot(Place.NOW, nowItem = ShellSnapshot.NowPick("sug:12", "Call the garage", "call", "c1")))
        )
        assertEquals(ScreenRef("task", "t1", "לקנות חלב"), ScreenContext.forShell(ShellSnapshot(Place.REMINDERS, reminder = "t1" to "לקנות חלב")))
        assertEquals(
            ScreenRef("notification", "n9", "WhatsApp"),
            ScreenContext.forShell(ShellSnapshot(Place.INBOX, notifApp = ShellSnapshot.NotifPick("com.whatsapp", "WhatsApp", "n9")))
        )
        assertNull(ScreenContext.forShell(ShellSnapshot(Place.INBOX, notifApp = ShellSnapshot.NotifPick("com.whatsapp", "WhatsApp", null))))
        assertEquals(ScreenRef("call", "c1", "Mom"), ScreenContext.forShell(ShellSnapshot(Place.MORE, more = MoreItem.CALLS, call = "c1" to "Mom")))
        assertEquals(ScreenRef("person", "p1", "Gal"), ScreenContext.forShell(ShellSnapshot(Place.MORE, more = MoreItem.PEOPLE, person = "p1" to "Gal")))
        assertEquals("review", ScreenContext.forShell(ShellSnapshot(Place.MORE, more = MoreItem.REVIEW, reviewId = "w46"))?.kind)
        // A person picked earlier doesn't count while Calls is open.
        assertNull(ScreenContext.forShell(ShellSnapshot(Place.MORE, more = MoreItem.CALLS, person = "p1" to "Gal")))
        assertNull(ScreenContext.forShell(ShellSnapshot(Place.MORE, more = MoreItem.ABOUT)))
    }

    @Test
    fun effectiveContextRules() {
        val a = ScreenRef("task", "1")
        val b = ScreenRef("task", "2")
        // Cover: only the pin counts.
        assertEquals(a, ScreenContext.effective(true, b, a, b, null))
        assertNull(ScreenContext.effective(true, b, null, null, null))
        // Unfolded: the pin wins while the screen hasn't changed, then the screen.
        assertEquals(a, ScreenContext.effective(false, b, a, b, null))
        assertEquals(b, ScreenContext.effective(false, b, a, null, null))
        // Dropped stays dropped until the screen shows something else.
        assertNull(ScreenContext.effective(false, b, null, null, b))
        assertEquals(a, ScreenContext.effective(false, a, null, null, b))
    }

    @Test
    fun coverPinsWhatIsOpenWhenTheThreadOpensAndForgetsItOnClose() {
        val shell = ShellController(titleFor = { _, id -> "Title $id" })
        shell.setStackedLayout(true)
        shell.setLive(ScreenRef("task", "t1", "Milk"))
        assertNull("the cover shows nothing until the thread opens", shell.screen.value)
        shell.openThread()
        assertEquals(ScreenRef("task", "t1", "Milk"), shell.screen.value)
        shell.closeThread()
        assertNull(shell.screen.value)
        // "Ask Dana about this" pins with a looked-up title.
        shell.openThread(ThreadContext("call", "c1"))
        assertEquals(ScreenRef("call", "c1", "Title c1"), shell.screen.value)
        shell.dropScreen()
        assertNull(shell.screen.value)
    }

    @Test
    fun unfoldedFollowsTheScreenAndTheDropLastsUntilItChanges() {
        val shell = ShellController()
        shell.setStackedLayout(false)
        shell.setLive(ScreenRef("task", "t1"))
        assertEquals("t1", shell.screen.value?.id)
        shell.dropScreen()
        assertNull(shell.screen.value)
        shell.setLive(ScreenRef("task", "t2"))
        assertEquals("t2", shell.screen.value?.id)
        // A deep-link pin wins until the screen moves on.
        shell.openThread(ThreadContext("call", "c9", "Mom"))
        assertEquals("c9", shell.screen.value?.id)
        shell.setLive(ScreenRef("task", "t3"))
        assertEquals("t3", shell.screen.value?.id)
    }

    @Test
    fun backFromSettingsGoesToAboutYou() {
        val shell = ShellController()
        shell.openRoute("settings")
        assertEquals(MoreItem.SETTINGS, shell.more.value)
        shell.clearPick()
        assertEquals(MoreItem.ABOUT, shell.more.value)
        shell.clearPick()
        assertNull(shell.more.value)
    }

    @Test
    fun routesReachTheCircuitPlaces() {
        val shell = ShellController()
        shell.openRoute("notifications")
        assertEquals(Place.INBOX, shell.place.value)
        shell.openRoute("now")
        shell.openRoute("inbox")
        assertEquals(Place.INBOX, shell.place.value)
        shell.openRoute("watching")
        assertEquals(Place.WATCHING, shell.place.value)
        // Projects lives under More now.
        shell.openRoute("projects")
        assertEquals(Place.MORE, shell.place.value)
        assertEquals(MoreItem.PROJECTS, shell.more.value)
        // Back from it returns to More's list.
        shell.clearPick(Place.MORE)
        assertNull(shell.more.value)
    }

    @Test
    fun projectsUnderMoreCarryTheirOwnContext() {
        val ref = ScreenRef("project", "p1", "Kitchen")
        assertEquals(ref, ScreenContext.forShell(ShellSnapshot(Place.MORE, more = MoreItem.PROJECTS, placeRef = ref)))
        assertNull(ScreenContext.forShell(ShellSnapshot(Place.WATCHING)))
    }
}
