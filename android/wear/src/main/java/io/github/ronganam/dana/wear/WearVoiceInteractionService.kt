package io.github.ronganam.dana.wear

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.service.voice.VoiceInteractionService
import android.service.voice.VoiceInteractionSession
import android.service.voice.VoiceInteractionSessionService
import android.util.Log
import io.github.ronganam.dana.wear.data.PhoneNode
import io.github.ronganam.dana.wear.perf.WearPerf

/**
 * Dana as the watch's digital assistant: holding the side button lands in
 * [WearVoiceInteractionSession.onShow]. While Dana is the default assistant
 * the system keeps this service bound, so the app process stays warm and the
 * press only has to start [MainActivity].
 */
class WearVoiceInteractionService : VoiceInteractionService() {
    override fun onReady() {
        super.onReady()
        // Have the phone's node id ready before the first press.
        PhoneNode.prefetch(this)
    }
}

class WearVoiceInteractionSessionService : VoiceInteractionSessionService() {
    override fun onNewSession(args: Bundle?): VoiceInteractionSession {
        return WearVoiceInteractionSession(this)
    }
}

class WearVoiceInteractionSession(context: Context) : VoiceInteractionSession(context) {

    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        WearPerf.start("assist button")
        launchWearAssistant()
        hide()
    }

    private fun launchWearAssistant() {
        val intent = Intent(context, MainActivity::class.java).apply {
            // SINGLE_TOP: a running MainActivity gets onNewIntent. CLEAR_TOP
            // alone finished and re-created it (and its ViewModel) on every press.
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            putExtra("start_listening", true)
        }
        try {
            context.startActivity(intent)
            WearPerf.mark("activity requested")
        } catch (e: Exception) {
            Log.e("WearVoiceSession", "Couldn't open the assistant", e)
        }
    }
}
