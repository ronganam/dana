package io.github.ronganam.dana

import android.content.Context
import io.github.ronganam.dana.data.network.TaskSnooze
import io.github.ronganam.dana.data.sync.SubmitResult
import io.github.ronganam.dana.data.sync.TaskActionQueue

/** What the alarm's buttons send (queued while the server is unreachable); each answer is the toast to show. */
internal object ReminderActions {
    suspend fun complete(context: Context, taskId: String): String = when (TaskActionQueue.complete(context, taskId)) {
        SubmitResult.SENT -> "Task completed!"
        SubmitResult.QUEUED -> "Offline. The task will be completed when the server is reachable."
        SubmitResult.REJECTED -> "The server couldn't complete this task"
    }

    /** The server's snooze for a dial leaf (it replaces every trigger the task had). */
    fun snoozeRequest(leaf: MenuNode.Leaf): TaskSnooze = when (leaf.snoozeType) {
        "time" -> TaskSnooze("time", minutes = leaf.minutes)
        "location" -> TaskSnooze("location", location = leaf.location, event = leaf.event)
        "car" -> TaskSnooze("car", action = leaf.action)
        else -> TaskSnooze(leaf.snoozeType) // wake: no sub fields
    }

    suspend fun snooze(context: Context, taskId: String, leaf: MenuNode.Leaf): String =
        when (TaskActionQueue.snooze(context, taskId, snoozeRequest(leaf))) {
            SubmitResult.SENT -> "Task snoozed successfully"
            SubmitResult.QUEUED -> "Offline. The snooze will be sent when the server is reachable."
            SubmitResult.REJECTED -> "The server couldn't snooze this task"
        }
}
