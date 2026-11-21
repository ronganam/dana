package io.github.ronganam.dana.sync

import io.github.ronganam.dana.data.sync.TaskActionQueue
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.long
import io.github.ronganam.dana.data.network.text
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TaskActionQueueTest {
    private fun obj(text: String) = ApiJson.parseToJsonElement(text) as JsonObject

    @Test
    fun queuedActionsRoundTrip() {
        val actions = listOf(
            TaskActionQueue.Action("a1", "task-1", "status", obj("""{"status":"completed"}"""), 10L),
            TaskActionQueue.Action("a2", "task-2", "snooze", obj("""{"type":"time","minutes":30}"""), 20L)
        )
        val decoded = TaskActionQueue.decode(TaskActionQueue.encode(actions))

        assertEquals(2, decoded.size)
        assertEquals("task-1", decoded[0].taskId)
        assertEquals("completed", decoded[0].payload.text("status"))
        assertEquals("snooze", decoded[1].kind)
        assertEquals(30L, decoded[1].payload.long("minutes"))
        assertEquals(20L, decoded[1].createdAt)
    }

    @Test
    fun malformedEntriesAreSkipped() {
        val decoded = TaskActionQueue.decode("""[{"id":"","task_id":"x"},{"id":"ok","task_id":"t","kind":"status"},3]""")
        assertEquals(listOf("ok"), decoded.map { it.id })
    }

    @Test
    fun onlyUnreachableAndServerErrorsAreRetried() {
        assertTrue(TaskActionQueue.isRetryable(-1))   // unreachable (VPN off)
        assertTrue(TaskActionQueue.isRetryable(502))
        assertTrue(TaskActionQueue.isRetryable(429))
        assertFalse(TaskActionQueue.isRetryable(404)) // task deleted: retrying won't help
        assertFalse(TaskActionQueue.isRetryable(401))
    }

    @Test
    fun fieldUpdatesMergeAndStatusReplaces() {
        fun a(id: String, task: String, kind: String, payload: JsonObject) = TaskActionQueue.Action(id, task, kind, payload, 0L)
        var q = TaskActionQueue.merge(emptyList(), a("1", "t", "update", obj("""{"title":"A"}""")))
        q = TaskActionQueue.merge(q, a("2", "t", "status", obj("""{"status":"completed"}""")))
        q = TaskActionQueue.merge(q, a("3", "t", "update", obj("""{"priority":"high","title":"B"}""")))
        // The status stays, the two updates become one (newest values) at the end.
        assertEquals(listOf("status", "update"), q.map { it.kind })
        assertEquals("B", q[1].payload.text("title"))
        assertEquals("high", q[1].payload.text("priority"))

        q = TaskActionQueue.merge(q, a("4", "t", "snooze", obj("""{"type":"wake"}""")))
        assertEquals(listOf("update", "snooze"), q.map { it.kind })
        q = TaskActionQueue.merge(q, a("5", "other", "status", obj("""{"status":"completed"}""")))
        assertEquals(3, q.size)
    }
}
