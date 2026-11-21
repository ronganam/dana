package io.github.ronganam.dana

import android.os.Bundle
import android.service.voice.VoiceInteractionService

class DanaVoiceInteractionService : VoiceInteractionService() {
    override fun onReady() {
        super.onReady()
        instance = this
    }

    override fun onShutdown() {
        if (instance === this) instance = null
        super.onShutdown()
    }

    companion object {
        /** Set while Dana is the default assistant and the system has bound us. */
        @Volatile var instance: DanaVoiceInteractionService? = null
            private set

        /** Marks a show requested by Dana itself (the Ask Dana widget): no "Screen" control. */
        const val ARG_FROM_DANA = "from_dana"

        /**
         * Opens the Dana assistant overlay from Dana's own widget / shortcut.
         * No `SHOW_WITH_ASSIST` / `SHOW_WITH_SCREENSHOT`: what is underneath
         * is the launcher (or Dana), nothing worth capturing. False if Dana
         * isn't the assistant.
         */
        fun openSession(): Boolean {
            val service = instance ?: return false
            val args = Bundle().apply { putBoolean(ARG_FROM_DANA, true) }
            return runCatching { service.showSession(args, 0) ; true }.getOrDefault(false)
        }
    }
}
