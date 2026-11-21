package io.github.ronganam.dana

import io.github.ronganam.dana.data.network.ServerConfig
import android.util.Log
import android.app.KeyguardManager
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.Ringtone
import android.media.RingtoneManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.view.WindowManager
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.lifecycleScope
import io.github.ronganam.dana.data.notifications.AppIcons
import io.github.ronganam.dana.data.notifications.DanaNotificationListener
import io.github.ronganam.dana.data.notifications.Snoozer
import io.github.ronganam.dana.ui.components.InkChip
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.theme.Danger
import io.github.ronganam.dana.ui.theme.DanaTheme
import io.github.ronganam.dana.ui.theme.ThemeSettings
import io.github.ronganam.dana.ui.theme.Warning
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Full-screen alert for an urgent phone notification (see
 * [NotificationAlertReceiver]): harder to ignore than the shade, gentler than
 * a task alarm — it vibrates and plays the notification sound for up to
 * [ALERT_MS], then waits quietly. Open it in its app, snooze, dismiss or ask
 * Dana; "Got it" leaves it in the Inbox. Open, Ask Dana and Got it tell the
 * server it was seen (snooze and dismiss already do), which stops re-nudges.
 */
class NotificationAlertActivity : ComponentActivity() {

    private var ringtone: Ringtone? = null
    private var vibrator: Vibrator? = null
    private val handler = Handler(Looper.getMainLooper())
    private var alert: NotificationAlertReceiver.Alert? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
        }
        show(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        stopAlerts()
        show(intent)
    }

    private fun show(intent: Intent) {
        val a = NotificationAlertReceiver.Alert.from(intent) ?: run { finish(); return }
        alert = a
        startAlerts()
        ThemeSettings.load(applicationContext)
        setContent {
            DanaTheme {
                NotificationAlertScreen(
                    alert = a,
                    onOpen = { openInApp(a) },
                    onSnooze = { opt -> snooze(a, opt) },
                    onDismiss = { dismiss(a) },
                    onAsk = { askDana(a) },
                    onGotIt = { handled(a); markSeen(a); finish() }
                )
            }
        }
    }

    /** Clear our notification and stop the sound. */
    private fun handled(a: NotificationAlertReceiver.Alert) {
        stopAlerts()
        (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).cancel(a.notificationId)
    }

    /** Tell the server the user saw it, so it stops re-nudging (best effort). */
    private fun markSeen(a: NotificationAlertReceiver.Alert) {
        val app = applicationContext
        CoroutineScope(Dispatchers.IO).launch {
            try {
                ServerConfig.load(app).api().notifications.markSeen(listOf(a.id))
            } catch (e: Exception) {
                Log.w("NotificationAlert", "Couldn't mark ${a.id} seen", e)
            }
        }
    }

    private fun unlockThen(block: () -> Unit) {
        val km = getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && km.isKeyguardLocked) {
            km.requestDismissKeyguard(this, object : KeyguardManager.KeyguardDismissCallback() {
                override fun onDismissSucceeded() = block()
            })
        } else block()
    }

    private fun openInApp(a: NotificationAlertReceiver.Alert) {
        handled(a)
        markSeen(a)
        unlockThen {
            val opened = DanaNotificationListener.open(this, a.key) || run {
                val launch = a.packageName?.let { packageManager.getLaunchIntentForPackage(it) }
                if (launch != null) { startActivity(launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); true } else false
            }
            if (!opened) Toast.makeText(this, "Couldn't open ${a.app}", Toast.LENGTH_SHORT).show()
            finish()
        }
    }

    private fun snooze(a: NotificationAlertReceiver.Alert, opt: Snoozer.Option) {
        handled(a)
        lifecycleScope.launch {
            val msg = try {
                Snoozer.snooze(applicationContext, ServerConfig.load(applicationContext).api(),
                    listOf(Snoozer.Target(a.id, a.key, a.app, a.title)), opt.untilMillis)
                "Snoozed ${Snoozer.untilLabel(opt.untilMillis)}"
            } catch (e: Exception) {
                "Couldn't snooze: ${e.message ?: "server unreachable"}"
            }
            Toast.makeText(applicationContext, msg, Toast.LENGTH_SHORT).show()
            finish()
        }
    }

    private fun dismiss(a: NotificationAlertReceiver.Alert) {
        handled(a)
        lifecycleScope.launch {
            try {
                val keys = ServerConfig.load(applicationContext).api().notifications.setStatus(listOf(a.id), "dismissed")
                DanaNotificationListener.cancel(keys.ifEmpty { listOfNotNull(a.key) })
            } catch (e: Exception) {
                Toast.makeText(applicationContext, "Couldn't dismiss: ${e.message ?: "server unreachable"}", Toast.LENGTH_SHORT).show()
            }
            finish()
        }
    }

    private fun askDana(a: NotificationAlertReceiver.Alert) {
        handled(a)
        markSeen(a)
        unlockThen {
            startActivity(Intent(this, MainActivity::class.java).apply {
                putExtra(CallDebriefActivity.EXTRA_CONTEXT_KIND, "notification")
                putExtra(CallDebriefActivity.EXTRA_CONTEXT_ID, a.id)
                putExtra(CallDebriefActivity.EXTRA_CONTEXT_TITLE, a.title ?: a.app)
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            })
            finish()
        }
    }

    // --- Sound & vibration (bounded) -------------------------------------------

    private val replay = object : Runnable {
        override fun run() {
            try { ringtone?.play() } catch (e: Exception) { android.util.Log.w("NotificationAlert", "Couldn't play the alert sound", e) }
            handler.postDelayed(this, REPLAY_MS)
        }
    }

    private fun startAlerts() {
        try {
            val uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)
            ringtone = RingtoneManager.getRingtone(applicationContext, uri)?.apply {
                audioAttributes = AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_NOTIFICATION_COMMUNICATION_INSTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()
            }
            handler.post(replay)
            vibrator = getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
            // Strong, insistent buzz: three pulses then a pause, full amplitude,
            // tagged as an alarm so silent / vibrate ringer modes don't mute it.
            val pattern = longArrayOf(0, 600, 150, 600, 150, 600, 900)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                val amps = intArrayOf(0, 255, 0, 255, 0, 255, 0)
                val effect = try {
                    VibrationEffect.createWaveform(pattern, amps, 0)
                } catch (_: Exception) {
                    VibrationEffect.createWaveform(pattern, 0)
                }
                @Suppress("DEPRECATION")
                vibrator?.vibrate(
                    effect,
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ALARM)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()
                )
            } else {
                @Suppress("DEPRECATION")
                vibrator?.vibrate(pattern, 0)
            }
            handler.postDelayed({ stopAlerts() }, ALERT_MS)
        } catch (e: Exception) {
            android.util.Log.w("NotificationAlert", "Couldn't start sound/vibration", e)
        }
    }

    private fun stopAlerts() {
        handler.removeCallbacksAndMessages(null)
        try { ringtone?.stop() } catch (e: Exception) { android.util.Log.w("NotificationAlert", "Couldn't stop the alert sound", e) }
        try { vibrator?.cancel() } catch (e: Exception) { android.util.Log.w("NotificationAlert", "Couldn't stop vibrating", e) }
    }

    override fun onDestroy() {
        stopAlerts()
        super.onDestroy()
    }

    companion object {
        /** Sound + vibration stop after this, the screen stays. */
        private const val ALERT_MS = 60_000L
        private const val REPLAY_MS = 8_000L
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun NotificationAlertScreen(
    alert: NotificationAlertReceiver.Alert,
    onOpen: () -> Unit,
    onSnooze: (Snoozer.Option) -> Unit,
    onDismiss: () -> Unit,
    onAsk: () -> Unit,
    onGotIt: () -> Unit
) {
    val accent = if (alert.priority == "urgent") Danger else Warning
    val ctx = LocalContext.current.applicationContext
    val icon by produceState<ImageBitmap?>(null, alert.packageName) {
        value = alert.packageName?.let { pkg -> withContext(Dispatchers.IO) { AppIcons.load(ctx, pkg) } }?.asImageBitmap()
    }
    Surface(color = MaterialTheme.colorScheme.background, modifier = Modifier.fillMaxSize()) {
        Column(
            // Unfolded: a readable column in the middle, not edge-to-edge lines.
            Modifier.fillMaxSize().wrapContentWidth().widthIn(max = 680.dp).fillMaxWidth()
                .systemBarsPadding().padding(horizontal = 24.dp, vertical = 16.dp)
        ) {
            Column(Modifier.weight(1f).verticalScroll(rememberScrollState())) {
                Spacer(Modifier.height(24.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    val bmp = icon
                    if (bmp != null) {
                        Image(bmp, contentDescription = null, modifier = Modifier.size(56.dp).clip(RoundedCornerShape(15.dp)))
                    } else {
                        Box(Modifier.size(56.dp).clip(CircleShape).background(accent.copy(alpha = 0.16f)), contentAlignment = Alignment.Center) {
                            Text(alert.emoji, fontSize = 26.sp)
                        }
                    }
                    Spacer(Modifier.width(16.dp))
                    Column {
                        MonoLabel("⚡ ${if (alert.priority == "urgent") "Urgent" else "Needs you"}" +
                            if (alert.attempt > 1) " · still waiting" else "", color = accent)
                        Text(alert.app, style = MaterialTheme.typography.titleMedium)
                    }
                }
                Spacer(Modifier.height(28.dp))
                alert.title?.let {
                    Text(it, style = MaterialTheme.typography.headlineMedium.copy(textDirection = TextDirection.Content), fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(10.dp))
                }
                alert.text?.let {
                    Text(it, style = MaterialTheme.typography.bodyLarge.copy(textDirection = TextDirection.Content),
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }

            Spacer(Modifier.height(16.dp))
            Button(onClick = onOpen, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp)) {
                Text("Open in ${alert.app}", style = MaterialTheme.typography.titleMedium)
            }
            Spacer(Modifier.height(16.dp))
            MonoLabel("Snooze")
            Spacer(Modifier.height(8.dp))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Snoozer.options().forEach { opt ->
                    InkChip(opt.label, onClick = { onSnooze(opt) }, modifier = Modifier.heightIn(min = 40.dp))
                }
            }
            Spacer(Modifier.height(16.dp))
            Rule()
            Row(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                TextButton(onClick = onDismiss) { Text("Dismiss", color = Danger) }
                TextButton(onClick = onAsk) { Text("Ask Dana") }
                TextButton(onClick = onGotIt) { Text("Got it") }
            }
        }
    }
}
