package io.github.ronganam.dana.chat

import io.github.ronganam.dana.data.network.ChatApi
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Every frame of the server's golden run stream (`server/tests/fixtures/golden.sse`) reads into a [io.github.ronganam.dana.data.network.RunEvent]. */
class RunEventContractTest {
    private val frames = javaClass.classLoader!!.getResource("golden.sse")!!.readText()
        .lineSequence().filter { it.startsWith("data:") }.map { it.removePrefix("data:").trim() }.toList()

    @Test
    fun everyFrameParses() {
        val events = frames.map { assertNotNull(it, ChatApi.parseEvent(it)); ChatApi.parseEvent(it)!! }
        assertEquals("run_start", events.first().type)
        assertEquals("Balanced", events.first().model?.label)
        val seqs = events.mapNotNull { it.seq }
        assertEquals((1..seqs.size).toList(), seqs)
        val runId = events.first().runId
        assertTrue(runId != null && events.dropLast(1).all { it.runId == runId })
        val ends = events.filter { it.type == "tool_end" }
        assertEquals(listOf(300L, 300L), ends.map { it.durationMs })
        assertTrue(ends.all { it.ok })
        assertEquals("Both lookups done.", events.single { it.type == "final" }.content)
        val state = events.last().state!!
        assertEquals("idle", state.status)
        assertEquals("Both lookups done.", state.messages.last().message)
        assertEquals(emptyList<Any>(), state.undos)
    }
}
