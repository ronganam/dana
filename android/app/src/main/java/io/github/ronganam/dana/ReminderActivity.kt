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
import android.view.HapticFeedbackConstants
import android.view.WindowManager
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.outlined.DirectionsCar
import androidx.compose.material.icons.outlined.Place
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.outlined.WbSunny
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.LayoutCoordinates
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import io.github.ronganam.dana.data.network.ApiService
import io.github.ronganam.dana.data.sync.SubmitResult
import io.github.ronganam.dana.data.sync.TaskActionQueue
import io.github.ronganam.dana.data.wear.WearReminderBridge
import io.github.ronganam.dana.ui.components.InkChip
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.components.StatusPill
import io.github.ronganam.dana.ui.theme.DanaTheme
import io.github.ronganam.dana.ui.theme.ThemeSettings
import kotlinx.coroutines.launch
import org.json.JSONObject
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.sqrt

sealed class MenuNode {
    abstract val label: String

    data class Parent(
        override val label: String,
        val children: List<MenuNode>
    ) : MenuNode()

    data class Leaf(
        override val label: String,
        val snoozeType: String,
        val minutes: Int = 0,
        val location: String = "",
        val event: String = "",
        val action: String = ""
    ) : MenuNode()
}

data class VisualNode(
    val node: MenuNode,
    val x: Float,
    val y: Float,
    val radiusPx: Float,
    val isHighlighted: Boolean,
    val label: String,
    val parentX: Float = 0f,
    val parentY: Float = 0f
)

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
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            keyguardManager.requestDismissKeyguard(this, null)
        }

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
                        val message = when (TaskActionQueue.complete(this@ReminderActivity, taskId)) {
                            SubmitResult.SENT -> "Task completed!"
                            SubmitResult.QUEUED -> "Offline. The task will be completed when the server is reachable."
                            SubmitResult.REJECTED -> "The server couldn't complete this task"
                        }
                        Toast.makeText(this@ReminderActivity, message, Toast.LENGTH_LONG).show()
                        finish()
                    }
                },
                onSnoozeSelected = { leaf ->
                    stopAlerts()
                    onHandled()
                    lifecycleScope.launch {
                        val payload = JSONObject().apply {
                            put("type", leaf.snoozeType)
                            if (leaf.snoozeType == "time") {
                                put("minutes", leaf.minutes)
                            } else if (leaf.snoozeType == "location") {
                                put("location", leaf.location)
                                put("event", leaf.event)
                            } else if (leaf.snoozeType == "car") {
                                put("action", leaf.action)
                            } else if (leaf.snoozeType == "wake") {
                                // wake trigger has no sub fields
                            }
                        }
                        Toast.makeText(this@ReminderActivity, "Snoozing · ${snoozeSentence(leaf)}",Toast.LENGTH_SHORT).show()
                        val message = when (TaskActionQueue.snooze(this@ReminderActivity, taskId, payload)) {
                            SubmitResult.SENT -> "Task snoozed successfully"
                            SubmitResult.QUEUED -> "Offline. The snooze will be sent when the server is reachable."
                            SubmitResult.REJECTED -> "The server couldn't snooze this task"
                        }
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

            vibrator = getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
            val pattern = longArrayOf(0, 1000, 500, 1000)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                vibrator?.vibrate(VibrationEffect.createWaveform(pattern, 0))
            } else {
                @Suppress("DEPRECATION")
                vibrator?.vibrate(pattern, 0)
            }
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
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                vibrator?.vibrate(VibrationEffect.createWaveform(pattern, 0))
            } else {
                @Suppress("DEPRECATION")
                vibrator?.vibrate(pattern, 0)
            }
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

/**
 * The alarm screen, in the app's Ink & Paper language: mono kicker, serif
 * title, one vermilion accent, hairlines. Snooze either with one tap (the
 * quick row) or by holding the dial and dragging out to a time, place, the
 * car or waking up.
 *
 * Sized from the window it is in (Fold cover ≈ 555×876 dp, inner ≈ 821×1088
 * portrait / 1088×821 landscape): the title shrinks on short screens, the top
 * section scrolls instead of pushing the actions off, and wide landscape
 * windows split into two panes. The dial sits low, near the thumb, and its
 * options fan out *upward* so the hand never covers them; while dragging, a
 * readout at the top spells out what releasing will do.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ReminderScreen(
    taskTitle: String,
    priority: String,
    triggerType: String,
    onComplete: () -> Unit,
    onSnoozeSelected: (MenuNode.Leaf) -> Unit,
    onDismiss: () -> Unit,
    onInteractionStateChanged: (Boolean) -> Unit
) {
    val context = LocalContext.current
    val apiService = remember {
        val prefs = context.getSharedPreferences("DanaPrefs", Context.MODE_PRIVATE)
        val serverUrl = prefs.getString("server_url", "https://") ?: "https://"
        val apiToken = prefs.getString("api_token", "") ?: ""
        ApiService(serverUrl, apiToken)
    }

    var zones by remember { mutableStateOf<List<String>>(emptyList()) }

    // Fetch dynamic location zones
    LaunchedEffect(Unit) {
        zones = try {
            apiService.getZones().map { it.name }
        } catch (e: Exception) {
            Log.e("ReminderActivity", "Failed to fetch zones dynamically, using fallbacks", e)
            listOf("Home", "Work", "Parent's home")
        }
    }

    val menuTree = remember(zones) { buildSnoozeTree(zones) }

    val now = remember { java.time.LocalTime.now() }
    val (emoji, cleanTitle) = remember(taskTitle) { extractLeadingEmoji(taskTitle) }
    val pColor = when (priority.lowercase()) {
        "high" -> io.github.ronganam.dana.ui.theme.Danger
        "medium" -> io.github.ronganam.dana.ui.theme.Warning
        else -> io.github.ronganam.dana.ui.theme.Info
    }

    // Dial state shared between the dock (gesture) and the overlay (drawing).
    var activePath by remember { mutableStateOf<List<MenuNode>>(emptyList()) }
    val dragging = activePath.isNotEmpty()
    var rootCoords by remember { mutableStateOf<LayoutCoordinates?>(null) }
    var dialCenter by remember { mutableStateOf(Offset.Unspecified) }
    val fade by animateFloatAsState(if (dragging) 0.06f else 1f, tween(160), label = "fade")

    val density = LocalDensity.current
    val safeTopPx = WindowInsets.safeContent.getTop(density).toFloat()

    BoxWithConstraints(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
            .onGloballyPositioned { rootCoords = it }
    ) {
        val wide = maxWidth >= 720.dp && maxWidth > maxHeight
        val compact = maxHeight < 780.dp
        val widthPx = constraints.maxWidth.toFloat()
        val geometry = remember(dialCenter, widthPx, safeTopPx) {
            if (dialCenter == Offset.Unspecified) null
            else SnoozeDialGeometry.fit(
                origin = dialCenter,
                width = widthPx,
                topLimit = safeTopPx + with(density) { READOUT_HEIGHT.toPx() },
                density = density.density
            )
        }

        val header: @Composable () -> Unit = {
            ReminderHeader(
                kicker = "Reminder · ${"%02d:%02d".format(now.hour, now.minute)} · ${triggerLabel(triggerType)}",
                priority = priority,
                priorityColor = pColor,
                emoji = emoji,
                title = cleanTitle,
                compact = compact
            )
        }
        val quickSnoozes: @Composable () -> Unit = {
            Spacer(Modifier.height(if (compact) 10.dp else 14.dp))
            MonoLabel("Snooze")
            Spacer(Modifier.height(8.dp))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                QUICK_SNOOZES.forEach { leaf ->
                    InkChip(leaf.label, onClick = { onSnoozeSelected(leaf) }, modifier = Modifier.heightIn(min = 40.dp))
                }
                InkChip(WAKE_LEAF.label, onClick = { onSnoozeSelected(WAKE_LEAF) }, modifier = Modifier.heightIn(min = 40.dp))
            }
        }
        val dock: @Composable (Modifier) -> Unit = { mod ->
            SnoozeDialDock(
                tree = menuTree,
                geometry = geometry,
                activePath = activePath,
                compact = compact,
                onPathChange = { activePath = it },
                onPositioned = { coords ->
                    val root = rootCoords
                    if (root != null && root.isAttached && coords.isAttached) {
                        val c = root.localPositionOf(coords, Offset(coords.size.width / 2f, coords.size.height / 2f))
                        if (c != dialCenter) dialCenter = c
                    }
                },
                onSelected = onSnoozeSelected,
                onInteractionStateChanged = onInteractionStateChanged,
                modifier = mod
            )
        }
        val actions: @Composable () -> Unit = {
            Rule()
            Spacer(Modifier.height(if (compact) 12.dp else 16.dp))
            Button(
                onClick = onComplete,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(if (compact) 52.dp else 56.dp)
            ) {
                Icon(Icons.Filled.Check, contentDescription = null, modifier = Modifier.size(20.dp))
                Spacer(Modifier.width(8.dp))
                Text("Mark complete", style = MaterialTheme.typography.titleMedium)
            }
            Spacer(Modifier.height(4.dp))
            TextButton(onClick = onDismiss, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) {
                Text("Dismiss", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }

        if (wide) {
            // Inner screen in landscape: words and actions left, the dial right.
            Row(
                Modifier
                    .fillMaxSize()
                    .wrapContentWidth()
                    .widthIn(max = 1100.dp)
                    .fillMaxWidth()
                    .safeContentPadding()
                    .padding(horizontal = 32.dp, vertical = 20.dp),
                horizontalArrangement = Arrangement.spacedBy(40.dp)
            ) {
                Column(Modifier.weight(1f).fillMaxHeight().alpha(fade)) {
                    Column(Modifier.weight(1f).verticalScroll(rememberScrollState())) {
                        header()
                        Spacer(Modifier.height(20.dp))
                        Rule()
                        quickSnoozes()
                    }
                    actions()
                }
                // The dial in the middle of its pane (the fan opens up and to the sides).
                Box(Modifier.weight(1f).fillMaxHeight(), contentAlignment = Alignment.Center) {
                    dock(Modifier.padding(top = 120.dp))
                }
            }
        } else {
            Column(
                Modifier
                    .align(Alignment.TopCenter)
                    .fillMaxHeight()
                    .widthIn(max = 600.dp)
                    .fillMaxWidth()
                    .safeContentPadding()
                    .padding(horizontal = 24.dp, vertical = if (compact) 12.dp else 16.dp)
            ) {
                // Everything above the dial scrolls if a long title needs it, so
                // the dial and the actions always stay on screen.
                Column(
                    Modifier
                        .weight(1f)
                        .alpha(fade)
                        .verticalScroll(rememberScrollState())
                ) {
                    header()
                    Spacer(Modifier.height(if (compact) 14.dp else 20.dp))
                    Rule()
                    quickSnoozes()
                }
                dock(Modifier.fillMaxWidth().padding(vertical = if (compact) 8.dp else 16.dp))
                Column(Modifier.alpha(fade)) { actions() }
            }
        }

        // While dragging: the fan over the whole window, and the readout on top.
        if (dragging && geometry != null) {
            SnoozeFanOverlay(tree = menuTree, geometry = geometry, activePath = activePath, origin = dialCenter)
            SnoozeReadout(
                path = activePath,
                modifier = Modifier
                    // Two-pane: over the dial's pane; otherwise centred.
                    .align(if (wide) Alignment.TopEnd else Alignment.TopCenter)
                    .then(if (wide) Modifier.fillMaxWidth(0.5f) else Modifier)
                    .padding(top = with(density) { safeTopPx.toDp() } + 16.dp)
                    .padding(horizontal = 24.dp)
                    .widthIn(max = 560.dp)
                    .fillMaxWidth()
            )
        }
    }
}

@Composable
private fun ReminderHeader(
    kicker: String,
    priority: String,
    priorityColor: Color,
    emoji: String?,
    title: String,
    compact: Boolean
) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        MonoLabel(kicker, color = MaterialTheme.colorScheme.primary, modifier = Modifier.weight(1f))
        StatusPill(priority.replaceFirstChar { it.uppercase() }, priorityColor)
    }
    Spacer(Modifier.height(if (compact) 10.dp else 14.dp))
    Row(verticalAlignment = Alignment.Top) {
        if (emoji != null) {
            Text(
                emoji,
                fontSize = if (compact) 30.sp else 40.sp,
                modifier = Modifier.padding(end = 12.dp, top = 2.dp)
            )
        }
        Text(
            text = title,
            style = (if (compact) MaterialTheme.typography.headlineLarge else MaterialTheme.typography.displaySmall)
                .copy(textDirection = TextDirection.Content),
            color = MaterialTheme.colorScheme.onBackground,
            maxLines = if (compact) 3 else 4,
            overflow = TextOverflow.Ellipsis
        )
    }
}

/** Time snoozes, shared by the quick row and the dial. */
private val QUICK_SNOOZES = listOf(
    MenuNode.Leaf("15 min", "time", minutes = 15),
    MenuNode.Leaf("1 hour", "time", minutes = 60),
    MenuNode.Leaf("3 hours", "time", minutes = 180),
    MenuNode.Leaf("1 day", "time", minutes = 1440)
)

private val WAKE_LEAF = MenuNode.Leaf("When I wake up", "wake")

/** Room kept clear at the top for the readout while dragging. */
private val READOUT_HEIGHT = 128.dp

/** The dial's menu: time, place (each zone → arriving / leaving), car, waking up. */
fun buildSnoozeTree(zones: List<String>): MenuNode.Parent = MenuNode.Parent(
    "Root", listOf(
        MenuNode.Parent("Time based", QUICK_SNOOZES),
        MenuNode.Parent("Location based", zones.map { zone ->
            MenuNode.Parent(zone, listOf(
                MenuNode.Leaf("Arriving", "location", location = zone, event = "arrive"),
                MenuNode.Leaf("Leaving", "location", location = zone, event = "leave")
            ))
        }),
        MenuNode.Parent("Car based", listOf(
            MenuNode.Leaf("Entering", "car", action = "enter"),
            MenuNode.Leaf("Exiting", "car", action = "leave")
        )),
        WAKE_LEAF
    )
)

private fun triggerLabel(type: String) = when (type.lowercase()) {
    "time" -> "On time"
    "location" -> "Location"
    "car" -> "Car"
    "wake" -> "Wake up"
    else -> type.replace("_", " ")
}

private fun level1Icon(label: String) = when (label) {
    "Time based" -> Icons.Outlined.Schedule
    "Location based" -> Icons.Outlined.Place
    "Car based" -> Icons.Outlined.DirectionsCar
    "When I wake up" -> Icons.Outlined.WbSunny
    else -> null
}

private fun shortLabel(node: MenuNode) = when (node.label) {
    "Time based" -> "Time"
    "Location based" -> "Place"
    "Car based" -> "Car"
    "When I wake up" -> "Wake up"
    else -> node.label
}

/** What releasing on [leaf] does, as a sentence ("When I arrive at Home"). */
fun snoozeSentence(leaf: MenuNode.Leaf): String = when (leaf.snoozeType) {
    "time" -> "In ${leaf.label}"
    "location" -> if (leaf.event == "leave") "When I leave ${leaf.location}" else "When I arrive at ${leaf.location}"
    "car" -> if (leaf.action == "leave") "When I get out of the car" else "When I get in the car"
    "wake" -> "When I wake up"
    else -> leaf.label
}

/** Readout headline + hint for the current drag path. */
fun snoozeReadout(path: List<MenuNode>): Pair<String, String> {
    val last = path.lastOrNull()
    return when {
        path.size <= 1 -> "Drag to an option" to "Let go here to cancel"
        last is MenuNode.Leaf -> snoozeSentence(last) to "Let go to snooze"
        path[1].label == "Time based" -> "Snooze for…" to "Keep going out"
        path[1].label == "Location based" && path.size == 2 -> "When I get to a place…" to "Keep going out"
        path[1].label == "Location based" -> "${last?.label}: arrive or leave?" to "Keep going out"
        path[1].label == "Car based" -> "When the car…" to "Keep going out"
        else -> (last?.label ?: "") to "Keep going out"
    }
}

/**
 * Where the dial's rings sit, in px relative to the dial centre. Angles are in
 * screen degrees (0° = right, −90° = straight up). Everything fans across the
 * upper half so the hand holding the dial never covers an option; rings scale
 * to the room around the dial (≈ 245 dp reach on a Fold cover).
 *
 * The same geometry drives drawing and hit-testing, so what you see is what
 * you select.
 */
class SnoozeDialGeometry(
    val r1: Float,
    val r2: Float,
    val r3: Float,
    density: Float,
    /** Vertical stretch: rings are ovals k× taller than wide, using the room above the dial. */
    val k: Float = 1f
) {
    /** A finger offset mapped into the round space the rings are defined in. */
    fun normalize(offset: Offset) = Offset(offset.x, offset.y / k)

    /** Leave the centre this far to pick a category… */
    val t1 = r1 * 0.45f
    /** …this far to step into the category's options… */
    val t2 = r1 + (r2 - r1) * 0.3f
    /** …and this far for a place's arrive / leave. */
    val t3 = r2 + (r3 - r2) * 0.3f

    val node1 = 32f * density
    val node2 = 30f * density
    val node3 = 30f * density

    /** One ring of options: [count] slots of [step]° starting at [start]°. */
    data class Ring(val start: Float, val step: Float, val count: Int, val radius: Float, val k: Float = 1f) {
        fun angleOf(i: Int) = start + step * (i + 0.5f)
        fun indexAt(angle: Float) = floor((angle - start) / step).toInt().coerceIn(0, count - 1)
        /** Where slot [i] is drawn, px from the dial centre (on the oval). */
        fun position(i: Int): Offset {
            val rad = Math.toRadians(angleOf(i).toDouble())
            return Offset((radius * cos(rad)).toFloat(), (k * radius * sin(rad)).toFloat())
        }
    }

    /** Categories: evenly across the upper half. */
    fun ring1(count: Int) = Ring(-180f, 180f / count.coerceAtLeast(1), count.coerceAtLeast(1), r1, k)

    /** Options of the category at [parentAngle]. */
    fun ring2(parentAngle: Float, count: Int) = fan(parentAngle, count, r2, node2)

    /** Arrive / leave of the place at [parentAngle]. */
    fun ring3(parentAngle: Float, count: Int) = fan(parentAngle, count, r3, node3)

    /** A fan centred on [center], wide enough that nodes don't touch, kept in the upper half. */
    private fun fan(center: Float, count: Int, radius: Float, nodeRadius: Float): Ring {
        val n = count.coerceAtLeast(1)
        val minStep = Math.toDegrees((nodeRadius * 2.3f / radius).toDouble()).toFloat()
        val step = maxOf(22f, minStep).coerceAtMost(MAX_FAN / n)
        val span = step * n
        var start = center - span / 2f
        if (start < FAN_MIN) start = FAN_MIN
        if (start + span > FAN_MAX) start = FAN_MAX - span
        return Ring(start, step, n, radius, k)
    }

    companion object {
        /** Fans may dip a little below horizontal at the sides, never further. */
        const val FAN_MIN = -195f
        const val FAN_MAX = 15f
        const val MAX_FAN = FAN_MAX - FAN_MIN

        /**
         * Fit the rings to the space around [origin] (dial centre, window px):
         * as far out as the window's sides allow (190–270 dp), then stretched
         * upward (up to 1.4×) into the room below the readout. The inner ring
         * always clears the dial.
         */
        fun fit(origin: Offset, width: Float, topLimit: Float, density: Float): SnoozeDialGeometry {
            val margin = 6f * density
            val nodeR = 32f * density
            val up = origin.y - topLimit - nodeR
            val side = minOf(origin.x - margin, width - origin.x - margin) - nodeR
            val reach = minOf(side, up).coerceIn(190f * density, 270f * density)
            val k = (up / reach).coerceIn(1f, 1.4f)
            val r1 = maxOf(reach * 0.42f, 100f * density)
            val r2 = maxOf(reach * 0.71f, r1 + 66f * density)
            val r3 = maxOf(reach, r2 + 64f * density)
            return SnoozeDialGeometry(r1, r2, r3, density, k)
        }

        /** atan2 angle folded into (−270°, 90°] so left-side fans stay continuous. */
        fun screenAngle(offset: Offset): Float {
            var a = Math.toDegrees(atan2(offset.y.toDouble(), offset.x.toDouble())).toFloat()
            if (a > 90f) a -= 360f
            return a
        }
    }
}

/**
 * The menu path for a finger at [offset] (px from the dial centre). Deeper
 * levels lock their parent, so sweeping sideways along an outer ring changes
 * only that ring's choice.
 */
fun selectSnoozePath(
    offset: Offset,
    root: MenuNode.Parent,
    geometry: SnoozeDialGeometry,
    previous: List<MenuNode>
): List<MenuNode> {
    val round = geometry.normalize(offset)
    val distance = round.getDistance()
    val angle = SnoozeDialGeometry.screenAngle(round)
    val path = mutableListOf<MenuNode>(root)
    if (distance < geometry.t1 || root.children.isEmpty()) return path

    val l1Nodes = root.children
    val ring1 = geometry.ring1(l1Nodes.size)
    val prevL1 = previous.getOrNull(1)
    val i1 = if (distance >= geometry.t2 && prevL1 != null && prevL1 in l1Nodes) l1Nodes.indexOf(prevL1)
             else ring1.indexAt(angle)
    val l1 = l1Nodes[i1]
    path.add(l1)
    if (distance < geometry.t2 || l1 !is MenuNode.Parent || l1.children.isEmpty()) return path

    val ring2 = geometry.ring2(ring1.angleOf(i1), l1.children.size)
    val prevL2 = previous.getOrNull(2)
    val i2 = if (distance >= geometry.t3 && prevL1 == l1 && prevL2 != null && prevL2 in l1.children) l1.children.indexOf(prevL2)
             else ring2.indexAt(angle)
    val l2 = l1.children[i2]
    path.add(l2)
    if (distance < geometry.t3 || l2 !is MenuNode.Parent || l2.children.isEmpty()) return path

    val ring3 = geometry.ring3(ring2.angleOf(i2), l2.children.size)
    path.add(l2.children[ring3.indexAt(angle)])
    return path
}

/** Nodes to draw for [path]: px from the dial centre, plus the parent they hang from. */
fun layoutSnoozeFan(root: MenuNode.Parent, geometry: SnoozeDialGeometry, path: List<MenuNode>): List<VisualNode> {
    val out = mutableListOf<VisualNode>()
    val l1Nodes = root.children
    if (l1Nodes.isEmpty()) return out
    val ring1 = geometry.ring1(l1Nodes.size)
    val l1 = path.getOrNull(1)
    l1Nodes.forEachIndexed { i, node ->
        // Once a category is open, only it stays on the inner ring.
        if (path.size <= 2 || node == l1) {
            val p = ring1.position(i)
            out.add(VisualNode(node, p.x, p.y, geometry.node1, node == l1, node.label))
        }
    }
    if (l1 !is MenuNode.Parent || l1.children.isEmpty()) return out
    val i1 = l1Nodes.indexOf(l1)
    val p1 = ring1.position(i1)
    val ring2 = geometry.ring2(ring1.angleOf(i1), l1.children.size)
    val l2 = path.getOrNull(2)
    l1.children.forEachIndexed { i, node ->
        if (path.size <= 3 || node == l2) {
            val p = ring2.position(i)
            out.add(VisualNode(node, p.x, p.y, geometry.node2, node == l2, node.label, p1.x, p1.y))
        }
    }
    if (l2 !is MenuNode.Parent || l2.children.isEmpty()) return out
    val i2 = l1.children.indexOf(l2)
    val p2 = ring2.position(i2)
    val ring3 = geometry.ring3(ring2.angleOf(i2), l2.children.size)
    val l3 = path.getOrNull(3)
    l2.children.forEachIndexed { i, node ->
        val p = ring3.position(i)
        out.add(VisualNode(node, p.x, p.y, geometry.node3, node == l3, node.label, p2.x, p2.y))
    }
    return out
}

/**
 * The dial at rest: a hairline arc with four ticks above it (where the
 * categories will open), the "Hold" button, and a one-line hint. Owns the
 * gesture; the fan itself is drawn by [SnoozeFanOverlay] over the whole window.
 */
@Composable
private fun SnoozeDialDock(
    tree: MenuNode.Parent,
    geometry: SnoozeDialGeometry?,
    activePath: List<MenuNode>,
    compact: Boolean,
    onPathChange: (List<MenuNode>) -> Unit,
    onPositioned: (LayoutCoordinates) -> Unit,
    onSelected: (MenuNode.Leaf) -> Unit,
    onInteractionStateChanged: (Boolean) -> Unit,
    modifier: Modifier = Modifier
) {
    val view = LocalView.current
    val accent = MaterialTheme.colorScheme.primary
    val onAccent = MaterialTheme.colorScheme.onPrimary
    val outline = MaterialTheme.colorScheme.outline
    val hairline = MaterialTheme.colorScheme.outlineVariant
    val paper = MaterialTheme.colorScheme.background
    val ink = MaterialTheme.colorScheme.onSurface
    val inkSoft = MaterialTheme.colorScheme.onSurfaceVariant
    val dragging = activePath.isNotEmpty()
    val dialSize = if (compact) 96.dp else 108.dp

    // The gesture loop outlives recompositions; read the latest values through these.
    val currentTree by rememberUpdatedState(tree)
    val currentGeometry by rememberUpdatedState(geometry)
    val currentPath by rememberUpdatedState(activePath)

    Column(modifier, horizontalAlignment = Alignment.CenterHorizontally) {
        Box(
            Modifier.size(dialSize + 72.dp, dialSize + 40.dp),
            contentAlignment = Alignment.BottomCenter
        ) {
            // Idle hint: an arc of ticks above the dial where the options will open.
            Canvas(Modifier.fillMaxSize().alpha(if (dragging) 0f else 1f)) {
                val c = Offset(size.width / 2f, size.height - dialSize.toPx() / 2f)
                val r = dialSize.toPx() / 2f + 22.dp.toPx()
                drawArc(
                    color = hairline,
                    startAngle = 180f, sweepAngle = 180f, useCenter = false,
                    topLeft = Offset(c.x - r, c.y - r), size = Size(r * 2, r * 2),
                    style = Stroke(width = 1.dp.toPx())
                )
                for (k in 0 until 4) {
                    val rad = Math.toRadians(-180.0 + 22.5 + 45.0 * k)
                    drawCircle(outline, 3.dp.toPx(), Offset(c.x + r * cos(rad).toFloat(), c.y + r * sin(rad).toFloat()))
                }
            }
            Box(
                modifier = Modifier
                    .size(dialSize)
                    .onGloballyPositioned(onPositioned)
                    .clip(CircleShape)
                    .background(if (dragging) accent else paper)
                    .border(1.dp, if (dragging) accent else outline, CircleShape)
                    .semantics { contentDescription = "Snooze dial: hold and drag up to pick when" }
                    .pointerInput(Unit) {
                        awaitPointerEventScope {
                            while (true) {
                                val down = awaitFirstDown(requireUnconsumed = false)
                                val geo = currentGeometry ?: continue
                                val center = Offset(size.width / 2f, size.height / 2f)
                                down.consume()
                                onInteractionStateChanged(true)
                                var path: List<MenuNode> = listOf(currentTree)
                                onPathChange(path)
                                view.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
                                var released = false
                                while (!released) {
                                    val event = awaitPointerEvent()
                                    val change = event.changes.firstOrNull { it.id == down.id } ?: break
                                    if (change.pressed) {
                                        change.consume()
                                        val next = selectSnoozePath(change.position - center, currentTree, currentGeometry ?: geo, path)
                                        if (next != path) {
                                            path = next
                                            onPathChange(next)
                                            view.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
                                        }
                                    } else {
                                        released = true
                                    }
                                }
                                onPathChange(emptyList())
                                onInteractionStateChanged(false)
                                val leaf = path.lastOrNull() as? MenuNode.Leaf
                                if (released && leaf != null) {
                                    view.performHapticFeedback(HapticFeedbackConstants.CONTEXT_CLICK)
                                    onSelected(leaf)
                                }
                            }
                        }
                    },
                contentAlignment = Alignment.Center
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        text = "HOLD",
                        style = MaterialTheme.typography.labelSmall,
                        color = if (dragging) onAccent.copy(alpha = 0.75f) else inkSoft
                    )
                    Text(
                        text = "Snooze",
                        style = MaterialTheme.typography.headlineSmall,
                        color = if (dragging) onAccent else ink
                    )
                }
            }
        }
        Spacer(Modifier.height(8.dp))
        MonoLabel(
            "Hold & drag up · time · place · car",
            color = inkSoft,
            modifier = Modifier.alpha(if (dragging) 0f else 1f)
        )
    }
}

/** The open fan, drawn over the whole window around the dial ([origin], px). Touch-transparent. */
@Composable
private fun SnoozeFanOverlay(
    tree: MenuNode.Parent,
    geometry: SnoozeDialGeometry,
    activePath: List<MenuNode>,
    origin: Offset
) {
    val accent = MaterialTheme.colorScheme.primary
    val onAccent = MaterialTheme.colorScheme.onPrimary
    val hairline = MaterialTheme.colorScheme.outlineVariant
    val outline = MaterialTheme.colorScheme.outline
    val paper = MaterialTheme.colorScheme.background
    val ink = MaterialTheme.colorScheme.onSurface
    val density = LocalDensity.current
    val nodes = remember(tree, geometry, activePath) { layoutSnoozeFan(tree, geometry, activePath) }

    Box(Modifier.fillMaxSize()) {
        Canvas(Modifier.fillMaxSize()) {
            val hair = 1.dp.toPx()
            nodes.forEach { vn ->
                drawLine(
                    color = if (vn.isHighlighted) accent else hairline,
                    // Inner-ring spokes start at the dial's edge, not across its face.
                    start = if (vn.parentX == 0f && vn.parentY == 0f) {
                        val len = sqrt(vn.x * vn.x + vn.y * vn.y).coerceAtLeast(1f)
                        val edge = 56.dp.toPx()
                        Offset(origin.x + vn.x / len * edge, origin.y + vn.y / len * edge)
                    } else Offset(origin.x + vn.parentX, origin.y + vn.parentY),
                    end = Offset(origin.x + vn.x, origin.y + vn.y),
                    strokeWidth = if (vn.isHighlighted) 2.dp.toPx() else hair
                )
            }
            nodes.forEach { vn ->
                val c = Offset(origin.x + vn.x, origin.y + vn.y)
                drawCircle(if (vn.isHighlighted) accent else paper, vn.radiusPx, c)
                drawCircle(if (vn.isHighlighted) accent else outline, vn.radiusPx, c, style = Stroke(hair))
            }
        }
        nodes.forEach { vn ->
            val d = with(density) { (vn.radiusPx * 2).toDp() }
            Box(
                Modifier
                    .offset { IntOffset((origin.x + vn.x - vn.radiusPx).roundToInt(), (origin.y + vn.y - vn.radiusPx).roundToInt()) }
                    .size(d),
                contentAlignment = Alignment.Center
            ) {
                val icon = level1Icon(vn.label)
                val tint = if (vn.isHighlighted) onAccent else ink
                if (icon != null) {
                    Icon(icon, contentDescription = vn.label, tint = tint, modifier = Modifier.size(24.dp))
                } else {
                    // Sans, sentence case: mono caps break mid-word in a 60 dp circle.
                    Text(
                        text = vn.label,
                        style = MaterialTheme.typography.bodySmall.copy(textDirection = TextDirection.Content),
                        fontSize = if (vn.label.length > 9) 11.sp else 12.sp,
                        color = tint,
                        lineHeight = 13.sp,
                        textAlign = TextAlign.Center,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.padding(horizontal = 5.dp)
                    )
                }
            }
        }
    }
}

/** Top-of-screen readout while dragging: where you are, and what letting go does. */
@Composable
private fun SnoozeReadout(path: List<MenuNode>, modifier: Modifier = Modifier) {
    val (headline, hint) = snoozeReadout(path)
    val onLeaf = path.lastOrNull() is MenuNode.Leaf
    Column(modifier, horizontalAlignment = Alignment.CenterHorizontally) {
        MonoLabel(
            (listOf("Snooze") + path.drop(1).map(::shortLabel)).joinToString("  ›  "),
            color = MaterialTheme.colorScheme.primary
        )
        Spacer(Modifier.height(8.dp))
        Text(
            headline,
            style = MaterialTheme.typography.headlineMedium.copy(textDirection = TextDirection.Content),
            color = MaterialTheme.colorScheme.onBackground,
            textAlign = TextAlign.Center,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis
        )
        Spacer(Modifier.height(6.dp))
        Text(
            hint,
            style = MaterialTheme.typography.bodyMedium,
            color = if (onLeaf) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}

fun extractLeadingEmoji(text: String): Pair<String?, String> {
    if (text.isEmpty()) return Pair(null, text)
    
    val codePoint = text.codePointAt(0)
    
    val isEmoji = (codePoint in 0x1F300..0x1F9FF) || 
                  (codePoint in 0x1FA00..0x1FAFF) ||
                  (codePoint in 0x2600..0x27BF) ||
                  (codePoint in 0x1F1E6..0x1F1FF)
                  
    if (isEmoji) {
        val emojiStr = String(Character.toChars(codePoint))
        var remaining = text.substring(emojiStr.length)
        remaining = remaining.trimStart { it == ' ' || it == '-' || it == ':' || it == '–' }
        return Pair(emojiStr, remaining)
    }
    
    return Pair(null, text)
}
