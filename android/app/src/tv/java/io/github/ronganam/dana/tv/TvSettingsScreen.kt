package io.github.ronganam.dana.tv

import io.github.ronganam.dana.data.network.ServerConfig
import android.content.Intent
import android.provider.Settings
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.nativeKeyCode
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import io.github.ronganam.dana.BuildConfig
import io.github.ronganam.dana.ui.theme.Success
import io.github.ronganam.dana.wake.MicHub
import io.github.ronganam.dana.wake.WakeBus
import io.github.ronganam.dana.wake.WakePrefs
import kotlinx.coroutines.launch

private enum class Section(val label: String) {
    CONNECTION("Connection"), APPS("Apps"), HOME("Home controls"), CAMERAS("Cameras"),
    BUTTONS("Remote buttons"), VOICE("Hey Dana"), PIP("Picture-in-picture"), SYSTEM("System"),
}

private val PICKER_DOMAINS = listOf(
    "light" to "Lights", "switch" to "Switches", "climate" to "Climate", "cover" to "Covers",
    "scene" to "Scenes", "script" to "Scripts", "vacuum" to "Vacuums", "fan" to "Fans",
    "lock" to "Locks", "input_boolean" to "Toggles", "media_player" to "Media", "sensor" to "Sensors",
)

@Composable
fun TvSettingsScreen(model: HomeModel, onBack: () -> Unit, onAskDana: () -> Unit) {
    var section by remember { mutableStateOf(Section.CONNECTION) }
    val settings by TvConfig.settings.collectAsState()
    val firstNav = remember { FocusRequester() }
    LaunchedEffect(Unit) { runCatching { firstNav.requestFocus() } }

    Row(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        Column(
            Modifier.width(250.dp).fillMaxHeight().background(MaterialTheme.colorScheme.surfaceContainerLow)
                .padding(start = 48.dp, end = 18.dp, top = 27.dp, bottom = 20.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp)
        ) {
            TvMono("Dana TV · v${BuildConfig.VERSION_NAME}")
            Text("Settings", style = TvType.title, color = MaterialTheme.colorScheme.onSurface)
            Spacer(Modifier.height(8.dp))
            Section.entries.forEachIndexed { i, s ->
                FocusTile(
                    Modifier.fillMaxWidth().height(36.dp),
                    shape = RoundedCornerShape(8.dp),
                    focusRequester = if (i == 0) firstNav else null,
                    background = if (s == section) MaterialTheme.colorScheme.surfaceContainerHighest else MaterialTheme.colorScheme.surfaceContainerLow,
                    onFocus = { if (it) section = s },
                    onClick = { section = s },
                ) { _ ->
                    Text(s.label, Modifier.align(Alignment.CenterStart).padding(horizontal = 12.dp), style = TvType.body,
                        color = if (s == section) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Spacer(Modifier.weight(1f))
            TvChip("Back to home", onClick = onBack)
        }
        LazyColumn(
            Modifier.weight(1f).fillMaxHeight(),
            contentPadding = PaddingValues(start = 32.dp, end = 48.dp, top = 30.dp, bottom = 40.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            when (section) {
                Section.CONNECTION -> item { ConnectionSection(model) }
                Section.APPS -> appsSection(model, settings)
                Section.HOME -> homeSection(model, settings)
                Section.CAMERAS -> camerasSection(settings)
                Section.BUTTONS -> item { ButtonsSection(model) }
                Section.VOICE -> item { VoiceSection(onAskDana) }
                Section.PIP -> item { PipSection() }
                Section.SYSTEM -> item { SystemSection() }
            }
        }
    }
}

@Composable
private fun Heading(title: String, note: String? = null) {
    Column(Modifier.padding(bottom = 4.dp)) {
        Text(title, style = TvType.title, color = MaterialTheme.colorScheme.onSurface)
        note?.let { Text(it, style = TvType.small, color = MaterialTheme.colorScheme.onSurfaceVariant) }
    }
}

/** A list row: label (+ secondary) on the left, chips on the right. */
@Composable
private fun SettingRow(label: String, secondary: String? = null, actions: @Composable () -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = 44.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(label, style = TvType.body, color = MaterialTheme.colorScheme.onSurface, maxLines = 1, overflow = TextOverflow.Ellipsis)
            secondary?.let { TvMono(it, color = MaterialTheme.colorScheme.outline) }
        }
        ChipRow(actions)
    }
}

private fun <T> List<T>.moved(index: Int, by: Int): List<T> {
    val to = (index + by).coerceIn(0, size - 1)
    if (to == index) return this
    return toMutableList().apply { add(to, removeAt(index)) }
}

// --- Connection ---

@Composable
private fun ConnectionSection(model: HomeModel) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var server by remember { mutableStateOf(ServerConfig.load(context).url) }
    var token by remember { mutableStateOf(ServerConfig.load(context).token) }
    var result by remember { mutableStateOf<String?>(null) }
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Heading("Connection", "The assistant server. Home Assistant is reached through it, so no HA token lives on the TV.")
        OutlinedTextField(server, { server = it }, Modifier.fillMaxWidth(), label = { Text("Server URL") }, singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri))
        OutlinedTextField(token, { token = it }, Modifier.fillMaxWidth(), label = { Text("API token") }, singleLine = true,
            visualTransformation = PasswordVisualTransformation())
        ChipRow {
            TvChip("Save & test") {
                ServerConfig.save(context, server, token)
                result = "Testing…"
                scope.launch {
                    result = runCatching { HaClient.states(context, listOf("sun.sun")) }
                        .fold({ "Connected · Home Assistant reachable" }, { "Failed: ${it.message}" })
                    model.startPolling()
                }
            }
        }
        result?.let { Text(it, style = TvType.body, color = MaterialTheme.colorScheme.onSurfaceVariant) }
    }
}

// --- Apps ---

private fun LazyListScope.appsSection(model: HomeModel, settings: TvSettings) {
    item { Heading("Apps", "The home row, in order. Everything installed is under All apps.") }
    item { TvSection("On home") }
    val favorites = settings.favorites
    items(favorites.size, key = { "fav_" + favorites[it] }) { i ->
        val pkg = favorites[i]
        val label = model.apps.firstOrNull { it.pkg == pkg }?.label ?: pkg
        FavoriteRow(label, pkg, i)
    }
    item { Spacer(Modifier.height(8.dp)); TvSection("Add to home") }
    val others = model.apps.filter { it.pkg !in favorites }
    items(others, key = { "add_" + it.pkg }) { app -> AddRow(app.label, app.pkg) { addFavorite(model, app.pkg) } }
}

private fun addFavorite(model: HomeModel, pkg: String) =
    TvConfig.update(model.context) { it.copy(favorites = it.favorites + pkg) }

@Composable
private fun FavoriteRow(label: String, pkg: String, index: Int) {
    val context = LocalContext.current
    SettingRow(label, pkg) {
        TvChip("↑") { TvConfig.update(context) { it.copy(favorites = it.favorites.moved(index, -1)) } }
        TvChip("↓") { TvConfig.update(context) { it.copy(favorites = it.favorites.moved(index, 1)) } }
        TvChip("Remove") { TvConfig.update(context) { s -> s.copy(favorites = s.favorites - pkg) } }
    }
}

@Composable
private fun AddRow(label: String, secondary: String, onAdd: () -> Unit) {
    SettingRow(label, secondary) { TvChip("Add", onClick = onAdd) }
}

// --- Home controls ---

private fun LazyListScope.homeSection(model: HomeModel, settings: TvSettings) {
    item { Heading("Home controls", "Tiles on the home screen. OK toggles; the AC opens temperature and mode.") }
    item { TvSection("On home") }
    val ids = settings.entities
    items(ids.size, key = { "ent_" + ids[it] }) { i -> EntityRow(model, ids[i], i) }
    item { Spacer(Modifier.height(8.dp)); EntityPicker(model) }
}

@Composable
private fun EntityRow(model: HomeModel, id: String, index: Int) {
    val context = LocalContext.current
    SettingRow(model.entities[id]?.name ?: id, id) {
        TvChip("↑") { TvConfig.update(context) { it.copy(entities = it.entities.moved(index, -1)) } }
        TvChip("↓") { TvConfig.update(context) { it.copy(entities = it.entities.moved(index, 1)) } }
        TvChip("Remove") { TvConfig.update(context) { s -> s.copy(entities = s.entities - id) } }
    }
}

@Composable
private fun EntityPicker(model: HomeModel) {
    val context = LocalContext.current
    val settings by TvConfig.settings.collectAsState()
    var domain by remember { mutableStateOf<String?>(null) }
    var found by remember { mutableStateOf<List<HaEntity>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(domain) {
        val d = domain ?: return@LaunchedEffect
        error = null
        runCatching { HaClient.list(context, listOf(d)) }
            .onSuccess { found = it }.onFailure { error = it.message; found = emptyList() }
    }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        TvSection("Add from Home Assistant")
        PICKER_DOMAINS.chunked(6).forEach { row ->
            ChipRow { row.forEach { (d, label) -> TvChip(label, selected = d == domain) { domain = d } } }
        }
        error?.let { Text("Couldn't load: $it", style = TvType.small, color = MaterialTheme.colorScheme.error) }
        found.filter { it.entityId !in settings.entities }.take(60).forEach { e ->
            SettingRow(e.name, "${e.entityId} · ${e.displayState()}") {
                TvChip("Add") {
                    TvConfig.update(context) { it.copy(entities = it.entities + e.entityId) }
                    model.startPolling()
                }
            }
        }
    }
}

// --- Cameras ---

private fun LazyListScope.camerasSection(settings: TvSettings) {
    item { Heading("Cameras", "RTSP streams for picture-in-picture (go2rtc: rtsp://<frigate host>:8554/<stream>) and the HA camera used for the home-screen snapshot.") }
    val cams = settings.cameras
    items(cams.size, key = { "cam_" + cams[it].id }) { i -> CameraEditor(i) }
    item {
        val context = LocalContext.current
        TvChip("Add camera") {
            TvConfig.update(context) { s ->
                s.copy(cameras = s.cameras + Camera("cam${System.currentTimeMillis() % 100000}", "Camera", "rtsp://", null))
            }
        }
    }
}

@Composable
private fun CameraEditor(index: Int) {
    val context = LocalContext.current
    val settings by TvConfig.settings.collectAsState()
    val cam = settings.cameras.getOrNull(index) ?: return
    fun edit(change: (Camera) -> Camera) = TvConfig.update(context) { s ->
        s.copy(cameras = s.cameras.mapIndexed { i, c -> if (i == index) change(c) else c })
    }
    Column(
        Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainerLow, RoundedCornerShape(14.dp)).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        TvMono("id ${cam.id}")
        OutlinedTextField(cam.name, { v -> edit { it.copy(name = v) } }, Modifier.fillMaxWidth(), label = { Text("Name") }, singleLine = true)
        OutlinedTextField(cam.url, { v -> edit { it.copy(url = v.trim()) } }, Modifier.fillMaxWidth(), label = { Text("RTSP URL") }, singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri))
        OutlinedTextField(cam.snapshotEntity.orEmpty(), { v -> edit { it.copy(snapshotEntity = v.trim().ifEmpty { null }) } },
            Modifier.fillMaxWidth(), label = { Text("Snapshot entity (camera.…)") }, singleLine = true)
        ChipRow {
            TvChip("Preview") { CameraPip.toggle(context, cam.id) }
            TvChip("Remove") { TvConfig.update(context) { s -> s.copy(cameras = s.cameras.filterIndexed { i, _ -> i != index }) } }
        }
    }
}

// --- Remote buttons ---

private fun describe(action: TvAction, settings: TvSettings, model: HomeModel): String = when (action) {
    is TvAction.TogglePip -> "Show / hide ${settings.cameras.firstOrNull { it.id == action.cameraId }?.name ?: action.cameraId} camera"
    is TvAction.OpenApp -> "Open ${model.apps.firstOrNull { it.pkg == action.pkg }?.label ?: action.pkg}"
    is TvAction.ToggleEntity -> "Toggle ${model.entities[action.entityId]?.name ?: action.entityId}"
    TvAction.OpenAssistant -> "Ask Dana"
    TvAction.GoHome -> "Dana TV home"
    TvAction.OpenSettings -> "Android TV settings"
}

@Composable
private fun ButtonsSection(model: HomeModel) {
    val context = LocalContext.current
    val settings by TvConfig.settings.collectAsState()
    var adding by remember { mutableStateOf(false) }
    val serviceOn = DanaKeyService.instance != null
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Heading("Remote buttons", "Bind any remote button: a short press, or a long press (held about half a second). A bound button loses its usual function.")
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.width(8.dp).height(8.dp).background(if (serviceOn) Success else MaterialTheme.colorScheme.error, RoundedCornerShape(4.dp)))
            Spacer(Modifier.width(8.dp))
            Text(if (serviceOn) "Button service running" else "Button service is off — turn on “Dana TV buttons” in Accessibility",
                Modifier.weight(1f), style = TvType.body, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (!serviceOn) TvChip("Accessibility settings") {
                runCatching { context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
            }
        }
        TvSection("Bindings")
        if (settings.bindings.isEmpty()) Text("No buttons bound", style = TvType.body, color = MaterialTheme.colorScheme.outline)
        settings.bindings.forEach { b ->
            SettingRow("${TvConfig.keyName(b.keyCode)} · ${b.press.label}", describe(b.action, settings, model)) {
                TvChip("Remove") { TvConfig.update(context) { s -> s.copy(bindings = s.bindings - b) } }
            }
        }
        ChipRow { TvChip("Add binding") { adding = true } }
    }
    if (adding) BindingDialog(model, settings) { adding = false }
}

@Composable
private fun BindingDialog(model: HomeModel, settings: TvSettings, onDone: () -> Unit) {
    val context = LocalContext.current
    val captured by KeyCapture.captured.collectAsState()
    var key by remember { mutableStateOf<Int?>(null) }
    var press by remember { mutableStateOf<Press?>(null) }
    val first = remember { FocusRequester() }

    LaunchedEffect(Unit) { KeyCapture.start() }
    LaunchedEffect(captured) { if (key == null && captured != null) key = captured }
    LaunchedEffect(key, press) { runCatching { first.requestFocus() } }
    val close = { KeyCapture.cancel(); onDone() }

    val actions = buildList {
        settings.cameras.forEach { add(TvAction.TogglePip(it.id)) }
        add(TvAction.OpenAssistant)
        add(TvAction.GoHome)
        add(TvAction.OpenSettings)
        settings.entities.forEach { add(TvAction.ToggleEntity(it)) }
        settings.favorites.forEach { add(TvAction.OpenApp(it)) }
    }

    Dialog(onDismissRequest = close) {
        Column(
            Modifier.width(560.dp).heightIn(max = 470.dp)
                .background(MaterialTheme.colorScheme.surfaceContainerHigh, RoundedCornerShape(18.dp)).padding(26.dp)
                // Without the key service the dialog itself sees the button.
                .onPreviewKeyEvent { ev ->
                    if (key == null && ev.type == KeyEventType.KeyUp && ev.key.nativeKeyCode != android.view.KeyEvent.KEYCODE_BACK) {
                        key = ev.key.nativeKeyCode; KeyCapture.cancel(); true
                    } else key == null && ev.key.nativeKeyCode != android.view.KeyEvent.KEYCODE_BACK
                },
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            TvMono("New binding")
            when {
                key == null -> {
                    Text("Press the remote button", style = TvType.title, color = MaterialTheme.colorScheme.onSurface)
                    Text("Any button except Back. Back cancels.", style = TvType.body, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                press == null -> {
                    Text(TvConfig.keyName(key!!), style = TvType.title, color = MaterialTheme.colorScheme.primary)
                    ChipRow {
                        Press.entries.forEachIndexed { i, p ->
                            TvChip(p.label, focusRequester = if (i == 0) first else null) { press = p }
                        }
                    }
                }
                else -> {
                    Text("${TvConfig.keyName(key!!)} · ${press!!.label}", style = TvType.title, color = MaterialTheme.colorScheme.onSurface)
                    LazyColumn(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        items(actions.size) { i ->
                            val a = actions[i]
                            FocusTile(Modifier.fillMaxWidth().height(40.dp), shape = RoundedCornerShape(8.dp),
                                focusRequester = if (i == 0) first else null, onClick = {
                                    val b = Binding(key!!, press!!, a)
                                    TvConfig.update(context) { s ->
                                        s.copy(bindings = s.bindings.filterNot { it.keyCode == b.keyCode && it.press == b.press } + b)
                                    }
                                    close()
                                }) { _ ->
                                Text(describe(a, settings, model), Modifier.align(Alignment.CenterStart).padding(horizontal = 12.dp),
                                    style = TvType.body, color = MaterialTheme.colorScheme.onSurface)
                            }
                        }
                    }
                }
            }
        }
    }
}

// --- Hey Dana ---

@Composable
private fun VoiceSection(onAskDana: () -> Unit) {
    val context = LocalContext.current
    val status by WakeBus.status.collectAsState()
    val score by WakeBus.score.collectAsState()
    val level by MicHub.level.collectAsState()
    var threshold by remember { mutableFloatStateOf(WakePrefs.threshold(context)) }
    var source by remember { mutableIntStateOf(WakePrefs.micSource(context)) }
    var aec by remember { mutableStateOf(WakePrefs.echoCancel(context)) }
    var speak by remember { mutableStateOf(WakePrefs.speakReplies(context)) }
    var followUp by remember { mutableStateOf(WakePrefs.followUp(context)) }
    fun save() = WakePrefs.saveVoice(context, threshold, speak, followUp, source, aec)

    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Heading("Hey Dana", status)
        TvSection("Live score (2 s peak) · fires at ≥ %.2f".format(threshold))
        Meter(score, marker = threshold, label = "%.2f".format(score))
        val gain = io.github.ronganam.dana.wake.GainStream.gainFor(level.noiseFloor)
        val speechAt = io.github.ronganam.dana.wake.GainStream.thresholdFor(level.noiseFloor) / gain
        TvMono("Mic level %.0f · room %.0f · voice gain %.1f× · speech above %.0f".format(level.rms, level.noiseFloor, gain, speechAt))
        Meter((level.rms / 3000.0).toFloat(), marker = null, label = null)
        SettingRow("Sensitivity", "Lower threshold = wakes more easily") {
            TvChip("More sensitive") { threshold = (threshold - 0.05f).coerceAtLeast(0.2f); save() }
            TvChip("Less") { threshold = (threshold + 0.05f).coerceAtMost(0.9f); save() }
        }
        TvSection("Microphone")
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            WakePrefs.MIC_SOURCES.forEach { (id, label) ->
                SettingRow(label) { TvChip(if (id == source) "Selected" else "Use", selected = id == source) { source = id; save() } }
            }
        }
        TvSection("Conversation")
        SettingRow("Speak replies") { TvChip(if (speak) "On" else "Off", selected = speak) { speak = !speak; save() } }
        SettingRow("Listen for a follow-up") { TvChip(if (followUp) "On" else "Off", selected = followUp) { followUp = !followUp; save() } }
        ChipRow { TvChip("Test: open Dana", onClick = onAskDana) }
    }
}

@Composable
private fun Meter(value: Float, marker: Float?, label: String?) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.weight(1f).height(10.dp).background(MaterialTheme.colorScheme.surfaceContainerHigh, RoundedCornerShape(5.dp))) {
            Box(Modifier.fillMaxWidth(value.coerceIn(0f, 1f)).fillMaxHeight()
                .background(if (marker != null && value >= marker) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.secondary, RoundedCornerShape(5.dp)))
            if (marker != null) Box(Modifier.fillMaxWidth(marker).fillMaxHeight()) {
                Box(Modifier.align(Alignment.CenterEnd).width(2.dp).fillMaxHeight().background(MaterialTheme.colorScheme.onSurface))
            }
        }
        label?.let { Text(it, Modifier.width(56.dp).padding(start = 10.dp), style = TvType.mono, color = MaterialTheme.colorScheme.onSurface) }
    }
}

// --- Picture-in-picture ---

@Composable
private fun PipSection() {
    val context = LocalContext.current
    val settings by TvConfig.settings.collectAsState()
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Heading("Picture-in-picture", "Where the camera window sits over other apps, and how big it is.")
        TvSection("Corner")
        ChipRow {
            Corner.entries.forEach { c ->
                TvChip(c.label, selected = settings.pipCorner == c) { TvConfig.update(context) { it.copy(pipCorner = c) } }
            }
        }
        TvSection("Size")
        ChipRow {
            listOf(0.25f to "Small", 0.32f to "Medium", 0.42f to "Large").forEach { (v, label) ->
                TvChip(label, selected = kotlin.math.abs(settings.pipScale - v) < 0.01f) { TvConfig.update(context) { it.copy(pipScale = v) } }
            }
        }
        TvSection("Preview")
        ChipRow { settings.cameras.forEach { cam -> TvChip(cam.name) { CameraPip.toggle(context, cam.id) } } }
    }
}

// --- System ---

@Composable
private fun SystemSection() {
    val context = LocalContext.current
    val settings by TvConfig.settings.collectAsState()
    val home = remember {
        context.packageManager.resolveActivity(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME), 0)
            ?.activityInfo?.packageName
    }
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Heading("System")
        SettingRow("Default home app", home ?: "unknown") {
            if (home != context.packageName) TvChip("Home settings") {
                runCatching { context.startActivity(Intent(Settings.ACTION_HOME_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
            }
        }
        SettingRow("Keep Dana TV as home", "Google TV's launcher outranks other home apps; the button service catches Home (hold: Android settings) and replaces Google TV's home screen") {
            TvChip(if (settings.homeOverride) "On" else "Off", selected = settings.homeOverride) {
                TvConfig.update(context) { it.copy(homeOverride = !it.homeOverride) }
            }
        }
        SettingRow("Android TV settings") { TvChip("Open") { openAndroidSettings(context) } }
        TvMono("io.github.ronganam.dana.tv · v${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})")
    }
}
