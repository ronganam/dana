package io.github.ronganam.dana.push

import io.github.ronganam.dana.push.AgentSessions.Session
import io.github.ronganam.dana.push.AgentSessions.Shown
import io.github.ronganam.dana.push.AgentSessions.Step
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AgentSessionsTest {
    private fun session(id: String, status: String, rev: Int) = Session(
        id, "claude", "laptop", "dana", "Fix it", status, "Editing push.py", "", 1, 3, rev, 0L, 0L,
    )

    @Test
    fun parsesTheServerPayload() {
        val s = Session.from(JSONObject(
            """{"id":"claude:s-1","agent":"claude","host":"laptop","project":"dana","title":"Fix",
               "status":"waiting","activity":"Needs permission","summary":"","steps_done":2,"steps_total":5,
               "tools":7,"rev":3,"started_at":"2026-11-17T07:00:00+00:00","updated_at":"2026-11-17T07:12:30+00:00"}"""
        ))!!
        assertEquals("claude:s-1", s.id)
        assertEquals("Claude", s.agentName)
        assertTrue(s.live)
        assertEquals(2, s.stepsDone)
        assertEquals(5, s.stepsTotal)
        assertEquals(12 * 60_000L + 30_000L, s.updatedMs - s.startedMs)
        assertNull(Session.from(JSONObject("""{"status":"running"}""")))
    }

    @Test
    fun runningIsRepostedSilently() {
        val steps = AgentSessions.plan(mapOf("a" to Shown(1, true)), listOf(session("a", "running", 1)))
        assertEquals(listOf<Step>(Step.Live(session("a", "running", 1), alert = false)), steps)
    }

    @Test
    fun waitingAlertsOncePerChange() {
        val waiting = session("a", "waiting", 2)
        assertEquals(listOf<Step>(Step.Live(waiting, alert = true)),
            AgentSessions.plan(mapOf("a" to Shown(1, true)), listOf(waiting)))
        assertEquals(listOf<Step>(Step.Live(waiting, alert = false)),
            AgentSessions.plan(mapOf("a" to Shown(2, true)), listOf(waiting)))
    }

    @Test
    fun finishedIsPostedOncePerRev() {
        val done = session("a", "done", 3)
        assertEquals(listOf<Step>(Step.Finished(done)), AgentSessions.plan(mapOf("a" to Shown(2, true)), listOf(done)))
        assertEquals(emptyList<Step>(), AgentSessions.plan(mapOf("a" to Shown(3, false)), listOf(done)))
        // Never seen before (the phone was offline while it ran): still announced.
        assertEquals(listOf<Step>(Step.Finished(done)), AgentSessions.plan(emptyMap(), listOf(done)))
    }

    @Test
    fun parsesSubagents() {
        val s = Session.from(JSONObject(
            """{"id":"claude:p","status":"running","rev":1,"subagents":[
               {"id":"claude:p/a1","title":"Explore","status":"done","activity":"Done","summary":"Found it"},
               {"id":"claude:k","title":"Gmail kind","status":"running","activity":"Editing kind.py","summary":""},
               {"title":"no id"}]}"""
        ))!!
        assertEquals(listOf("Explore", "Gmail kind"), s.subagents.map { it.title })
        assertTrue(s.subagents[0].finished)
        assertEquals("1/2 subagents done", s.subsLine)
        assertEquals("", session("a", "running", 1).subsLine)
    }

    @Test
    fun aGroupThatStopsBeingLiveIsFinishedEvenOnTheSameRev() {
        // The parent had stopped while its subagents ran; the last one finished.
        val done = session("a", "done", 3)
        assertEquals(listOf<Step>(Step.Finished(done)), AgentSessions.plan(mapOf("a" to Shown(3, true)), listOf(done)))
    }

    @Test
    fun vanishedLiveSessionsAreCancelledFinishedOnesKept() {
        val steps = AgentSessions.plan(mapOf("a" to Shown(1, true), "b" to Shown(4, false)), emptyList())
        assertEquals(listOf<Step>(Step.Cancel("a")), steps)
    }
}
