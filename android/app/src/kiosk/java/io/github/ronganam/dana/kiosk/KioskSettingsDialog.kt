package io.github.ronganam.dana.kiosk

import android.content.Intent
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.Button
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import io.github.ronganam.dana.BuildConfig
import io.github.ronganam.dana.wake.DanaSpeaker
import io.github.ronganam.dana.wake.MicHub
import io.github.ronganam.dana.wake.WakeBus
import io.github.ronganam.dana.wake.WakePrefs

@Composable
fun KioskSettingsDialog(
    speaker: DanaSpeaker,
    onDismiss: () -> Unit,
    onSaved: () -> Unit,
    onTestWake: () -> Unit
) {
    val context = LocalContext.current
    var server by remember { mutableStateOf(WakePrefs.serverUrl(context)) }
    var token by remember { mutableStateOf(WakePrefs.apiToken(context)) }
    var path by remember { mutableStateOf(KioskPrefs.dashboardPath(context)) }
    var threshold by remember { mutableFloatStateOf(WakePrefs.threshold(context)) }
    var speak by remember { mutableStateOf(WakePrefs.speakReplies(context)) }
    var followUp by remember { mutableStateOf(WakePrefs.followUp(context)) }

    val level by MicHub.level.collectAsState()
    val score by WakeBus.score.collectAsState()
    val status by WakeBus.status.collectAsState()

    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(
            shape = MaterialTheme.shapes.extraLarge,
            color = MaterialTheme.colorScheme.surface,
            modifier = Modifier.widthIn(max = 620.dp).fillMaxWidth().padding(24.dp)
        ) {
            Column(
                Modifier.padding(24.dp).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Text("Dana kiosk", style = MaterialTheme.typography.headlineSmall)
                Text("v${BuildConfig.VERSION_NAME} · $status", style = MaterialTheme.typography.bodySmall)

                OutlinedTextField(server, { server = it }, label = { Text("Server URL") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth(), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri))
                OutlinedTextField(token, { token = it }, label = { Text("API token") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth(), visualTransformation = PasswordVisualTransformation())
                OutlinedTextField(path, { path = it }, label = { Text("Dashboard path") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth())

                Text("Wake word sensitivity — fires at score ≥ %.2f".format(threshold), style = MaterialTheme.typography.titleSmall)
                Slider(value = threshold, onValueChange = { threshold = it }, valueRange = 0.2f..0.9f)
                Text("Live score (2 s peak): %.2f".format(score), style = MaterialTheme.typography.bodyMedium)
                LinearProgressIndicator(progress = { score.coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth())
                val gain = io.github.ronganam.dana.wake.GainStream.gainFor(level.noiseFloor)
                Text("Mic level %.0f · room noise %.0f (voice gain %.1f×, speech > %.0f raw)".format(
                    level.rms, level.noiseFloor, gain,
                    io.github.ronganam.dana.wake.GainStream.thresholdFor(level.noiseFloor) / gain),
                    style = MaterialTheme.typography.bodyMedium)
                LinearProgressIndicator(progress = { (level.rms / 3000.0).toFloat().coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth())

                ToggleRow("Speak replies", speak) { speak = it }
                if (!speaker.hebrewAvailable) {
                    Text("No Hebrew voice installed — Hebrew replies are shown but not spoken. Install “Speech Services by Google” and pick it as the TTS engine.",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                }
                ToggleRow("Listen for a follow-up after replying", followUp) { followUp = it }

                Spacer(Modifier.height(4.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = onTestWake) { Text("Open assistant") }
                    OutlinedButton(onClick = {
                        context.startActivity(Intent(Settings.ACTION_HOME_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                    }) { Text("Home app") }
                    OutlinedButton(onClick = {
                        context.startActivity(Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                    }) { Text("Android settings") }
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                    TextButton(onClick = onDismiss) { Text("Cancel") }
                    Button(onClick = {
                        KioskPrefs.save(context, server, token, path, threshold, speak, followUp)
                        onSaved()
                    }) { Text("Save & reload") }
                }
            }
        }
    }
}

@Composable
private fun ToggleRow(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.weight(1f), style = MaterialTheme.typography.bodyLarge)
        Switch(checked = checked, onCheckedChange = onChange)
    }
}
