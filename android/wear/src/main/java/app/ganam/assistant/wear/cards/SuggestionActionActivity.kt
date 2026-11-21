package app.ganam.assistant.wear.cards

import android.app.Activity
import android.os.Bundle
import android.util.Log
import android.widget.Toast
import com.google.android.gms.wearable.Wearable
import org.json.JSONObject

/**
 * Tapped from the Waiting-on-you tile: sends Accept / Dismiss for one
 * suggestion to the phone (/suggestion_action), drops it from the tile
 * straight away and closes. The phone refreshes and sends a new snapshot.
 */
class SuggestionActionActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val id = intent.getStringExtra("id").orEmpty()
        val action = intent.getStringExtra("action").orEmpty()
        if (id.isBlank() || action !in setOf("accept", "dismiss")) { finish(); return }

        val app = applicationContext
        val bytes = JSONObject().put("id", id).put("action", action).toString().toByteArray()
        Wearable.getNodeClient(app).connectedNodes
            .addOnSuccessListener { nodes ->
                if (nodes.isEmpty()) {
                    Toast.makeText(app, "Phone not connected", Toast.LENGTH_SHORT).show()
                    return@addOnSuccessListener
                }
                nodes.forEach { Wearable.getMessageClient(app).sendMessage(it.id, PATH, bytes) }
                DanaSnapshot.removeSuggestion(app, id)
                Toast.makeText(app, if (action == "accept") "Accepted" else "Dismissed", Toast.LENGTH_SHORT).show()
            }
            .addOnFailureListener { e ->
                Log.w("SuggestionAction", "Couldn't reach the phone", e)
                Toast.makeText(app, "Phone not connected", Toast.LENGTH_SHORT).show()
            }
        finish()
    }

    companion object {
        const val PATH = "/suggestion_action"
    }
}
