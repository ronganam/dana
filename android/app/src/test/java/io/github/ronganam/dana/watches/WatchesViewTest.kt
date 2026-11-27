package io.github.ronganam.dana.watches

import io.github.ronganam.dana.data.models.ChatMessage
import io.github.ronganam.dana.data.models.LiveAction
import io.github.ronganam.dana.data.models.LiveActivity
import io.github.ronganam.dana.data.models.ToolCallEntry
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.watches.CheckGroup
import io.github.ronganam.dana.data.watches.MarkKind
import io.github.ronganam.dana.data.watches.RunStepState
import io.github.ronganam.dana.data.watches.SourceKind
import io.github.ronganam.dana.data.watches.TraceRow
import io.github.ronganam.dana.data.watches.Watch
import io.github.ronganam.dana.data.watches.WatchActionSpec
import io.github.ronganam.dana.data.watches.WatchCondition
import io.github.ronganam.dana.data.watches.WatchSpec
import io.github.ronganam.dana.data.watches.WatchTrace
import io.github.ronganam.dana.data.watches.WatchTrigger
import io.github.ronganam.dana.data.watches.circuitOf
import io.github.ronganam.dana.data.watches.countChecks
import io.github.ronganam.dana.data.watches.draftOf
import io.github.ronganam.dana.data.watches.groupChecks
import io.github.ronganam.dana.data.watches.groupCount
import io.github.ronganam.dana.data.watches.quietUntil
import io.github.ronganam.dana.data.watches.reasonWords
import io.github.ronganam.dana.data.watches.runSteps
import io.github.ronganam.dana.data.watches.summaryWords
import io.github.ronganam.dana.data.watches.todays
import io.github.ronganam.dana.data.watches.trackMarks
import io.github.ronganam.dana.data.watches.trackStart
import io.github.ronganam.dana.data.watches.watchRuns
import io.github.ronganam.dana.data.watches.whenWords
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime

/** The Watching place's words and counts, from watches, traces and live activities. */
class WatchesViewTest {
    private val zone = ZoneId.of("Asia/Jerusalem")
    private val today = LocalDate.of(2026, 11, 27)
    private val now = ZonedDateTime.of(2026, 11, 27, 14, 0, 0, 0, zone)

    private fun fixture(): JsonObject =
        ApiJson.parseToJsonElement(javaClass.classLoader!!.getResource("watches_web.json")!!.readText()).jsonObject

    private fun row(outcome: String, at: String = "2026-11-27T13:43:00+02:00", reason: String = "", summary: String = "") =
        TraceRow(at = at, outcome = outcome, reason = reason, summary = summary)

    @Test fun readsTheServersWatchesAndDrawsTheirCircuits() {
        val f = fixture()
        val list = ApiJson.decodeFromJsonElement(io.github.ronganam.dana.data.watches.WatchDetail.serializer(), f.getValue("detail_agent"))
        val dish = circuitOf(list.watch.parsed)
        assertEquals(SourceKind.Notification, dish.sourceKind)
        assertEquals("Home Assistant notification", dish.source)
        assertEquals("Says “dishwasher” or “מדיח”", dish.rule)
        assertEquals("Ask Gal on WhatsApp to empty the dishwasher", dish.action)
        assertNull(dish.ai)

        val trace = ApiJson.decodeFromJsonElement(WatchTrace.serializer(), f.getValue("trace"))
        assertEquals(listOf("fired"), trace.trace.map { it.outcome })
    }

    @Test fun messageAppsAndBooleanConditionsReadAsWords() {
        val spec = WatchSpec(
            title = "Courier wants a time",
            triggers = listOf(WatchTrigger(eventType = "phone.notification.received", where = listOf(
                WatchCondition("app", "eq", "WhatsApp"), WatchCondition("is_group", "eq", "false")),
                semantic = "A courier asks when to deliver")),
            onMatch = listOf(WatchActionSpec(type = "notify")),
        )
        val c = circuitOf(spec)
        assertEquals(SourceKind.Message, c.sourceKind)
        assertEquals("WhatsApp message", c.source)
        assertEquals("Not a group", c.rule)
        assertEquals("A courier asks when to deliver", c.ai)
        assertEquals("Tell you", c.action)
    }

    @Test fun onlyRunsOfWatchesCountAndAWaitingOneComesFirst() {
        val runs = watchRuns(listOf(
            LiveActivity(id = "watches:run:watch-3c2cc3536851:r1", text = "Using list_notifications", updatedAt = "2026-11-27T13:40:00+02:00"),
            LiveActivity(id = "watches:run:watch-3c2cc3536851-2:r2", text = "Remember it", updatedAt = "2026-11-27T13:30:00+02:00",
                actions = listOf(LiveAction("approve", "Approve"), LiveAction("deny", "Deny"))),
            LiveActivity(id = "watches:run:watch-7082b1ffb838:r3", ended = true),
            LiveActivity(id = "trains:ride:1"),
        ))
        assertEquals(setOf("3c2cc3536851"), runs.keys)
        val run = runs.getValue("3c2cc3536851")
        assertTrue(run.waiting)
        assertEquals("watch-3c2cc3536851-2", run.sessionId)
    }

    @Test fun aRunsStepsAreItsToolsAfterTheWatchsMessageThenYourOk() {
        val run = watchRuns(listOf(LiveActivity(id = "watches:run:watch-abc:r", actions = listOf(LiveAction("approve"))))).getValue("abc")
        val msgs = listOf(
            ChatMessage(sender = "you", message = "older", isUser = true),
            ChatMessage(sender = "dana", message = "", isUser = false, toolCall = ToolCallEntry("old_tool", "", "", succeeded = true)),
            ChatMessage(sender = "you", message = "The watch fired", isUser = true),
            ChatMessage(sender = "dana", message = "", isUser = false, toolCall = ToolCallEntry("list_notifications", "", "", succeeded = true)),
            ChatMessage(sender = "dana", message = "", isUser = false, toolCall = ToolCallEntry("remember_info", "", "", requiresApproval = true)),
        )
        val steps = runSteps(msgs, run)
        assertEquals(listOf("List notifications", "Your OK"), steps.map { it.label })
        assertEquals(listOf(RunStepState.Done, RunStepState.Waiting), steps.map { it.state })
    }

    @Test fun todaysChecksAreCountedAlongTheCircuit() {
        val rows = listOf(
            row("fired"), row("semantic_no"), row("drop"), row("drop"),
            row("unstored", summary = "41 more checks not stored this hour"),
            row("fired", at = "2026-11-26T22:00:00+02:00"),
        )
        val c = countChecks(todays(rows, today, zone))
        assertEquals(45, c.checks)
        assertEquals(2, c.passedRule)
        assertEquals(1, c.aiNo)
        assertEquals(1, c.fired)
    }

    @Test fun checksGroupByOutcomeInOrder() {
        val g = groupChecks(listOf(row("drop"), row("fired"), row("cooldown"), row("unstored", summary = "3 more checks")))
        assertEquals(listOf(CheckGroup.Fired, CheckGroup.RuleNo, CheckGroup.Other), g.keys.toList())
        assertEquals(4, groupCount(g.getValue(CheckGroup.RuleNo)))
    }

    @Test fun reasonsReadAsPlainWords() {
        assertEquals("App was “Gmail”", reasonWords(row("drop", reason = "app eq \"WhatsApp\" ← \"Gmail\"")))
        assertEquals("At 13:43, outside 23:00–06:00", reasonWords(row("during", reason = "during 23:00-06:00 ← 13:43")))
        assertEquals("No “finished” or “done” in it", reasonWords(row("drop", reason = "keywords \"finished|done\" not in the text")))
        assertEquals("Amount was 120", reasonWords(row("drop", reason = "amount gt \"500\" ← \"120\"")))
        assertEquals("The AI check did not answer", reasonWords(row("semantic_failed")))
    }

    @Test fun aFireWaitingForYouIsHeldOnTheTrack() {
        val fired = row("fired", at = "2026-11-27T13:43:00+02:00")
        val held = trackMarks(listOf(fired, row("drop", at = "2026-11-27T09:00:00+02:00")), heldSince = fired.atMs)
        assertEquals(listOf(MarkKind.Dropped, MarkKind.Held), held.map { it.kind })
        assertEquals(MarkKind.Fired, trackMarks(listOf(fired)).single().kind)
        val six = today.atTime(6, 0).atZone(zone).toInstant().toEpochMilli()
        assertEquals(six, trackStart(held, today, zone))
        val early = trackMarks(listOf(row("drop", at = "2026-11-27T03:00:00+02:00")))
        assertEquals(today.atStartOfDay(zone).toInstant().toEpochMilli(), trackStart(early, today, zone))
    }

    @Test fun wordsForTheList() {
        val at = { s: String -> ZonedDateTime.parse(s).toInstant().toEpochMilli() }
        assertEquals("08:31", whenWords(at("2026-11-27T08:31:00+02:00"), now))
        assertEquals("Tue", whenWords(at("2026-11-24T08:31:00+02:00"), now))
        assertEquals("9 Nov", whenWords(at("2026-11-09T08:31:00+02:00"), now))
        val ws = listOf(Watch(id = "a"), Watch(id = "b"), Watch(id = "c", status = "paused"))
        assertEquals("2 on, 1 paused. One needs you.", summaryWords(ws, 1))
        assertEquals("2 on, 1 paused.", summaryWords(ws, 0))
    }

    @Test fun quietHoursShowTheirStartOnlyWhenNotNow() {
        val spec = WatchSpec(triggers = listOf(WatchTrigger(during = "23:00-06:00")))
        assertEquals("From 23:00", quietUntil(spec, now))
        assertNull(quietUntil(spec, now.withHour(2)))
        assertNull(quietUntil(WatchSpec(), now))
    }

    @Test fun aDraftIsTheWordsAToolWouldSend() {
        assertEquals("Tomorrow 10:00 works", draftOf("{\"chat\":\"x\",\"text\":\"Tomorrow 10:00 works\"}"))
        assertNull(draftOf("{\"entity\":\"light.x\"}"))
        assertNull(draftOf("not json"))
    }
}
