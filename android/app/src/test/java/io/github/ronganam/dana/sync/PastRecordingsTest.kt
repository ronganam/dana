package io.github.ronganam.dana.sync

import androidx.work.WorkInfo
import io.github.ronganam.dana.data.sync.CallUploadStore.Entry
import io.github.ronganam.dana.data.sync.CallUploadStore.State
import io.github.ronganam.dana.data.sync.PastRecordings
import io.github.ronganam.dana.data.sync.PastRecordings.Backlog
import io.github.ronganam.dana.data.sync.PastRecordings.Verdict
import io.github.ronganam.dana.data.sync.RecordingDoc
import org.junit.Assert.assertEquals
import org.junit.Test

class PastRecordingsTest {

    private val started = 50_000L
    private fun doc(name: String, modifiedMs: Long, mime: String? = "audio/mp4") = RecordingDoc("content://tree/$name", name, mime, modifiedMs, 1000)

    private val robin = doc("Call Robin_241001_093015.m4a", 3_000)
    private val gal = doc("Call Gal_241002_120000.m4a", 1_000)
    private val maya = doc("Call Maya_241003_080000.m4a", 2_000)
    private val notes = doc("notes.txt", 500, mime = "text/plain")

    private fun names(docs: List<RecordingDoc>) = docs.map { it.name }

    @Test
    fun picksOldCallsOldestFirst() {
        val states = mapOf(robin.name to Entry(State.SEEN, 1), gal.name to Entry(State.SEEN, 1), maya.name to Entry(State.NOTIFIED, 1),
            notes.name to Entry(State.SEEN, 1))
        val picked = PastRecordings.candidates(listOf(robin, gal, maya, notes), { states[it] }, started)
        assertEquals(listOf(gal.name, maya.name, robin.name), names(picked))
    }

    @Test
    fun leavesNewDoneAndSkippedRecordingsAlone() {
        val states = mapOf(
            robin.name to Entry(State.UPLOADED, 1),
            gal.name to Entry(State.QUEUED, 1),
            maya.name to Entry(State.SKIPPED, 1)
        )
        val pending = doc("Call Pending_241004_080000.m4a", 4_000)
        val fresh = doc("Call Fresh_241005_080000.m4a", 5_000)
        val all = states + (pending.name to Entry(State.PENDING, 1))
        // No state (fresh) and PENDING belong to the folder scan.
        assertEquals(emptyList<String>(), names(PastRecordings.candidates(listOf(robin, gal, maya, pending, fresh), { all[it] }, started)))
    }

    @Test
    fun resumesWhereAStoppedRunLeftOff() {
        // The run uploaded Gal and Maya before the network went; Robin is next.
        val states = mapOf(
            gal.name to Entry(State.UPLOADED, started + 10),
            maya.name to Entry(State.UPLOADED, started + 20),
            robin.name to Entry(State.SEEN, 1)
        )
        val docs = listOf(robin, gal, maya)
        assertEquals(listOf(robin.name), names(PastRecordings.candidates(docs, { states[it] }, started)))
        val tally = PastRecordings.tally(docs, { states[it] }, started)
        assertEquals(PastRecordings.Tally(uploaded = 2, failed = 0), tally)
        assertEquals(2, tally.done)
    }

    @Test
    fun aFailureIsPassedForTheRestOfTheRunAndRetriedByTheNext() {
        val states = mapOf(
            gal.name to Entry(State.FAILED, started + 10), // failed in this run
            maya.name to Entry(State.FAILED, started - 10), // failed before it
            robin.name to Entry(State.SEEN, 1)
        )
        val docs = listOf(robin, gal, maya)
        assertEquals(listOf(maya.name, robin.name), names(PastRecordings.candidates(docs, { states[it] }, started)))
        assertEquals(PastRecordings.Tally(uploaded = 0, failed = 1), PastRecordings.tally(docs, { states[it] }, started))
        // The next run starts later: Gal is tried again.
        assertEquals(listOf(gal.name, maya.name, robin.name), names(PastRecordings.candidates(docs, { states[it] }, started + 100)))
    }

    @Test
    fun verdictFollowsTheSettings() {
        assertEquals(Verdict.UPLOAD, PastRecordings.verdict(seconds = 10, thresholdSeconds = 10, skipped = false))
        assertEquals(Verdict.TOO_SHORT, PastRecordings.verdict(seconds = 9, thresholdSeconds = 10, skipped = false))
        assertEquals(Verdict.SKIPPED_CONTACT, PastRecordings.verdict(seconds = 600, thresholdSeconds = 10, skipped = true))
    }

    @Test
    fun backlogStateForSettings() {
        assertEquals(Backlog.Running(12, 140, waiting = false), PastRecordings.backlog(WorkInfo.State.RUNNING, 12, 140, 0, 0, false))
        assertEquals(Backlog.Running(12, 140, waiting = true), PastRecordings.backlog(WorkInfo.State.ENQUEUED, 12, 140, 0, 0, false))
        assertEquals(Backlog.Finished(138, 2, stopped = false), PastRecordings.backlog(WorkInfo.State.SUCCEEDED, 0, 0, 138, 2, false))
        assertEquals(Backlog.Finished(30, 1, stopped = true), PastRecordings.backlog(WorkInfo.State.FAILED, 0, 0, 30, 1, true))
        assertEquals(Backlog.Idle, PastRecordings.backlog(WorkInfo.State.CANCELLED, 5, 140, 0, 0, false))
        assertEquals(Backlog.Idle, PastRecordings.backlog(null, 0, 0, 0, 0, false))
    }

    @Test
    fun rangeLeavesOutOlderRecordings() {
        val states = mapOf(robin.name to Entry(State.SEEN, 1), gal.name to Entry(State.SEEN, 1), maya.name to Entry(State.SEEN, 1))
        // Since 2 000: Gal (1 000) is older than the range.
        assertEquals(listOf(maya.name, robin.name), names(PastRecordings.candidates(listOf(robin, gal, maya), { states[it] }, started, sinceMs = 2_000)))
    }

    @Test
    fun rangeCutOffs() {
        val now = 100L * 24 * 60 * 60 * 1000
        val day = 24L * 60 * 60 * 1000
        assertEquals(now - 7 * day, PastRecordings.Range.WEEK.sinceMs(now))
        assertEquals(now - 30 * day, PastRecordings.Range.MONTH.sinceMs(now))
        assertEquals(now - 90 * day, PastRecordings.Range.QUARTER.sinceMs(now))
        assertEquals(0L, PastRecordings.Range.ALL.sinceMs(now))
        assertEquals(12_345L, PastRecordings.Range.SINCE.sinceMs(now, customSinceMs = 12_345L))
        assertEquals(now - 7 * day, PastRecordings.Range.WEEK.sinceMs(now, customSinceMs = 12_345L))
        assertEquals(PastRecordings.Range.MONTH, PastRecordings.Range.of(null))
        assertEquals(PastRecordings.Range.ALL, PastRecordings.Range.of("ALL"))
    }
}
