package io.github.ronganam.dana.tv

import android.content.Context
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import io.github.ronganam.dana.ui.theme.Success
import io.github.ronganam.dana.wake.WakeBus
import kotlinx.coroutines.delay
import org.json.JSONObject
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

val TILE_W = 160.dp
val TILE_H = 90.dp
private val SAFE_X = 48.dp
private val ROW_PAD = PaddingValues(horizontal = SAFE_X, vertical = 5.dp)

@Composable
fun HomeScreen(
    model: HomeModel,
    firstApp: FocusRequester,
    onAllApps: () -> Unit,
    onSettings: () -> Unit,
    onAskDana: () -> Unit,
) {
    val settings by TvConfig.settings.collectAsState()
    val apps = model.apps
    val favorites = remember(apps, settings.favorites) {
        settings.favorites.mapNotNull { pkg -> apps.firstOrNull { it.pkg == pkg } }
    }
    var climateFor by remember { mutableStateOf<String?>(null) }
    val appsRow = rememberLazyListState()

    // On every Home press, and once the favourites have loaded (focus lands before they exist).
    LaunchedEffect(model.homeRequests, favorites.firstOrNull()?.pkg) {
        if (model.homeRequests > 0) {
            appsRow.scrollToItem(0)
            runCatching { firstApp.requestFocus() }
        }
    }

    Column(
        Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).padding(top = 24.dp, bottom = 20.dp)
    ) {
        Header(onAskDana = onAskDana, onSettings = onSettings)
        Spacer(Modifier.height(12.dp))

        TvSection("Apps", Modifier.padding(horizontal = SAFE_X))
        LazyRow(state = appsRow, contentPadding = ROW_PAD, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            itemsIndexed(favorites, key = { _, a -> a.pkg }) { i, app ->
                AppTile(app, if (i == 0) firstApp else null) { TvApps.launch(model.context, app.pkg) }
            }
            item(key = "_all") {
                FocusTile(Modifier.size(TILE_W, TILE_H), focusRequester = if (favorites.isEmpty()) firstApp else null, onClick = onAllApps) { focused ->
                    Column(Modifier.align(Alignment.Center), horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("All apps", style = TvType.name, color = MaterialTheme.colorScheme.onSurface)
                        TvMono("${apps.size} installed", color = if (focused) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline)
                    }
                }
            }
        }
        Spacer(Modifier.height(10.dp))

        TvSection("Home", Modifier.padding(horizontal = SAFE_X), trailing = model.haError)
        LazyRow(contentPadding = ROW_PAD, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            items(settings.entities, key = { it }) { id ->
                val entity = model.entities[id]
                EntityTile(id, entity) {
                    if (entity?.domain == "climate") climateFor = id else model.runPrimary(id)
                }
            }
            if (settings.entities.isEmpty()) item {
                Text("Add Home Assistant controls in Settings", Modifier.padding(vertical = 30.dp),
                    style = TvType.body, color = MaterialTheme.colorScheme.outline)
            }
        }
        Spacer(Modifier.height(10.dp))

        TvSection("Cameras", Modifier.padding(horizontal = SAFE_X), trailing = pipHint(settings))
        val showing by CameraPip.showing.collectAsState()
        LazyRow(contentPadding = ROW_PAD, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            items(settings.cameras, key = { it.id }) { cam ->
                CameraTile(model, cam, live = showing == cam.id) { CameraPip.toggle(model.context, cam.id) }
            }
        }
    }

    climateFor?.let { id ->
        model.entities[id]?.let { ClimateDialog(model, it) { climateFor = null } } ?: run { climateFor = null }
    }
}

@Composable
private fun Header(onAskDana: () -> Unit, onSettings: () -> Unit) {
    val now by produceState(LocalDateTime.now()) {
        while (true) {
            value = LocalDateTime.now()
            delay((60 - value.second).coerceAtLeast(1) * 1000L)
        }
    }
    val listening by WakeBus.listening.collectAsState()
    Row(Modifier.fillMaxWidth().padding(horizontal = SAFE_X), verticalAlignment = Alignment.Bottom) {
        Text(now.format(DateTimeFormatter.ofPattern("HH:mm")), style = TvType.clock, color = MaterialTheme.colorScheme.onSurface)
        Column(Modifier.padding(start = 16.dp, bottom = 8.dp)) {
            TvMono(now.format(DateTimeFormatter.ofPattern("EEEE", Locale.ENGLISH)))
            Text(now.format(DateTimeFormatter.ofPattern("d MMMM", Locale.ENGLISH)), style = TvType.body,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Spacer(Modifier.weight(1f))
        Row(Modifier.padding(bottom = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(8.dp).background(if (listening) Success else MaterialTheme.colorScheme.outline, CircleShape))
            Spacer(Modifier.width(8.dp))
            TvMono(if (listening) "Say “Hey Dana”" else "Wake word off")
            Spacer(Modifier.width(18.dp))
            ChipRow {
                TvChip("Ask Dana", onClick = onAskDana)
                TvChip("Settings", onClick = onSettings)
            }
        }
    }
}

@Composable
fun AppTile(app: TvApp, focusRequester: FocusRequester?, modifier: Modifier = Modifier, marked: Boolean = false, onClick: () -> Unit) {
    val context = LocalContext.current
    val banner by produceState<ImageBitmap?>(null, app.pkg) { value = TvApps.banner(context, app) }
    val hasBanner = remember(app.pkg) { TvApps.hasBanner(context, app) }
    FocusTile(modifier.size(TILE_W, TILE_H), focusRequester = focusRequester, onClick = onClick) { _ ->
        banner?.let { Image(it, app.label, Modifier.fillMaxSize(), contentScale = ContentScale.Crop) }
        if (!hasBanner) {
            Text(app.label, Modifier.align(Alignment.BottomCenter).padding(bottom = 8.dp, start = 6.dp, end = 6.dp),
                style = TvType.small, color = MaterialTheme.colorScheme.onSurface, maxLines = 1,
                overflow = TextOverflow.Ellipsis, textAlign = TextAlign.Center)
        }
        if (marked) Box(Modifier.align(Alignment.TopEnd).padding(8.dp).size(8.dp)
            .background(MaterialTheme.colorScheme.primary, CircleShape))
    }
}

@Composable
private fun EntityTile(id: String, entity: HaEntity?, onClick: () -> Unit) {
    val active = entity?.active == true
    FocusTile(Modifier.size(TILE_W, TILE_H), onClick = onClick) { focused ->
        Column(Modifier.fillMaxSize().padding(horizontal = 12.dp, vertical = 9.dp)) {
            Text(entity?.name ?: id, style = TvType.name, maxLines = 1, overflow = TextOverflow.Ellipsis,
                color = if (focused) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.weight(1f))
            Row(verticalAlignment = Alignment.Bottom) {
                Text(entity?.displayState() ?: "…", style = TvType.value, maxLines = 1,
                    color = if (active) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface)
                entity?.detail()?.let {
                    Spacer(Modifier.width(8.dp))
                    Text(it, Modifier.padding(bottom = 5.dp).weight(1f, fill = false), style = TvType.small,
                        color = MaterialTheme.colorScheme.outline, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
}

@Composable
private fun CameraTile(model: HomeModel, cam: Camera, live: Boolean, onClick: () -> Unit) {
    var focused by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val shot by produceState<ImageBitmap?>(null, cam.snapshotEntity, model.resumed, focused) {
        val entity = cam.snapshotEntity ?: return@produceState
        while (model.resumed) {
            runCatching { HaClient.snapshot(context, entity, 400) }.getOrNull()?.let { value = it.asImageBitmap() }
            delay(if (focused) 3_000 else 20_000)
        }
    }
    FocusTile(Modifier.size(TILE_W, TILE_H), onFocus = { focused = it }, onClick = onClick) { _ ->
        shot?.let { Image(it, cam.name, Modifier.fillMaxSize(), contentScale = ContentScale.Crop) }
        Row(
            Modifier.align(Alignment.BottomStart).padding(8.dp)
                .background(Color(0xB313100E), RoundedCornerShape(6.dp)).padding(horizontal = 8.dp, vertical = 3.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            if (live) {
                Box(Modifier.size(6.dp).background(MaterialTheme.colorScheme.primary, CircleShape))
                Spacer(Modifier.width(6.dp))
            }
            TvMono(cam.name, color = MaterialTheme.colorScheme.onSurface)
        }
    }
}

@Composable
private fun ClimateDialog(model: HomeModel, entity: HaEntity, onDismiss: () -> Unit) {
    val first = remember { FocusRequester() }
    val step = entity.attrs.optDouble("target_temp_step").takeIf { !it.isNaN() && it > 0 } ?: 1.0
    val min = entity.attrs.optDouble("min_temp", 16.0)
    val max = entity.attrs.optDouble("max_temp", 30.0)
    var target by remember(entity.entityId) { mutableStateOf(entity.attrs.optDouble("temperature").takeIf { !it.isNaN() } ?: 24.0) }
    var mode by remember(entity.entityId) { mutableStateOf(entity.state) }
    val modes = entity.attrs.optJSONArray("hvac_modes")?.let { a -> List(a.length()) { a.getString(it) } }
        ?: listOf("off", "cool", "heat")

    // Send the temperature once the remote has been still for a moment.
    var pendingTemp by remember { mutableStateOf<Double?>(null) }
    LaunchedEffect(pendingTemp) {
        val t = pendingTemp ?: return@LaunchedEffect
        delay(700)
        model.call(entity.entityId, "climate", "set_temperature", JSONObject().put("temperature", t))
        pendingTemp = null
    }
    LaunchedEffect(Unit) { runCatching { first.requestFocus() } }

    Dialog(onDismissRequest = onDismiss) {
        Column(
            Modifier.width(460.dp).background(MaterialTheme.colorScheme.surfaceContainerHigh, RoundedCornerShape(18.dp))
                .padding(28.dp)
        ) {
            TvMono(entity.detail() ?: "Climate")
            Text(entity.name, style = TvType.title, color = MaterialTheme.colorScheme.onSurface)
            Spacer(Modifier.height(18.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                TvChip("−", focusRequester = first) {
                    target = (target - step).coerceAtLeast(min); pendingTemp = target
                }
                Text(HaEntity.formatTemp(target), Modifier.width(120.dp), style = TvType.clock, textAlign = TextAlign.Center,
                    color = if (mode == "off") MaterialTheme.colorScheme.outline else MaterialTheme.colorScheme.primary)
                TvChip("+") { target = (target + step).coerceAtMost(max); pendingTemp = target }
            }
            Spacer(Modifier.height(18.dp))
            TvMono("Mode")
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                modes.take(6).forEach { m ->
                    TvChip(HaEntity.pretty(m), selected = m == mode) {
                        mode = m
                        model.call(entity.entityId, "climate", "set_hvac_mode", JSONObject().put("hvac_mode", m))
                    }
                }
            }
            Spacer(Modifier.height(20.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) { TvChip("Done", onClick = onDismiss) }
        }
    }
}

@Composable
fun AllAppsScreen(model: HomeModel, onBack: () -> Unit) {
    val settings by TvConfig.settings.collectAsState()
    val first = remember { FocusRequester() }
    LaunchedEffect(model.apps.isNotEmpty()) { if (model.apps.isNotEmpty()) runCatching { first.requestFocus() } }
    Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).padding(top = 27.dp)) {
        Row(Modifier.padding(horizontal = SAFE_X), verticalAlignment = Alignment.Bottom) {
            Text("All apps", style = TvType.title, color = MaterialTheme.colorScheme.onSurface)
            Spacer(Modifier.width(14.dp))
            TvMono("● on home · manage in Settings", Modifier.padding(bottom = 6.dp))
        }
        Spacer(Modifier.height(10.dp))
        LazyVerticalGrid(
            columns = GridCells.Adaptive(TILE_W + 14.dp),
            contentPadding = PaddingValues(horizontal = SAFE_X, vertical = 10.dp),
            horizontalArrangement = Arrangement.spacedBy(14.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            items(model.apps, key = { it.pkg }) { app ->
                val i = model.apps.indexOf(app)
                AppTile(app, if (i == 0) first else null, marked = app.pkg in settings.favorites) {
                    TvApps.launch(model.context, app.pkg)
                    onBack()
                }
            }
        }
    }
}

/** Opens [pkg]'s TV settings page (used from Settings for "Android settings"). */
fun openAndroidSettings(context: Context) {
    runCatching {
        context.startActivity(android.content.Intent(android.provider.Settings.ACTION_SETTINGS)
            .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}

/** "Info: PTZ · hold Info: Front door" — the remote buttons bound to cameras. */
private fun pipHint(settings: TvSettings): String? = settings.bindings
    .filter { it.action is TvAction.TogglePip }
    .joinToString(" · ") { b ->
        val cam = settings.cameras.firstOrNull { it.id == (b.action as TvAction.TogglePip).cameraId }?.name ?: "camera"
        (if (b.press == Press.LONG) "hold " else "") + TvConfig.keyName(b.keyCode) + ": " + cam
    }.ifEmpty { null }
