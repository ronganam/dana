package io.github.ronganam.dana

import android.app.Activity
import android.content.Intent
import android.os.Bundle

/**
 * Trampoline for the "Ask Dana" widget and shortcut: opens the assistant
 * overlay when Dana is the default assistant, otherwise the chat screen.
 */
class AskDanaActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (!DanaVoiceInteractionService.openSession()) {
            startActivity(
                Intent(this, MainActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
                    .putExtra(EXTRA_OPEN_SCREEN, "chat")
            )
        }
        finish()
        @Suppress("DEPRECATION")
        overridePendingTransition(0, 0)
    }
}
