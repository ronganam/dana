package io.github.ronganam.dana.sync

import io.github.ronganam.dana.data.sync.CallUploadStore.State
import io.github.ronganam.dana.data.sync.FolderScan
import io.github.ronganam.dana.data.sync.RecordingDoc
import io.github.ronganam.dana.data.sync.SharedAudio
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FolderScanTest {

    private val now = 10_000_000_000L
    private fun doc(name: String, ageMs: Long, mime: String? = "audio/mp4") = RecordingDoc("content://tree/$name", name, mime, now - ageMs, 1000)

    private val old = doc("Call Robin_241031_093015.m4a", 3600_000)
    private val older = doc("Gal(+15550100)_20241030120000.mp3", 7200_000, mime = null)
    private val fresh = doc("Call Maya_241031_101500.m4a", 5_000)
    private val notAudio = doc("notes.txt", 3600_000, mime = "text/plain")

    @Test
    fun firstScanOfAFolderSeedsWhatIsThere() {
        val plan = FolderScan.plan(listOf(old, older, fresh, notAudio), seeded = false, stateOf = { null }, nowMs = now)
        assertEquals(listOf(old.name, older.name), plan.seed)
        assertTrue(plan.handle.isEmpty())
        // The one still recording is left for the next scan, which handles it as new.
        assertTrue(plan.settling)
    }

    @Test
    fun handlesOnlyWhatTheStoreDoesNotKnow() {
        val states = mapOf(old.name to State.UPLOADED)
        val plan = FolderScan.plan(listOf(old, older, notAudio), seeded = true, stateOf = { states[it] }, nowMs = now)
        assertEquals(listOf(older to false), plan.handle)
        assertFalse(plan.settling)
    }

    @Test
    fun handledStatesAreNeverHandledAgain() {
        for (state in listOf(State.SEEN, State.QUEUED, State.UPLOADED, State.FAILED, State.SKIPPED)) {
            val plan = FolderScan.plan(listOf(old), seeded = true, stateOf = { state }, nowMs = now)
            assertTrue("$state", plan.handle.isEmpty())
        }
    }

    @Test
    fun halfHandledOnesAreRetriedForThreeDays() {
        val stale = doc("Call Robin_241001_093015.m4a", FolderScan.RECOVER_WINDOW_MS + 1)
        val states = mapOf(old.name to State.PENDING, older.name to State.NOTIFIED, stale.name to State.PENDING)
        val plan = FolderScan.plan(listOf(old, older, stale), seeded = true, stateOf = { states[it] }, nowMs = now)
        // Oldest first.
        assertEquals(listOf(older to true, old to true), plan.handle)
    }

    @Test
    fun aFileStillBeingWrittenWaits() {
        val plan = FolderScan.plan(listOf(fresh), seeded = true, stateOf = { null }, nowMs = now)
        assertTrue(plan.handle.isEmpty())
        assertTrue(plan.settling)
        // Once it settles it is new.
        val later = FolderScan.plan(listOf(fresh), seeded = true, stateOf = { null }, nowMs = now + FolderScan.SETTLE_MS)
        assertEquals(listOf(fresh to false), later.handle)
    }

    @Test
    fun oneFileNameIsOneRecording() {
        val plan = FolderScan.plan(listOf(old, old.copy(uri = "content://tree/other")), seeded = true, stateOf = { null }, nowMs = now)
        assertEquals(1, plan.handle.size)
    }

    @Test
    fun audioByTypeOrExtension() {
        assertTrue(FolderScan.isAudio("x.m4a", "audio/mp4"))
        assertTrue(FolderScan.isAudio("x.amr", null))
        assertTrue(FolderScan.isAudio("x.3gp", "video/3gpp"))
        assertTrue(FolderScan.isAudio("x.mp3", "application/octet-stream"))
        assertFalse(FolderScan.isAudio("x.jpg", "image/jpeg"))
        assertFalse(FolderScan.isAudio("x.txt", null))
    }

    // ── Share intent parsing ──

    private val types = mapOf("a" to "audio/mp4", "b" to "audio/ogg", "img" to "image/png", "blank" to null)

    @Test
    fun sendTakesTheStreamOrTheFirstClipItem() {
        assertEquals(listOf("a"), SharedAudio.pick(SharedAudio.SEND, "audio/*", "a", null, listOf("b")) { types[it] })
        assertEquals(listOf("b"), SharedAudio.pick(SharedAudio.SEND, "audio/*", null, null, listOf("b", "a")) { types[it] })
    }

    @Test
    fun sendMultipleKeepsOnlyAudio() {
        val picked = SharedAudio.pick(SharedAudio.SEND_MULTIPLE, "*/*", null, listOf("a", "img", "b", "a"), emptyList()) { types[it] }
        assertEquals(listOf("a", "b"), picked)
        // No stream extra: the clip items.
        assertEquals(listOf("a", "b"), SharedAudio.pick(SharedAudio.SEND_MULTIPLE, "audio/*", null, null, listOf("a", "b")) { types[it] })
    }

    @Test
    fun anUntypedItemCountsWhenTheShareSaysAudio() {
        assertEquals(listOf("blank"), SharedAudio.pick(SharedAudio.SEND, "audio/mpeg", "blank", null, emptyList()) { types[it] })
        assertTrue(SharedAudio.pick(SharedAudio.SEND, "*/*", "blank", null, emptyList()) { types[it] }.isEmpty())
    }

    @Test
    fun otherActionsAndBigSharesAreCut() {
        assertTrue(SharedAudio.pick("android.intent.action.VIEW", "audio/*", "a", null, emptyList()) { types[it] }.isEmpty())
        val many = (1..30).map { "f$it" }
        assertEquals(SharedAudio.MAX_FILES, SharedAudio.pick(SharedAudio.SEND_MULTIPLE, "audio/*", null, many, emptyList()) { "audio/mp4" }.size)
    }
}
