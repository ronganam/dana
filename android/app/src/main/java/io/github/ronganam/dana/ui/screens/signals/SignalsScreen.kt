package io.github.ronganam.dana.ui.screens.signals

import android.Manifest
import android.app.TimePickerDialog
import android.content.Intent
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import io.github.ronganam.dana.data.signals.BluetoothChoice
import io.github.ronganam.dana.data.signals.LocationAccess
import io.github.ronganam.dana.data.signals.PhoneSignalStatus
import io.github.ronganam.dana.data.signals.PhoneSignals
import io.github.ronganam.dana.data.signals.SignalInfo
import io.github.ronganam.dana.data.signals.SignalSource
import io.github.ronganam.dana.data.signals.WakeWindow
import io.github.ronganam.dana.ui.components.GlassCard
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.ScreenTopBar
import io.github.ronganam.dana.ui.components.SectionLabel
import io.github.ronganam.dana.ui.components.StatusPill
import io.github.ronganam.dana.ui.theme.DanaHues
import io.github.ronganam.dana.ui.theme.hues
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import io.github.ronganam.dana.ui.viewmodels.SignalsController

/**
 * Settings → Signals: each signal Dana uses, its sources (the one in use
 * marked, the last report), the phone sources' switches with the access they
 * need, and the wake window.
 */
@Composable
fun SignalsScreen(viewModel: MainViewModel, onBack: () -> Unit) {
    val controller = viewModel.signalsController
    val overview by controller.overview.collectAsState()
    val failed by controller.failed.collectAsState()
    val phone by controller.phone.collectAsState()
    LaunchedEffect(Unit) { controller.refresh() }
    // Back from a system settings page (usage access, location, Bluetooth): read the access again.
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val obs = LifecycleEventObserver { _, e -> if (e == Lifecycle.Event.ON_RESUME) controller.refreshPhone() }
        lifecycle.addObserver(obs)
        onDispose { lifecycle.removeObserver(obs) }
    }

    Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally) {
        ScreenTopBar(
            title = "Signals",
            kicker = "What Dana notices",
            showSettings = false,
            navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back") } }
        )
        Column(
            modifier = Modifier
                .widthIn(max = 560.dp)
                .fillMaxWidth()
                .weight(1f)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp)
                .navigationBarsPadding()
                .padding(bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp)
        ) {
            Text(
                "When you wake up, get in the car or arrive somewhere, reminders and your briefing follow. " +
                    "Each signal comes from the first source that has it: Dana's phone app, then Home Assistant when it's connected.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(vertical = 8.dp)
            )
            val o = overview
            if (o == null && failed) {
                Text("Couldn't load the signals from your server.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error)
                OutlinedButton(onClick = { controller.refresh() }, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) { Text("Try again") }
            }
            // The phone's switches show even before (or without) the server's overview.
            val signals = o?.signals ?: ORDER.map { SignalInfo(it, emptyList()) }
            signals.forEach { signal ->
                val p = phone
                val controls = when (signal.signal) {
                    "wake" -> p != null || o?.wakeWindow != null
                    "car", "location" -> p != null
                    else -> false
                }
                // Before the overview loads, only the signals with something to change here.
                if (o != null || controls) {
                    Spacer(Modifier.height(8.dp))
                    SectionLabel(signalLabel(signal.signal), hue = signalHue(hues, signal.signal))
                    GlassCard(Modifier.fillMaxWidth()) {
                        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                            if (o != null && signal.sources.isEmpty()) {
                                Text("No source can give this yet.", style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            signal.sources.forEach { SourceRow(it) }
                            if (controls) SignalControls(signal.signal, p, o?.wakeWindow, controller, ruled = o != null)
                        }
                    }
                }
            }
        }
    }
}

/** The phone's switches under a signal (and the wake window under wake), after a rule when sources are above. */
@Composable
private fun SignalControls(signal: String, p: PhoneSignalStatus?, window: WakeWindow?, controller: SignalsController, ruled: Boolean) {
    if (ruled) Rule()
    when (signal) {
        "wake" -> {
            if (p != null) {
                ScreenTimeControl(p, controller)
                Rule()
            }
            WakeWindowControl(window, controller)
        }
        "car" -> if (p != null) CarControl(p, controller)
        "location" -> if (p != null) PlacesControl(p, controller)
    }
}

/** The signals in the server's order (shown before the overview loads). */
private val ORDER = listOf("wake", "screen", "car", "location", "sleep", "weather")

internal fun signalLabel(signal: String): String = when (signal) {
    "wake" -> "Waking up"
    "screen" -> "Screen time"
    "car" -> "Car"
    "location" -> "Places"
    "sleep" -> "Sleep"
    "weather" -> "Weather"
    else -> signal.replace('_', ' ').replaceFirstChar { it.uppercase() }
}

private fun signalHue(h: DanaHues, signal: String): Color = when (signal) {
    "wake", "sleep" -> h.amber
    "screen" -> h.teal
    "car", "location" -> h.sage
    else -> h.sky
}

/** "Last · 07:12 · unlock", "Last · Sat 21 Nov 18:40", or "Nothing yet". */
internal fun lastLine(source: SignalSource, now: java.time.ZonedDateTime = java.time.ZonedDateTime.now()): String {
    val at = source.lastAt?.let { runCatching { java.time.OffsetDateTime.parse(it) }.getOrNull() }
        ?: return "Nothing yet"
    val local = at.atZoneSameInstant(now.zone)
    val time = local.toLocalTime().withSecond(0).withNano(0).toString()
    val day = if (local.toLocalDate() == now.toLocalDate()) time
        else "${local.format(java.time.format.DateTimeFormatter.ofPattern("EEE d MMM", java.util.Locale.getDefault()))} $time"
    return listOf("Last", day, source.detail).filter { it.isNotBlank() }.joinToString(" · ")
}

@Composable
private fun SourceRow(source: SignalSource) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(source.label, style = MaterialTheme.typography.titleSmall)
            MonoLabel(lastLine(source), modifier = Modifier.padding(top = 2.dp))
        }
        if (source.active) {
            Spacer(Modifier.width(8.dp))
            StatusPill("In use", hues.sage)
        }
    }
}

@Composable
private fun Rule() = HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)

@Composable
private fun SwitchRow(title: String, body: String, checked: Boolean, enabled: Boolean = true, onChange: (Boolean) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleSmall)
            Text(body, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Spacer(Modifier.width(12.dp))
        Switch(checked = checked, enabled = enabled, onCheckedChange = onChange)
    }
}

@Composable
private fun Missing(text: String) =
    Text(text, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)

@Composable
private fun ActionButton(text: String, onClick: () -> Unit) =
    OutlinedButton(onClick = onClick, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) { Text(text) }

/** "Wake from screen time": the unlocks always go; this adds the screen's on/off history (Usage access). */
@Composable
private fun ScreenTimeControl(p: PhoneSignalStatus, controller: SignalsController) {
    val context = LocalContext.current
    SwitchRow(
        "Wake from screen time",
        if (p.screenTimeSupported) "Your unlocks tell Dana you may be up; how long the screen then stays on confirms it. " +
            "Only when the screen was on is sent, never which apps."
        else "Needs Android 9 or newer. Your unlocks still count.",
        checked = p.screenTime && p.screenTimeSupported,
        enabled = p.screenTimeSupported
    ) { on ->
        controller.setScreenTime(on)
        if (on && !p.usageAccess) PhoneSignals.openUsageAccess(context)
    }
    if (p.screenTime && p.screenTimeSupported && !p.usageAccess) {
        Missing("Needs usage access: find Dana in the list and allow it.")
        ActionButton("Allow usage access") { PhoneSignals.openUsageAccess(context) }
    }
}

/** The wake window: shown, set by hand (two times), or back to the learned one. */
@Composable
private fun WakeWindowControl(window: WakeWindow?, controller: SignalsController) {
    val context = LocalContext.current
    val w = window ?: WakeWindow.DEFAULT
    fun pick(current: String, onPicked: (String) -> Unit) {
        val m = WakeWindow.minutes(current) ?: 0
        TimePickerDialog(context, { _, h, min -> onPicked(WakeWindow.hhmm(h * 60 + min)) }, m / 60, m % 60, android.text.format.DateFormat.is24HourFormat(context)).show()
    }
    Text("Wake window", style = MaterialTheme.typography.titleSmall)
    Text(
        if (w.learned) "Learned from when you unlock your phone in the morning. A wake outside it is ignored."
        else "Set by you. A wake outside it is ignored.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant
    )
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        TimeChip(w.start) { pick(w.start) { controller.setWakeWindow(it, w.end) } }
        Text("to", style = MaterialTheme.typography.bodyMedium)
        TimeChip(w.end) { pick(w.end) { controller.setWakeWindow(w.start, it) } }
        Spacer(Modifier.weight(1f))
        if (!w.learned) TextButton(onClick = { controller.resetWakeWindow() }) { Text("Use the learned one") }
    }
}

@Composable
private fun TimeChip(time: String, onClick: () -> Unit) {
    Text(
        time,
        style = MaterialTheme.typography.headlineSmall,
        color = MaterialTheme.colorScheme.primary,
        modifier = Modifier.clickable(onClick = onClick).padding(horizontal = 4.dp, vertical = 6.dp)
    )
}

/** The car: on/off, which paired Bluetooth device is the car, Android Auto. */
@Composable
private fun CarControl(p: PhoneSignalStatus, controller: SignalsController) {
    SwitchRow(
        "Know when I'm in the car",
        "Connecting to your car's Bluetooth, or Android Auto starting, counts as getting in; disconnecting as getting out. " +
            "The device's address stays on this phone.",
        checked = p.car
    ) { controller.setCar(it) }
    if (p.car) CarDeviceRow(p, controller)
}

/** "This is my car": the paired device that counts, chosen from a list (Nearby devices asked first on Android 12+). */
@Composable
private fun CarDeviceRow(p: PhoneSignalStatus, controller: SignalsController) {
    val context = LocalContext.current
    var picking by remember { mutableStateOf(false) }
    val bluetooth = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        controller.refreshPhone()
        if (granted) picking = true
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text("This is my car", style = MaterialTheme.typography.titleSmall)
            Text(p.carDevice ?: "No Bluetooth device chosen: only Android Auto counts.", style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        TextButton(onClick = {
            if (!p.bluetoothAllowed && Build.VERSION.SDK_INT >= 31) bluetooth.launch(Manifest.permission.BLUETOOTH_CONNECT) else picking = true
        }) { Text(if (p.carDevice == null) "Choose" else "Change") }
    }
    if (picking) CarPicker(controller.pairedDevices(), current = p.carDevice, onPick = { picking = false; controller.setCarDevice(it) },
        onPair = {
            picking = false
            runCatching { context.startActivity(Intent(Settings.ACTION_BLUETOOTH_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
        }, onDismiss = { picking = false })
}

@Composable
private fun CarPicker(devices: List<BluetoothChoice>, current: String?, onPick: (BluetoothChoice?) -> Unit, onPair: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Which is your car?") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                if (devices.isEmpty()) Text("No paired Bluetooth devices. Pair your car in Bluetooth settings first.")
                devices.forEach { d ->
                    Text(
                        d.name,
                        style = MaterialTheme.typography.bodyLarge,
                        color = if (d.name == current) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface,
                        modifier = Modifier.fillMaxWidth().clickable { onPick(d) }.padding(vertical = 12.dp)
                    )
                }
            }
        },
        confirmButton = { TextButton(onClick = onPair) { Text("Bluetooth settings") } },
        dismissButton = {
            if (current != null) TextButton(onClick = { onPick(null) }) { Text("No device") }
            else TextButton(onClick = onDismiss) { Text("Cancel") }
        }
    )
}

/** Places: geofences for the zones, which need location all the time (asked in two steps). */
@Composable
private fun PlacesControl(p: PhoneSignalStatus, controller: SignalsController) {
    var explainBackground by remember { mutableStateOf(false) }
    val background = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        controller.setPlaces(true)
    }
    val foreground = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { result ->
        controller.refreshPhone()
        if (result[Manifest.permission.ACCESS_FINE_LOCATION] == true) {
            if (Build.VERSION.SDK_INT >= 29) explainBackground = true else controller.setPlaces(true)
        }
    }
    fun ask(access: LocationAccess) = when (access) {
        LocationAccess.NONE -> foreground.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION))
        LocationAccess.WHILE_IN_USE -> explainBackground = true
        LocationAccess.ALWAYS -> Unit
    }
    SwitchRow(
        "Place reminders",
        "Arriving at or leaving your places (Home and the others in the web's Settings → Zones) fires their reminders. " +
            "The phone watches those circles itself; Dana only hears that you arrived or left, never where you are.",
        checked = p.places
    ) { on ->
        controller.setPlaces(on)
        if (on) ask(p.location)
    }
    if (p.places) when (p.location) {
        LocationAccess.NONE -> { Missing("Needs location access."); ActionButton("Allow location") { ask(p.location) } }
        LocationAccess.WHILE_IN_USE -> { Missing("Needs location all the time, or arrivals are missed while Dana is closed."); ActionButton("Allow all the time") { ask(p.location) } }
        LocationAccess.ALWAYS -> MonoLabel(if (p.fences == 1) "Watching 1 place" else "Watching ${p.fences} places")
    }
    if (explainBackground) AlertDialog(
        onDismissRequest = { explainBackground = false },
        title = { Text("Allow location all the time") },
        text = {
            Text(
                if (Build.VERSION.SDK_INT >= 30) "On the next screen choose “Allow all the time”. Arrivals and departures are noticed by the phone even while Dana is closed."
                else "Choose “Allow all the time” so arrivals and departures are noticed even while Dana is closed."
            )
        },
        confirmButton = {
            TextButton(onClick = {
                explainBackground = false
                if (Build.VERSION.SDK_INT >= 29) background.launch(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
            }) { Text("Continue") }
        },
        dismissButton = { TextButton(onClick = { explainBackground = false }) { Text("Not now") } }
    )
}
