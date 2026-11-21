package app.ganam.assistant.sync

import app.ganam.assistant.data.sync.TaskActionQueue
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TaskActionQueueTest {

    @Test
    fun queuedActionsRoundTrip() {
        val actions = listOf(
            TaskActionQueue.Action("a1", "task-1", "status", JSONObject().put("status", "completed"), 10L),
            TaskActionQueue.Action("a2", "task-2", "snooze", JSONObject().put("type", "time").put("minutes", 30), 20L)
        )
        val decoded = TaskActionQueue.decode(TaskActionQueue.encode(actions))

        assertEquals(2, decoded.size)
        assertEquals("task-1", decoded[0].taskId)
        assertEquals("completed", decoded[0].payload.getString("status"))
        assertEquals("snooze", decoded[1].kind)
        assertEquals(30, decoded[1].payload.getInt("minutes"))
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
        fun a(id: String, task: String, kind: String, payload: JSONObject) = TaskActionQueue.Action(id, task, kind, payload, 0L)
        var q = TaskActionQueue.merge(emptyList(), a("1", "t", "update", JSONObject().put("title", "A")))
        q = TaskActionQueue.merge(q, a("2", "t", "status", JSONObject().put("status", "completed")))
        q = TaskActionQueue.merge(q, a("3", "t", "update", JSONObject().put("priority", "high").put("title", "B")))
        // The status stays, the two updates become one (newest values) at the end.
        assertEquals(listOf("status", "update"), q.map { it.kind })
        assertEquals("B", q[1].payload.getString("title"))
        assertEquals("high", q[1].payload.getString("priority"))

        q = TaskActionQueue.merge(q, a("4", "t", "snooze", JSONObject().put("type", "wake")))
        assertEquals(listOf("update", "snooze"), q.map { it.kind })
        q = TaskActionQueue.merge(q, a("5", "other", "status", JSONObject().put("status", "completed")))
        assertEquals(3, q.size)
    }
}
