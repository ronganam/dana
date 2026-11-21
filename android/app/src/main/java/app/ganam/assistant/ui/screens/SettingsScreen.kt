package app.ganam.assistant.ui.screens

import android.content.Context
import android.content.Intent
import android.provider.Settings
import android.widget.Toast
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import app.ganam.assistant.BuildConfig
import app.ganam.assistant.data.update.UpdateManager
import app.ganam.assistant.data.voice.DeviceSpeech
import app.ganam.assistant.ui.components.GlassCard
import app.ganam.assistant.ui.components.ScreenTopBar
import app.ganam.assistant.ui.components.SectionLabel
import app.ganam.assistant.ui.theme.ThemeMode
import app.ganam.assistant.ui.theme.ThemeSettings
import app.ganam.assistant.ui.viewmodels.MainViewModel
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import app.ganam.assistant.data.sync.CallUploadSettings
import app.ganam.assistant.data.sync.CallUploads
import app.ganam.assistant.data.notifications.NotificationExtractor
import app.ganam.assistant.data.notifications.NotificationSettings
import app.ganam.assistant.ui.components.InkChip
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(viewModel: MainViewModel, onBack: (() -> Unit)? = null) {
    val context = LocalContext.current
    val prefs = context.getSharedPreferences("GanamPrefs", Context.MODE_PRIVATE)

    var serverUrl by remember { mutableStateOf(prefs.getString("server_url", "https://") ?: "") }
    var apiToken by remember { mutableStateOf(prefs.getString("api_token", "") ?: "") }
    var liveTranscription by remember { mutableStateOf(prefs.getBoolean(DeviceSpeech.PREF_KEY, true)) }

    val appUpdateInfo by viewModel.appUpdateInfo.collectAsState()
    val updateCheckDone by viewModel.updateCheckDone.collectAsState()
    val wearVersionInfo by viewModel.wearVersionInfo.collectAsState()
    val wearUpdateInfo by viewModel.wearUpdateInfo.collectAsState()

    var downloadProgress by remember { mutableStateOf(-1) }
    val scope = rememberCoroutineScope()

    Column(modifier = Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally) {
        ScreenTopBar(
            title = "Settings",
            showSettings = false,
            navigationIcon = onBack?.let { back ->
                {
                    IconButton(onClick = back) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                }
            }
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
            // --- Appearance ---
            SectionLabel("Appearance")
            GlassCard(modifier = Modifier.fillMaxWidth()) {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("Theme", style = MaterialTheme.typography.titleSmall)
                    SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
                        val modes = ThemeMode.values()
                        modes.forEachIndexed { index, mode ->
                            SegmentedButton(
                                selected = ThemeSettings.mode == mode,
                                onClick = { ThemeSettings.set(context, mode) },
                                shape = SegmentedButtonDefaults.itemShape(index, modes.size)
                            ) { Text(mode.label) }
                        }
                    }
                }
            }

            // --- Server ---
            Spacer(Modifier.height(8.dp))
            SectionLabel("Server")
            GlassCard(modifier = Modifier.fillMaxWidth()) {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    OutlinedTextField(
                        value = serverUrl,
                        onValueChange = { serverUrl = it },
                        label = { Text("Server URL") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                    OutlinedTextField(
                        value = apiToken,
                        onValueChange = { apiToken = it },
                        label = { Text("API Token") },
                        singleLine = true,
                        visualTransformation = PasswordVisualTransformation(),
                        modifier = Modifier.fillMaxWidth()
                    )
                    Button(
                        onClick = {
                            prefs.edit()
                                .putString("server_url", serverUrl)
                                .putString("api_token", apiToken)
                                .apply()
                            viewModel.updateServerConfig(serverUrl, apiToken)
                            Toast.makeText(context, "Settings Saved", Toast.LENGTH_SHORT).show()
                        },
                        modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
                    ) { Text("Save") }
                }
            }

            // --- Assistant ---
            Spacer(Modifier.height(8.dp))
            SectionLabel("Dana")
            GlassCard(modifier = Modifier.fillMaxWidth()) {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        "Use Dana when you long-press the power button or say your hotword.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    OutlinedButton(
                        onClick = {
                            try {
                                context.startActivity(Intent(Settings.ACTION_VOICE_INPUT_SETTINGS))
                            } catch (e: Exception) {
                                Toast.makeText(context, "Could not open settings", Toast.LENGTH_SHORT).show()
                            }
                        },
                        modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
                    ) { Text("Set as default assistant") }
                    HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text("Live transcription", style = MaterialTheme.typography.titleSmall)
                            Text(
                                "See your words as you speak (Google speech recognition, Hebrew + English). " +
                                    "Off: the server transcribes after you stop.",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                        Spacer(Modifier.width(12.dp))
                        Switch(
                            checked = liveTranscription,
                            onCheckedChange = {
                                liveTranscription = it
                                prefs.edit().putBoolean(DeviceSpeech.PREF_KEY, it).apply()
                            }
                        )
                    }
                }
            }

            // --- Call recordings ---
            Spacer(Modifier.height(8.dp))
            SectionLabel("Call recordings")
            CallRecordingSettings()

            // --- Caller card (phone only) ---
            if (!BuildConfig.KIOSK && !BuildConfig.TV) {
                Spacer(Modifier.height(8.dp))
                SectionLabel("Caller card")
                CallerCardSettings()
            }

            // --- Pop-ups (suggestions, urgent alerts) ---
            Spacer(Modifier.height(8.dp))
            SectionLabel("Pop-ups")
            PopUpSettings()

            // --- Notifications (Inbox) ---
            Spacer(Modifier.height(8.dp))
            SectionLabel("Notifications")
            NotificationInboxSettings(viewModel)

            // --- App update ---
            Spacer(Modifier.height(8.dp))
            SectionLabel("About & updates")
            GlassCard(modifier = Modifier.fillMaxWidth()) {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(
                        text = "Version ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})",
                        style = MaterialTheme.typography.titleSmall
                    )

                    val update = appUpdateInfo
                    when {
                        update == null && !updateCheckDone -> Text(
                            "Checking for updates…",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                        update == null -> Text(
                            "You're on the latest version",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                        else -> {
                            Text(
                                text = "Update available: ${update.versionName} (${update.versionCode})",
                                style = MaterialTheme.typography.titleMedium,
                                color = MaterialTheme.colorScheme.primary
                            )
                            if (update.releaseNotes.isNotEmpty()) {
                                Text(update.releaseNotes, style = MaterialTheme.typography.bodySmall)
                            }
                            if (downloadProgress in 0..99) {
                                LinearProgressIndicator(progress = { downloadProgress / 100f }, modifier = Modifier.fillMaxWidth())
                                Text("Downloading… $downloadProgress%", style = MaterialTheme.typography.bodySmall)
                            } else {
                                Button(
                                    onClick = {
                                        val savedServerUrl = prefs.getString("server_url", serverUrl) ?: serverUrl
                                        scope.launch {
                                            downloadProgress = 0
                                            val file = UpdateManager.downloadUpdate(
                                                context, savedServerUrl, update
                                            ) { percent -> downloadProgress = percent }
                                            downloadProgress = -1
                                            if (file != null) {
                                                if (!UpdateManager.installApk(context, file)) {
                                                    Toast.makeText(context, "Could not launch installer", Toast.LENGTH_LONG).show()
                                                }
                                            } else {
                                                Toast.makeText(context, "Download failed — try again", Toast.LENGTH_LONG).show()
                                            }
                                        }
                                    },
                                    modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
                                ) { Text("Download & install") }
                            }
                        }
                    }

                    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)

                    val wearVersion = wearVersionInfo
                    val latestWear = wearUpdateInfo
                    val wearBehind = latestWear != null && wearVersion != null && latestWear.versionCode > wearVersion.versionCode
                    Text(
                        text = when {
                            wearVersion == null -> "Watch app: not connected (open the watch app once)"
                            wearBehind -> "Watch app ${wearVersion.versionName} — update ${latestWear!!.versionName} available via deploy script"
                            else -> "Watch app ${wearVersion.versionName}: up to date"
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = if (wearBehind) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant
                    )

                    OutlinedButton(
                        onClick = {
                            viewModel.checkForAppUpdate()
                            viewModel.fetchWearVersion()
                        },
                        enabled = downloadProgress !in 0..99,
                        modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
                    ) { Text("Check for updates") }
                }
            }
        }
    }
}

/** Auto-upload of call recordings: on/off, length threshold, Wi-Fi only, never-upload contacts, backlog. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun CallRecordingSettings() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var auto by remember { mutableStateOf(CallUploadSettings.autoUpload(context)) }
    var threshold by remember { mutableFloatStateOf(CallUploadSettings.thresholdSeconds(context).toFloat()) }
    var wifiOnly by remember { mutableStateOf(CallUploadSettings.wifiOnly(context)) }
    var skipped by remember { mutableStateOf(CallUploadSettings.skippedContacts(context).sorted()) }
    var callLogGranted by remember {
        mutableStateOf(context.checkSelfPermission(android.Manifest.permission.READ_CALL_LOG) == PackageManager.PERMISSION_GRANTED)
    }
    var backlogBusy by remember { mutableStateOf(false) }
    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { callLogGranted = it }

    GlassCard(modifier = Modifier.fillMaxWidth()) {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Upload calls automatically", style = MaterialTheme.typography.titleSmall)
                    Text(
                        "Recordings are transcribed, labelled and turned into suggested reminders. Shorter calls ask first.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                Spacer(Modifier.width(12.dp))
                Switch(checked = auto, onCheckedChange = { auto = it; CallUploadSettings.setAutoUpload(context, it) })
            }
            if (auto) {
                Text("Ask first for calls under ${threshold.toInt()}s", style = MaterialTheme.typography.bodyMedium)
                Slider(
                    value = threshold,
                    onValueChange = { threshold = it },
                    onValueChangeFinished = { CallUploadSettings.setThresholdSeconds(context, threshold.toInt()) },
                    valueRange = 0f..60f,
                    steps = 11
                )
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Wi-Fi only", style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
                Switch(checked = wifiOnly, onCheckedChange = { wifiOnly = it; CallUploadSettings.setWifiOnly(context, it) })
            }
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            if (!callLogGranted) {
                Text(
                    "Allow call-log access so uploads know who called whom, the number and the direction.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                OutlinedButton(
                    onClick = { permissionLauncher.launch(android.Manifest.permission.READ_CALL_LOG) },
                    modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
                ) { Text("Allow call log") }
            }
            Text("Never upload", style = MaterialTheme.typography.titleSmall)
            if (skipped.isEmpty()) {
                Text(
                    "No one. Use “Never upload calls with…” on a short-call prompt to add someone.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            } else {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    skipped.forEach { name ->
                        InkChip(name, onClick = {
                            CallUploadSettings.removeSkipped(context, name)
                            skipped = CallUploadSettings.skippedContacts(context).sorted()
                        }, trailing = { Text("✕", style = MaterialTheme.typography.labelSmall) })
                    }
                }
            }
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            OutlinedButton(
                enabled = !backlogBusy,
                onClick = {
                    backlogBusy = true
                    scope.launch {
                        val n = withContext(Dispatchers.IO) { CallUploads.uploadPastRecordings(context) }
                        backlogBusy = false
                        Toast.makeText(
                            context,
                            if (n == 0) "Nothing new to upload" else "Uploading $n recordings in the background",
                            Toast.LENGTH_LONG
                        ).show()
                    }
                },
                modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
            ) { Text(if (backlogBusy) "Looking…" else "Upload past recordings") }
        }
    }
}

/** Inbox capture: access, on/off, sensitive apps, per-app rules and the deny list. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun NotificationInboxSettings(viewModel: MainViewModel) {
    val context = LocalContext.current
    val controller = viewModel.notificationsController
    val access by controller.accessGranted.collectAsState()
    val apps by controller.apps.collectAsState()
    var enabled by remember { mutableStateOf(NotificationSettings.enabled(context)) }
    var skipSensitive by remember { mutableStateOf(NotificationSettings.skipSensitive(context)) }
    var denied by remember { mutableStateOf(NotificationSettings.deniedPackages(context).sorted()) }

    LaunchedEffect(Unit) { controller.refreshAccess(); controller.fetchApps() }

    GlassCard(modifier = Modifier.fillMaxWidth()) {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(
                if (access) "Notification access: on" else "Notification access: off",
                style = MaterialTheme.typography.titleSmall
            )
            Text(
                "Dana sorts what your phone shows into Needs you / Everything / Suggested to dismiss, suggests reminders " +
                    "from them and sends a digest morning and evening. It never dismisses anything by itself.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
            OutlinedButton(
                onClick = { NotificationSettings.openAccessSettings(context) },
                modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
            ) { Text(if (access) "Manage notification access" else "Allow notification access") }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Send notifications to Dana", style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
                Switch(checked = enabled, onCheckedChange = { enabled = it; NotificationSettings.setEnabled(context, it) })
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Skip banking & authenticator apps", style = MaterialTheme.typography.titleSmall)
                    Text(
                        "For the Inbox. One-time codes are always hidden before upload. With Money connected, " +
                            "bank and card app notifications go only to Money, never to the Inbox.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                Spacer(Modifier.width(12.dp))
                Switch(checked = skipSensitive, onCheckedChange = { skipSensitive = it; NotificationSettings.setSkipSensitive(context, it) })
            }

            if (apps.isNotEmpty()) {
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                Text("Apps (last 30 days)", style = MaterialTheme.typography.titleSmall)
                apps.take(25).forEach { app ->
                    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        val handled = app.opened + app.swiped
                        val swipedPct = if (handled > 0) " · ${app.swiped * 100 / handled}% swiped away" else ""
                        Text("${app.appName} · ${app.total}$swipedPct", style = MaterialTheme.typography.bodyMedium)
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            InkChip("Always needs me", selected = app.rule == "important",
                                onClick = { controller.setAppRule(app.packageName, if (app.rule == "important") "none" else "important") })
                            InkChip("Suggest dismissing", selected = app.rule == "mute",
                                onClick = { controller.setAppRule(app.packageName, if (app.rule == "mute") "none" else "mute") })
                            InkChip("Stop capturing", onClick = {
                                controller.stopCapturing(app.packageName)
                                denied = NotificationSettings.deniedPackages(context).sorted()
                            })
                        }
                    }
                }
            }

            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            Text("Never captured", style = MaterialTheme.typography.titleSmall)
            if (denied.isEmpty()) {
                Text(
                    "No apps. Use “Stop capturing” on an app or notification to add one.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            } else {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    denied.forEach { pkg ->
                        InkChip(NotificationExtractor.appName(context, pkg), onClick = {
                            NotificationSettings.allow(context, pkg)
                            denied = NotificationSettings.deniedPackages(context).sorted()
                        }, trailing = { Text("✕", style = MaterialTheme.typography.labelSmall) })
                    }
                }
            }
        }
    }
}


/**
 * The two system switches the pop-up screens need: "Display over other apps"
 * (open a screen while you're using the phone) and, on Android 14+, "Full
 * screen notifications" (turn the screen on over the lock screen).
 */
@Composable
private fun PopUpSettings() {
    val context = LocalContext.current
    var overlay by remember { mutableStateOf(app.ganam.assistant.SuggestionsReceiver.canPopUp(context)) }
    var fullScreen by remember { mutableStateOf(app.ganam.assistant.SuggestionsReceiver.canUseFullScreen(context)) }
    val lifecycle = androidx.compose.ui.platform.LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val obs = androidx.lifecycle.LifecycleEventObserver { _, e ->
            if (e == androidx.lifecycle.Lifecycle.Event.ON_RESUME) {
                overlay = app.ganam.assistant.SuggestionsReceiver.canPopUp(context)
                fullScreen = app.ganam.assistant.SuggestionsReceiver.canUseFullScreen(context)
            }
        }
        lifecycle.addObserver(obs)
        onDispose { lifecycle.removeObserver(obs) }
    }
    GlassCard(modifier = Modifier.fillMaxWidth()) {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(
                "New suggestions after a call or a message, and urgent messages, open a screen so they aren't missed.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
            Text(
                if (overlay) "Pop up while using the phone: allowed" else "Pop up while using the phone: off — only a notification",
                style = MaterialTheme.typography.titleSmall
            )
            if (!overlay) OutlinedButton(
                onClick = {
                    context.startActivity(
                        android.content.Intent(android.provider.Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                            android.net.Uri.parse("package:${context.packageName}"))
                            .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
                    )
                },
                modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
            ) { Text("Allow “Display over other apps”") }
            Text(
                if (fullScreen) "Over the lock screen: allowed" else "Over the lock screen: off",
                style = MaterialTheme.typography.titleSmall
            )
            if (!fullScreen && android.os.Build.VERSION.SDK_INT >= 34) OutlinedButton(
                onClick = {
                    context.startActivity(
                        android.content.Intent(android.provider.Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT,
                            android.net.Uri.parse("package:${context.packageName}"))
                            .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
                    )
                },
                modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
            ) { Text("Allow full-screen notifications") }
        }
    }
}

/** The floating card (and watch card) with what's going on with the person calling. */
@Composable
private fun CallerCardSettings() {
    val context = LocalContext.current
    var enabled by remember { mutableStateOf(app.ganam.assistant.callcard.CallerCards.enabled(context)) }
    var missing by remember { mutableStateOf(app.ganam.assistant.callcard.CallerCards.missing(context)) }
    val launcher = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.RequestMultiplePermissions()
    ) { missing = app.ganam.assistant.callcard.CallerCards.missing(context) }
    // Re-check after coming back from the overlay settings screen.
    val lifecycle = androidx.compose.ui.platform.LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val obs = androidx.lifecycle.LifecycleEventObserver { _, e ->
            if (e == androidx.lifecycle.Lifecycle.Event.ON_RESUME) missing = app.ganam.assistant.callcard.CallerCards.missing(context)
        }
        lifecycle.addObserver(obs)
        onDispose { lifecycle.removeObserver(obs) }
    }
    GlassCard(modifier = Modifier.fillMaxWidth()) {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Show who's calling", style = MaterialTheme.typography.titleSmall)
                    Text(
                        "When someone calls, a small card floats over the call screen (and shows on your watch) with " +
                            "what's going on with them: last call, open items, birthday. Drag it anywhere; tap it for their page.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                Spacer(Modifier.width(12.dp))
                Switch(checked = enabled, onCheckedChange = { enabled = it; app.ganam.assistant.callcard.CallerCards.setEnabled(context, it) })
            }
            if (enabled && missing.isNotEmpty()) {
                Text("Still needed: ${missing.joinToString(", ")}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                val perms = buildList {
                    if ("phone state" in missing) add(android.Manifest.permission.READ_PHONE_STATE)
                    if ("call log" in missing) add(android.Manifest.permission.READ_CALL_LOG)
                }
                if (perms.isNotEmpty()) {
                    OutlinedButton(onClick = { launcher.launch(perms.toTypedArray()) }, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) {
                        Text("Allow phone access")
                    }
                }
                if ("display over other apps" in missing) {
                    OutlinedButton(onClick = {
                        runCatching {
                            context.startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, android.net.Uri.parse("package:${context.packageName}")))
                        }
                    }, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) { Text("Allow display over other apps") }
                }
            }
        }
    }
}
