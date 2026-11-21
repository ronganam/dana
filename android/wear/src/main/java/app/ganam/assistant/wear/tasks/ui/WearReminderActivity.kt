package app.ganam.assistant.wear.tasks

import android.content.Context
import android.os.Build
import android.os.Bundle
import android.os.VibrationEffect
import android.os.Vibrator
import android.view.HapticFeedbackConstants
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.outlined.Snooze
import androidx.compose.material.icons.outlined.WbSunny
import androidx.compose.material.icons.outlined.DirectionsCar
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.Work
import androidx.compose.material.icons.outlined.Cottage
import androidx.compose.material.icons.outlined.FitnessCenter
import androidx.compose.material.icons.outlined.School
import androidx.compose.material.icons.outlined.ShoppingCart
import androidx.compose.material.icons.outlined.Place
import androidx.compose.material.icons.filled.SouthWest
import androidx.compose.material.icons.filled.NorthEast
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.rotary.onRotaryScrollEvent
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.wear.compose.foundation.CurvedLayout
import androidx.wear.compose.material.curvedText
import androidx.wear.compose.material.Icon
import androidx.wear.compose.material.Text
import app.ganam.assistant.wear.ui.theme.GanamWear
import app.ganam.assistant.wear.ui.theme.GanamWearTheme
import com.google.android.gms.wearable.Wearable
import org.json.JSONObject
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.sin

class WearReminderActivity : ComponentActivity() {

    private var vibrator: Vibrator? = null
    private var currentTaskId = ""

    private val closeReceiver = object : android.content.BroadcastReceiver() {
        override fun onReceive(context: Context, intent: android.content.Intent) {
            val id = intent.getStringExtra(WearReminderSync.EXTRA_TASK_ID)
            if (id == null || id == currentTaskId) {
                stopVibration()
                finish()
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

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

        val taskId = intent.getStringExtra("task_id") ?: ""
        val taskTitle = intent.getStringExtra("task_title") ?: "Reminder"
        val priority = intent.getStringExtra("priority") ?: "medium"
        val triggerType = intent.getStringExtra("trigger_type") ?: "time"

        currentTaskId = taskId
        // The phone handled this alarm: stop ringing here too.
        androidx.core.content.ContextCompat.registerReceiver(
            this, closeReceiver, android.content.IntentFilter(WearReminderSync.ACTION_CLOSE),
            androidx.core.content.ContextCompat.RECEIVER_NOT_EXPORTED
        )

        startVibration()
        val options = wearSnoozeOptions(WearReminderSync.zones(this))

        setContent {
            GanamWearTheme {
                WearReminderAlarmScreen(
                    taskTitle = taskTitle,
                    priority = priority,
                    triggerType = triggerType,
                    options = options,
                    onPickerOpen = { stopVibration() },
                    onComplete = {
                        stopVibration()
                        WearReminderSync.handledOnWatch(this, taskId)
                        sendAction("/update_task_status", JSONObject().apply {
                            put("task_id", taskId)
                            put("status", "completed")
                        })
                        finish()
                    },
                    onSnooze = { option ->
                        stopVibration()
                        WearReminderSync.handledOnWatch(this, taskId)
                        sendAction("/snooze_task", option.payload().put("task_id", taskId))
                        finish()
                    },
                    onDismiss = {
                        stopVibration()
                        WearReminderSync.handledOnWatch(this, taskId)
                        finish()
                    }
                )
            }
        }
    }

    private fun sendAction(path: String, payload: JSONObject) {
        Wearable.getNodeClient(this).connectedNodes
            .addOnSuccessListener { nodes ->
                val phoneId = nodes.firstOrNull()?.id ?: return@addOnSuccessListener
                Wearable.getMessageClient(this)
                    .sendMessage(phoneId, path, payload.toString().toByteArray())
            }
    }

    private fun startVibration() {
        @Suppress("DEPRECATION")
        vibrator = getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
        val pattern = longArrayOf(0, 1000, 500, 1000)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            vibrator?.vibrate(VibrationEffect.createWaveform(pattern, 0))
        } else {
            @Suppress("DEPRECATION")
            vibrator?.vibrate(pattern, 0)
        }
    }

    private fun stopVibration() {
        vibrator?.cancel()
    }

    override fun onDestroy() {
        try {
            unregisterReceiver(closeReceiver)
        } catch (e: IllegalArgumentException) {
            // Not registered.
        }
        stopVibration()
        super.onDestroy()
    }
}

/** One snooze the watch offers; [payload] is what `/snooze_task` sends to the phone. */
data class WearSnooze(
    val group: String,
    val label: String,
    val detail: String?,
    val sentence: String,
    val type: String,
    val minutes: Int = 0,
    val location: String = "",
    val event: String = "",
    val action: String = ""
) {
    fun payload(): JSONObject = JSONObject().apply {
        put("type", type)
        when (type) {
            "time" -> put("minutes", minutes)
            "location" -> { put("location", location); put("event", event) }
            "car" -> put("action", action)
        }
    }
}

/** Same choices as the phone's dial: time, waking up, the car, then each place (arrive / leave). */
fun wearSnoozeOptions(zones: List<String>): List<WearSnooze> = buildList {
    listOf("15 min" to 15, "1 hour" to 60, "3 hours" to 180, "1 day" to 1440).forEach { (l, m) ->
        add(WearSnooze("Time", l, null, "In $l", "time", minutes = m))
    }
    add(WearSnooze("Wake up", "Wake up", null, "When I wake up", "wake"))
    add(WearSnooze("Car", "Get in", "the car", "When I get in the car", "car", action = "enter"))
    add(WearSnooze("Car", "Get out", "of the car", "When I get out of the car", "car", action = "leave"))
    zones.forEach { z ->
        add(WearSnooze("Place", "Arrive", "at $z", "When I arrive at $z", "location", location = z, event = "arrive"))
        add(WearSnooze("Place", "Leave", z, "When I leave $z", "location", location = z, event = "leave"))
    }
}

/**
 * The watch alarm, in the phone's Ink & Paper language: a curved mono kicker,
 * the serif title, and three round actions — Snooze, Done (the one accent),
 * Dismiss.
 *
 * Snoozing is the watch's version of the phone's drag dial: turn the crown
 * (or bezel) — or tap Snooze — and the screen becomes a picker. Each click of
 * the crown steps to the next option (time → wake up → car → places), a ring
 * of dots around the edge shows where you are, and the centre reads out the
 * result. Tap it, or the Snooze pill, to confirm; swipe back to return.
 */
@Composable
fun WearReminderAlarmScreen(
    taskTitle: String,
    priority: String,
    triggerType: String,
    options: List<WearSnooze>,
    onPickerOpen: () -> Unit,
    onComplete: () -> Unit,
    onSnooze: (WearSnooze) -> Unit,
    onDismiss: () -> Unit
) {
    val view = LocalView.current
    val focusRequester = remember { FocusRequester() }
    var picking by remember { mutableStateOf(false) }
    var index by remember { mutableIntStateOf(1) } // "1 hour" — the usual pick
    val accumulated = remember { FloatArray(1) }
    val (emoji, cleanTitle) = remember(taskTitle) { extractLeadingEmoji(taskTitle) }
    val now = remember { java.time.LocalTime.now() }

    LaunchedEffect(Unit) { runCatching { focusRequester.requestFocus() } }
    LaunchedEffect(picking) {
        if (picking) onPickerOpen()
        runCatching { focusRequester.requestFocus() }
    }
    BackHandler(enabled = picking) { picking = false }

    fun step(delta: Int) {
        val next = (index + delta).coerceIn(0, options.lastIndex)
        if (next != index) {
            index = next
            view.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(GanamWear.Canvas)
            .onRotaryScrollEvent { e ->
                accumulated[0] += e.verticalScrollPixels
                if (abs(accumulated[0]) >= ROTARY_STEP_PX) {
                    val dir = if (accumulated[0] > 0) 1 else -1
                    accumulated[0] = 0f
                    if (!picking) {
                        picking = true
                        view.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
                    } else step(dir)
                }
                true
            }
            .focusRequester(focusRequester)
            .focusable(),
        contentAlignment = Alignment.Center
    ) {
        AnimatedContent(
            targetState = picking,
            transitionSpec = { fadeIn(tween(160)) togetherWith fadeOut(tween(120)) },
            label = "mode"
        ) { isPicking ->
            if (!isPicking) {
                AlarmFace(
                    kicker = "Reminder · ${"%02d:%02d".format(now.hour, now.minute)}${triggerSuffix(triggerType)}",
                    priority = priority,
                    emoji = emoji,
                    title = cleanTitle,
                    onSnooze = { picking = true },
                    onComplete = onComplete,
                    onDismiss = onDismiss
                )
            } else {
                SnoozePicker(
                    options = options,
                    index = index,
                    onStep = ::step,
                    onConfirm = { onSnooze(options[index]) }
                )
            }
        }
    }
}

private const val ROTARY_STEP_PX = 48f

@Composable
private fun AlarmFace(
    kicker: String,
    priority: String,
    emoji: String?,
    title: String,
    onSnooze: () -> Unit,
    onComplete: () -> Unit,
    onDismiss: () -> Unit
) {
    val priorityColor = when (priority.lowercase()) {
        "high" -> GanamWear.Danger
        "low" -> GanamWear.Mute
        else -> GanamWear.Warning
    }
    Box(Modifier.fillMaxSize()) {
        CurvedLayout(anchor = 270f, modifier = Modifier.padding(top = 6.dp)) {
            curvedText(
                text = kicker.uppercase(),
                fontSize = 10.sp, color = GanamWear.Accent, fontFamily = GanamWear.Mono
            )
        }
        Column(
            Modifier
                .fillMaxSize()
                .padding(horizontal = 22.dp)
                .padding(top = 30.dp, bottom = 14.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(6.dp).clip(CircleShape).background(priorityColor))
                Spacer(Modifier.width(5.dp))
                Text(
                    priority.uppercase(),
                    fontFamily = GanamWear.Mono, fontSize = 9.sp, color = GanamWear.InkSoft
                )
            }
            Spacer(Modifier.height(4.dp))
            Column(
                Modifier.weight(1f).fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center
            ) {
                // Emoji inline with the title: a row of its own costs a line of text on a watch.
                Text(
                    text = if (emoji != null) "$emoji $title" else title,
                    fontFamily = GanamWear.Serif,
                    fontSize = if (title.length > 40) 17.sp else 20.sp,
                    lineHeight = if (title.length > 40) 19.sp else 22.sp,
                    color = GanamWear.Ink,
                    textAlign = TextAlign.Center,
                    maxLines = 3,
                    overflow = TextOverflow.Ellipsis,
                    style = androidx.wear.compose.material.LocalTextStyle.current.copy(textDirection = TextDirection.Content)
                )
            }
            Spacer(Modifier.height(6.dp))
            Row(
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                verticalAlignment = Alignment.Top
            ) {
                RoundAction(Icons.Outlined.Snooze, "Snooze", size = 42.dp, filled = false, onClick = onSnooze)
                RoundAction(Icons.Filled.Check, "Done", size = 52.dp, filled = true, onClick = onComplete)
                RoundAction(Icons.Filled.Close, "Dismiss", size = 42.dp, filled = false, onClick = onDismiss)
            }
            Spacer(Modifier.height(4.dp))
            Text(
                "TURN CROWN TO SNOOZE",
                fontFamily = GanamWear.Mono, fontSize = 8.sp, color = GanamWear.Mute
            )
        }
    }
}

@Composable
private fun RoundAction(icon: ImageVector, label: String, size: Dp, filled: Boolean, onClick: () -> Unit) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Box(
            Modifier
                .size(size)
                .clip(CircleShape)
                .then(
                    if (filled) Modifier.background(GanamWear.Accent)
                    else Modifier.background(GanamWear.Surface).border(1.dp, GanamWear.Outline, CircleShape)
                )
                .clickable(onClick = onClick),
            contentAlignment = Alignment.Center
        ) {
            Icon(
                icon, contentDescription = label,
                tint = if (filled) GanamWear.OnAccent else GanamWear.Ink,
                modifier = Modifier.size(if (filled) 26.dp else 20.dp)
            )
        }
        Spacer(Modifier.height(2.dp))
        Text(label, fontSize = 9.sp, color = if (filled) GanamWear.Ink else GanamWear.InkSoft)
    }
}

@Composable
private fun SnoozePicker(
    options: List<WearSnooze>,
    index: Int,
    onStep: (Int) -> Unit,
    onConfirm: () -> Unit
) {
    val opt = options[index]
    Box(Modifier.fillMaxSize()) {
        // Bezel ring: one small marker per option along the edge, so you can see
        // what's where — "15m"/"1h" for times, a sun, the car, each place's icon
        // (with an in / out arrow). The current one is filled in accent.
        BoxWithConstraints(Modifier.fillMaxSize()) {
            val n = options.size
            val span = 256f // clockwise from lower-left over the top; the gap holds the label
            val half = minOf(maxWidth, maxHeight) / 2
            // Shrink markers when there are many places so they never touch.
            val gap = (half.value - 16f) * Math.toRadians(span.toDouble()).toFloat() / (n - 1).coerceAtLeast(1)
            val marker = gap.coerceIn(14f, 22f).dp
            val r = half - marker / 2 - 3.dp
            options.forEachIndexed { i, o ->
                val a = Math.toRadians((-90f - span / 2f + span * i / (n - 1).coerceAtLeast(1)).toDouble())
                val selected = i == index
                val size = if (selected) marker + 6.dp else marker
                Box(
                    Modifier
                        .offset(
                            x = half + r * cos(a).toFloat() - size / 2,
                            y = half + r * sin(a).toFloat() - size / 2
                        )
                        .size(size)
                        .clip(CircleShape)
                        .then(if (selected) Modifier.background(GanamWear.Accent) else Modifier)
                        .clickable { onStep(i - index) },
                    contentAlignment = Alignment.Center
                ) {
                    SnoozeMarker(o, tint = if (selected) GanamWear.OnAccent else GanamWear.InkSoft, size = size)
                }
            }
        }
        CurvedLayout(anchor = 90f, modifier = Modifier.padding(bottom = 16.dp)) {
            curvedText(
                text = "SNOOZE · ${opt.group.uppercase()}",
                fontSize = 10.sp, color = GanamWear.Accent, fontFamily = GanamWear.Mono,
                angularDirection = androidx.wear.compose.foundation.CurvedDirection.Angular.CounterClockwise
            )
        }
        Row(
            Modifier.fillMaxSize().padding(horizontal = 30.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            StepArrow(Icons.AutoMirrored.Filled.KeyboardArrowLeft, "Previous", enabled = index > 0) { onStep(-1) }
            Column(
                Modifier
                    .weight(1f)
                    .clip(RoundedCornerShape(20.dp))
                    .clickable(onClick = onConfirm)
                    .padding(vertical = 8.dp),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Text(
                    opt.label,
                    fontFamily = GanamWear.Serif, fontSize = 30.sp, lineHeight = 32.sp,
                    color = GanamWear.Ink, textAlign = TextAlign.Center, maxLines = 1
                )
                opt.detail?.let {
                    Text(
                        it, fontFamily = GanamWear.Serif, fontSize = 18.sp, color = GanamWear.InkSoft,
                        textAlign = TextAlign.Center, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        style = androidx.wear.compose.material.LocalTextStyle.current.copy(textDirection = TextDirection.Content)
                    )
                }
                Spacer(Modifier.height(10.dp))
                Box(
                    Modifier
                        .clip(RoundedCornerShape(50))
                        .background(GanamWear.Accent)
                        .padding(horizontal = 16.dp, vertical = 6.dp)
                ) {
                    Text("Snooze", fontSize = 13.sp, color = GanamWear.OnAccent)
                }
            }
            StepArrow(Icons.AutoMirrored.Filled.KeyboardArrowRight, "Next", enabled = index < options.lastIndex) { onStep(1) }
        }
    }
}

@Composable
private fun StepArrow(icon: ImageVector, label: String, enabled: Boolean, onClick: () -> Unit) {
    Box(
        Modifier
            .size(32.dp)
            .clip(CircleShape)
            .clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center
    ) {
        Icon(icon, contentDescription = label, tint = if (enabled) GanamWear.InkSoft else Color.Transparent, modifier = Modifier.size(22.dp))
    }
}

/** The ring marker for one option: short text for times, an icon (+ in/out arrow) for the rest. */
@Composable
private fun SnoozeMarker(o: WearSnooze, tint: Color, size: Dp) {
    if (o.type == "time") {
        val short = when (o.minutes) { 15 -> "15m"; 60 -> "1h"; 180 -> "3h"; 1440 -> "1d"; else -> "${o.minutes}m" }
        Text(short, fontFamily = GanamWear.Mono, fontSize = (size.value * 0.42f).sp, color = tint, maxLines = 1)
        return
    }
    val icon = when (o.type) {
        "wake" -> Icons.Outlined.WbSunny
        "car" -> Icons.Outlined.DirectionsCar
        else -> zoneIcon(o.location)
    }
    val inOut = when {
        o.type == "location" && o.event == "arrive" || o.type == "car" && o.action == "enter" -> Icons.Filled.SouthWest
        o.type == "location" || o.type == "car" -> Icons.Filled.NorthEast
        else -> null
    }
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Icon(icon, contentDescription = o.sentence, tint = tint, modifier = Modifier.size(size * 0.62f))
        if (inOut != null) {
            Icon(
                inOut, contentDescription = null, tint = tint,
                modifier = Modifier.align(Alignment.TopEnd).size(size * 0.36f)
            )
        }
    }
}

/** A recognisable icon for a Home Assistant zone name (English or Hebrew). */
private fun zoneIcon(zone: String): ImageVector {
    val z = zone.lowercase()
    return when {
        "home" in z || "בית" in z && "אמא" !in z && "הורים" !in z -> Icons.Outlined.Home
        "work" in z || "office" in z || "עבודה" in z || "משרד" in z -> Icons.Outlined.Work
        "parent" in z || "mom" in z || "אמא" in z || "הורים" in z -> Icons.Outlined.Cottage
        "gym" in z || "חדר כושר" in z -> Icons.Outlined.FitnessCenter
        "school" in z || "university" in z || "בית ספר" in z -> Icons.Outlined.School
        "shop" in z || "market" in z || "סופר" in z -> Icons.Outlined.ShoppingCart
        else -> Icons.Outlined.Place
    }
}

private fun triggerSuffix(type: String) = when (type.lowercase()) {
    "location" -> " · Place"
    "car" -> " · Car"
    "wake" -> " · Wake"
    else -> ""
}

private fun extractLeadingEmoji(text: String): Pair<String?, String> {
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
