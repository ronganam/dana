package io.github.ronganam.dana

import android.app.KeyguardManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.Ringtone
import android.media.RingtoneManager
import android.os.Build
import android.os.Bundle
import android.os.VibrationEffect
import android.os.Vibrator
import android.util.Log
import android.view.WindowManager
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import io.github.ronganam.dana.data.wear.WearReminderBridge
import io.github.ronganam.dana.ui.theme.DanaTheme
import io.github.ronganam.dana.ui.theme.ThemeSettings
import kotlinx.coroutines.launch
import io.github.ronganam.dana.R

class ReminderActivity : ComponentActivity() {

    private var ringtone: Ringtone? = null
    private var vibrator: Vibrator? = null
    private var taskId: String = ""

    private val closeReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val id = intent.getStringExtra(WearReminderBridge.EXTRA_TASK_ID)
            if (id == null || id == taskId) {
                stopAlerts()
                finish()
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Bypassing Lock Screen
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
            )
        }

        val keyguardManager = getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
        keyguardManager.requestDismissKeyguard(this, null)

        // Retrieve intent extras
        taskId = intent.getStringExtra("task_id") ?: ""
        val taskTitle = intent.getStringExtra("task_title") ?: "Reminder"
        val priority = intent.getStringExtra("priority") ?: "medium"
        val triggerType = intent.getStringExtra("trigger_type") ?: "time"

        // The watch handled this alarm: stop ringing here too.
        ContextCompat.registerReceiver(
            this, closeReceiver, IntentFilter(WearReminderBridge.ACTION_CLOSE_REMINDER),
            ContextCompat.RECEIVER_NOT_EXPORTED
        )

        // Ringtone and Vibration
        startAlerts()

        ThemeSettings.load(applicationContext)
        setContent {
            DanaTheme {
                ReminderScreen(
                    taskTitle = taskTitle,
                priority = priority,
                triggerType = triggerType,
                onComplete = {
                    stopAlerts()
                    onHandled()
                    lifecycleScope.launch {
                        val message = ReminderActions.complete(this@ReminderActivity, taskId)
                        Toast.makeText(this@ReminderActivity, message, Toast.LENGTH_LONG).show()
                        finish()
                    }
                },
                onSnoozeSelected = { leaf ->
                    stopAlerts()
                    onHandled()
                    lifecycleScope.launch {
                        Toast.makeText(this@ReminderActivity, getString(R.string.toast_snoozing, snoozeSentence(leaf)), Toast.LENGTH_SHORT).show()
                        val message = ReminderActions.snooze(this@ReminderActivity, taskId, leaf)
                        Toast.makeText(this@ReminderActivity, message, Toast.LENGTH_LONG).show()
                        finish()
                    }
                },
                onDismiss = {
                    stopAlerts()
                    onHandled()
                    finish()
                },
                onInteractionStateChanged = { interacting ->
                    if (interacting) {
                        pauseMainVibration()
                    } else {
                        resumeMainVibration()
                    }
                }
)
            }
        }
    }

    private fun startAlerts() {
        try {
            val alarmUri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
                ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE)
            ringtone = RingtoneManager.getRingtone(applicationContext, alarmUri)
            ringtone?.play()

            vibrator = getSystemService(Vibrator::class.java)
            val pattern = longArrayOf(0, 1000, 500, 1000)
            vibrator?.vibrate(VibrationEffect.createWaveform(pattern, 0))
        } catch (e: Exception) {
            Log.e("ReminderActivity", "Failed to start ringtone or vibrator", e)
        }
    }

    private fun stopAlerts() {
        try {
            ringtone?.stop()
            vibrator?.cancel()
        } catch (e: Exception) {
            Log.e("ReminderActivity", "Failed to stop ringtone or vibrator", e)
        }
    }

    private fun pauseMainVibration() {
        try {
            vibrator?.cancel()
        } catch (e: Exception) {
            Log.e("ReminderActivity", "Failed to pause vibrator", e)
        }
    }

    private fun resumeMainVibration() {
        try {
            val pattern = longArrayOf(0, 1000, 500, 1000)
            vibrator?.vibrate(VibrationEffect.createWaveform(pattern, 0))
        } catch (e: Exception) {
            Log.e("ReminderActivity", "Failed to resume vibrator", e)
        }
    }

    /** Handled here: clear the notification and stop the watch's alarm. */
    private fun onHandled() {
        WearReminderBridge.dismissOnWatch(this, taskId)
        (getSystemService(Context.NOTIFICATION_SERVICE) as android.app.NotificationManager)
            .cancel(taskId.hashCode())
    }

    override fun onDestroy() {
        stopAlerts()
        try {
            unregisterReceiver(closeReceiver)
        } catch (e: IllegalArgumentException) {
            // Never registered (finished early).
        }
        super.onDestroy()
    }
}
