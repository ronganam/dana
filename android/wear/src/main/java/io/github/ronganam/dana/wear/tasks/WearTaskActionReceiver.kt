package io.github.ronganam.dana.wear.tasks

import io.github.ronganam.dana.protocol.SnoozeRequest
import io.github.ronganam.dana.protocol.TaskStatusRequest
import io.github.ronganam.dana.protocol.WearPaths
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.google.android.gms.wearable.Wearable

class WearTaskActionReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.getStringExtra("action") ?: return
        val taskId = intent.getStringExtra("task_id") ?: return

        when (action) {
            "complete" -> {
                sendAction(context, WearPaths.UPDATE_TASK_STATUS, TaskStatusRequest(taskId, "completed").encode())
            }
            "snooze" -> {
                val minutes = intent.getIntExtra("snooze_minutes", 60)
                sendAction(context, WearPaths.SNOOZE_TASK, SnoozeRequest(taskId, "time", minutes = minutes).encode())
            }
        }

        // Clears our notification and stops the phone's alarm too.
        WearReminderSync.handledOnWatch(context, taskId)
    }

    private fun sendAction(context: Context, path: String, payload: String) {
        Wearable.getNodeClient(context).connectedNodes
            .addOnSuccessListener { nodes ->
                val phoneId = nodes.firstOrNull()?.id ?: return@addOnSuccessListener
                Wearable.getMessageClient(context)
                    .sendMessage(phoneId, path, payload.toByteArray())
            }
    }
}
