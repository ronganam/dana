package io.github.ronganam.dana.tv

import android.content.Intent
import android.os.Bundle
import android.view.KeyEvent
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.lifecycle.lifecycleScope
import io.github.ronganam.dana.data.network.ServerStatusMonitor
import io.github.ronganam.dana.ui.components.FloatingAssistantOverlay
import io.github.ronganam.dana.ui.theme.DanaTheme
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import io.github.ronganam.dana.wake.AssistantSession
import io.github.ronganam.dana.wake.DanaSpeaker
import io.github.ronganam.dana.wake.OpenTimer
import io.github.ronganam.dana.wake.WakeWordService

/**
 * Dana over whatever is on the TV: a translucent activity in its own task
 * (so closing it returns to the app that was playing, not to the home
 * screen), opened by the wake-word service on "Hey Dana", by a remote-button
 * binding, or from the home screen. It is kept alive between conversations
 * (moved to the back, not finished) so the chat survives.
 */
class AssistantActivity : ComponentActivity() {
    private val vm: MainViewModel by viewModels()
    private lateinit var speaker: DanaSpeaker
    private lateinit var session: AssistantSession

    override fun onCreate(savedInstanceState: Bundle?) {
        OpenTimer.mark("activity create")
        super.onCreate(savedInstanceState)
        speaker = DanaSpeaker(this)
        vm.initializeChatOnly()
        session = AssistantSession(this, lifecycleScope, vm, speaker, onClosed = { moveTaskToBack(true) })
        session.installVoiceSource()

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (session.visible) session.close() else moveTaskToBack(true)
            }
        })

        setContent {
            DanaTheme(darkTheme = true) {
                Box(Modifier.fillMaxSize()) {
                    if (session.visible) {
                        LaunchedEffect(Unit) {
                            withFrameNanos { }
                            withFrameNanos { }
                            OpenTimer.mark("sheet first frame")
                        }
                        FloatingAssistantOverlay(
                            viewModel = vm,
                            onMoveToApp = null,
                            onDismiss = { session.close() },
                            onMinimizeOverride = { session.close() }
                        )
                    }
                }
            }
        }
        open(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        open(intent)
    }

    private fun open(intent: Intent?) {
        val frame = intent?.getLongExtra(WakeWordService.EXTRA_WAKE_FRAME, -1L)?.takeIf { it >= 0 }
        OpenTimer.mark("activity intent")
        session.open(frame)
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        session.noteInteraction()
        return super.dispatchKeyEvent(event)
    }

    override fun onStart() {
        super.onStart()
        ServerStatusMonitor.start(this)
    }

    override fun onStop() {
        // Home pressed or another app came forward mid-conversation: end it.
        if (session.visible && !isChangingConfigurations) session.close()
        ServerStatusMonitor.stop()
        super.onStop()
    }

    override fun onDestroy() {
        session.release()
        speaker.shutdown()
        super.onDestroy()
    }
}
