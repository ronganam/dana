package app.ganam.assistant.wear.tasks

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.google.android.gms.wearable.Wearable
import org.json.JSONObject

class WearTaskActionReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.getStringExtra("action") ?: return
        val taskId = intent.getStringExtra("task_id") ?: return

        when (action) {
            "complete" -> {
                sendAction(context, "/update_task_status", JSONObject().apply {
                    put("task_id", taskId)
                    put("status", "completed")
                })
            }
            "snooze" -> {
                val minutes = intent.getIntExtra("snooze_minutes", 60)
                sendAction(context, "/snooze_task", JSONObject().apply {
                    put("task_id", taskId)
                    put("type", "time")
                    put("minutes", minutes)
                })
            }
        }

        // Clears our notification and stops the phone's alarm too.
        WearReminderSync.handledOnWatch(context, taskId)
    }

    private fun sendAction(context: Context, path: String, payload: JSONObject) {
        Wearable.getNodeClient(context).connectedNodes
            .addOnSuccessListener { nodes ->
                val phoneId = nodes.firstOrNull()?.id ?: return@addOnSuccessListener
                Wearable.getMessageClient(context)
                    .sendMessage(phoneId, path, payload.toString().toByteArray())
            }
    }
}
