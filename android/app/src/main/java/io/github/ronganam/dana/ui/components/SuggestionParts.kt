package io.github.ronganam.dana.ui.components

import io.github.ronganam.dana.data.network.Zones
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Chat
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.models.CallLabel
import io.github.ronganam.dana.data.models.CallSuggestion
import io.github.ronganam.dana.data.models.ChatContext
import io.github.ronganam.dana.ui.theme.Danger
import io.github.ronganam.dana.ui.theme.Space
import io.github.ronganam.dana.ui.theme.Warning
import io.github.ronganam.dana.data.models.LocationTrigger
import io.github.ronganam.dana.data.models.Triggers
import io.github.ronganam.dana.data.network.SuggestionEdits
import java.time.Duration
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Calendar
import java.util.Locale
import android.app.DatePickerDialog
import android.app.TimePickerDialog
import androidx.compose.ui.platform.LocalContext

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

private fun isHebrewText(s: String) = s.any { it in '֐'..'׿' }

/**
 * ISO timestamp → local wall-clock time. Ones with an offset ("…+00:00",
 * "…Z", e.g. notification posted_at) are converted to this phone's zone;
 * naive ones (call timestamps) are already local.
 */
/**
 * A name inside a mixed line ("From the call with אמא · 2h ago"): Unicode
 * isolates keep a Hebrew name from pulling the neighbouring " · 2" into its
 * right-to-left run (which rendered as "2 · אמאh ago").
 */
fun isolate(s: String) = "\u2068$s\u2069"

fun parseLocalDateTime(iso: String?): LocalDateTime? {
    if (iso.isNullOrBlank()) return null
    return runCatching {
        java.time.OffsetDateTime.parse(iso)
            .atZoneSameInstant(java.time.ZoneId.systemDefault()).toLocalDateTime()
    }.getOrNull() ?: runCatching { LocalDateTime.parse(iso.take(19)) }.getOrNull()
}

/** "2h ago", "yesterday", "3d ago" from an ISO timestamp. */
fun relativeAge(iso: String?): String {
    val t = parseLocalDateTime(iso) ?: return ""
    val d = Duration.between(t, LocalDateTime.now())
    return when {
        d.isNegative -> ""
        d.toMinutes() < 60 -> "${d.toMinutes().coerceAtLeast(1)}m ago"
        d.toHours() < 24 -> "${d.toHours()}h ago"
        d.toDays() < 2 -> "yesterday"
        d.toDays() < 30 -> "${d.toDays()}d ago"
        else -> t.format(DateTimeFormatter.ofPattern("d MMM", Locale.getDefault()))
    }
}

/** "Thu 12 Nov" / "Today" / "Tomorrow" for a YYYY-MM-DD date. */
fun friendlyDate(date: String?): String? {
    val d = date?.let { runCatching { LocalDate.parse(it.take(10)) }.getOrNull() } ?: return date
    val today = LocalDate.now()
    return when (d) {
        today -> "Today"
        today.plusDays(1) -> "Tomorrow"
        else -> d.format(DateTimeFormatter.ofPattern("EEE d MMM", Locale.getDefault()))
    }
}

/** "Tomorrow 09:00" for "YYYY-MM-DD HH:MM" or "YYYY-MM-DDTHH:MM:SS". */
fun friendlyDateTime(value: String?): String? {
    if (value.isNullOrBlank()) return null
    val norm = value.replace('T', ' ')
    val date = friendlyDate(norm.take(10)) ?: return value
    val time = norm.drop(11).take(5)
    return if (time.length == 5) "$date $time" else date
}

/** Human trigger descriptions, e.g. ["⏰ Tomorrow 09:00", "📍 Arrive home"]. */
fun describeTriggers(triggers: Triggers?): List<String> {
    if (triggers == null) return emptyList()
    val out = mutableListOf<String>()
    triggers.time?.takeIf { it.isNotBlank() }?.let { out += "⏰ ${friendlyDateTime(it)}" }
    triggers.location?.let { loc ->
        val place = loc.location.orEmpty()
        if (place.isNotBlank()) out += "📍 ${if (loc.event == "leave") "Leave" else "Arrive"} ${place.replaceFirstChar { it.uppercase() }}"
    }
    triggers.car?.takeIf { it.isNotBlank() }?.let { out += if (it == "leave") "🚗 Leaving the car" else "🚗 Getting in the car" }
    if (triggers.wake == true) out += "☀️ When I wake up"
    return out
}

fun CallSuggestion.kindLabel(): String = when (kind) {
    "event" -> "Event"
    "alert" -> "Security"
    else -> "Reminder"
}

fun CallSuggestion.metaLine(): List<String> {
    val bits = mutableListOf<String>()
    when (kind) {
        "event" -> {
            friendlyDateTime(start)?.let { bits += "📅 $it" }
            location?.let { bits += "📍 $it" }
        }
        "task" -> {
            dueDate?.let { bits += "Due ${friendlyDate(it)}" }
            bits += describeTriggers(triggers)
            if (priority == "high") bits += "High priority"
        }
    }
    return bits
}

// ---------------------------------------------------------------------------
// Chips
// ---------------------------------------------------------------------------

/** A hairline-outlined mono chip (labels, triggers, facts). */
@Composable
fun InkChip(
    text: String,
    modifier: Modifier = Modifier,
    selected: Boolean = false,
    accent: Color = MaterialTheme.colorScheme.primary,
    onClick: (() -> Unit)? = null,
    maxLines: Int = 1,
    trailing: (@Composable () -> Unit)? = null
) {
    val shape = RoundedCornerShape(8.dp)
    Row(
        modifier = modifier
            .clip(shape)
            .then(if (selected) Modifier.background(accent.copy(alpha = 0.14f)) else Modifier)
            .border(1.dp, if (selected) accent.copy(alpha = 0.7f) else MaterialTheme.colorScheme.outlineVariant, shape)
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .heightIn(min = 30.dp)
            .padding(horizontal = 10.dp, vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(
            text,
            style = MaterialTheme.typography.labelMedium.copy(textDirection = TextDirection.Content),
            color = if (selected) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = maxLines,
            overflow = TextOverflow.Ellipsis,
            modifier = if (trailing != null) Modifier.weight(1f, fill = false) else Modifier
        )
        if (trailing != null) {
            Spacer(Modifier.width(4.dp))
            trailing()
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun LabelChips(labels: List<CallLabel>, modifier: Modifier = Modifier) {
    if (labels.isEmpty()) return
    FlowRow(modifier = modifier, horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        labels.forEach { InkChip("${it.emoji} ${it.name}") }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun MetaChips(items: List<String>, modifier: Modifier = Modifier) {
    if (items.isEmpty()) return
    FlowRow(modifier = modifier, horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        // Facts are sentences: wrap rather than cut them off on the narrow cover screen.
        items.forEach { InkChip(it, maxLines = 3) }
    }
}

// ---------------------------------------------------------------------------
// Suggestion rows (welcome area, inbox, call detail) and cards (Debrief)
// ---------------------------------------------------------------------------

/**
 * One pending suggestion as a hairline-ruled row: mono kicker (kind · source ·
 * age), the title, its trigger/date chips, and three actions — create, ask
 * the assistant to change it, dismiss.
 */
@Composable
fun SuggestionRow(
    suggestion: CallSuggestion,
    busy: Boolean,
    onAccept: () -> Unit,
    onAsk: () -> Unit,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
    onOpen: (() -> Unit)? = null,
    showSource: Boolean = true,
    horizontalPadding: androidx.compose.ui.unit.Dp = 0.dp
) {
    val isAlert = suggestion.kind == "alert"
    val accent = if (isAlert) Danger else MaterialTheme.colorScheme.primary
    Column(modifier) {
        Row(
            Modifier
                .fillMaxWidth()
                .then(if (onOpen != null) Modifier.clickable(onClick = onOpen) else Modifier)
                .padding(horizontal = horizontalPadding, vertical = 12.dp),
            verticalAlignment = Alignment.Top
        ) {
            Column(Modifier.weight(1f)) {
                val source = suggestion.source
                val kicker = buildString {
                    append(suggestion.kindLabel())
                    if (showSource && source?.contact != null) append(" · ").append(isolate(source.contact))
                    relativeAge(source?.callTimestamp).takeIf { showSource && it.isNotEmpty() }?.let { append(" · ").append(it) }
                }
                MonoLabel(kicker, color = accent)
                Spacer(Modifier.height(3.dp))
                Text(
                    suggestion.title,
                    style = MaterialTheme.typography.bodyLarge.copy(textDirection = TextDirection.Content),
                    color = MaterialTheme.colorScheme.onSurface,
                    maxLines = 3,
                    overflow = TextOverflow.Ellipsis
                )
                val meta = suggestion.metaLine()
                if (meta.isNotEmpty()) {
                    Spacer(Modifier.height(6.dp))
                    MetaChips(meta)
                }
            }
            Spacer(Modifier.width(8.dp))
            if (busy) {
                CircularProgressIndicator(Modifier.padding(12.dp).size(20.dp), strokeWidth = 2.dp)
            } else Row {
                if (!isAlert) {
                    IconButton(onClick = onAsk) {
                        Icon(Icons.AutoMirrored.Outlined.Chat, contentDescription = "Ask Dana to change it",
                            tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    FilledIconButton(
                        onClick = onAccept,
                        colors = IconButtonDefaults.filledIconButtonColors(containerColor = accent, contentColor = MaterialTheme.colorScheme.onPrimary)
                    ) { Icon(Icons.Filled.Check, contentDescription = if (suggestion.kind == "event") "Add to calendar" else "Create reminder") }
                } else {
                    IconButton(onClick = onAsk) {
                        Icon(Icons.AutoMirrored.Outlined.Chat, contentDescription = "Ask what to do", tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                IconButton(onClick = onDismiss) {
                    Icon(Icons.Filled.Close, contentDescription = "Dismiss", tint = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
        Rule()
    }
}

/**
 * The Debrief card: bigger, with the call's own words as evidence and labelled
 * buttons (Create / Edit / Ask / Dismiss) instead of icons.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SuggestionCard(
    suggestion: CallSuggestion,
    busy: Boolean,
    onAccept: () -> Unit,
    onEdit: () -> Unit,
    onAsk: () -> Unit,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier
) {
    val isAlert = suggestion.kind == "alert"
    val accent = if (isAlert) Danger else MaterialTheme.colorScheme.primary
    val resolved = suggestion.status != "pending"
    Column(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .border(1.dp, if (isAlert) Danger.copy(alpha = 0.6f) else MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(14.dp))
            .background(MaterialTheme.colorScheme.surface)
            .padding(16.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            MonoLabel(suggestion.kindLabel(), color = accent, modifier = Modifier.weight(1f))
            if (resolved) MonoLabel(if (suggestion.status == "accepted") "Created" else "Dismissed")
        }
        Spacer(Modifier.height(6.dp))
        Text(
            suggestion.title,
            style = MaterialTheme.typography.titleMedium.copy(textDirection = TextDirection.Content),
            color = MaterialTheme.colorScheme.onSurface
        )
        val meta = suggestion.metaLine()
        if (meta.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            MetaChips(meta)
        }
        suggestion.evidence?.let { quote ->
            Spacer(Modifier.height(10.dp))
            Row(Modifier.height(IntrinsicSize.Min)) {
                Box(Modifier.width(2.dp).fillMaxHeight().background(MaterialTheme.colorScheme.outline))
                Spacer(Modifier.width(10.dp))
                Text(
                    "“$quote”",
                    style = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content),
                    fontStyle = FontStyle.Italic,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
        if (!resolved) {
            Spacer(Modifier.height(14.dp))
            if (busy) {
                LinearProgressIndicator(Modifier.fillMaxWidth())
            } else FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (!isAlert) {
                    Button(onClick = onAccept, colors = ButtonDefaults.buttonColors(containerColor = accent)) {
                        Icon(Icons.Filled.Check, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(6.dp))
                        Text(if (suggestion.kind == "event") "Add to calendar" else "Create")
                    }
                    run {
                        OutlinedButton(onClick = onEdit) {
                            Icon(Icons.Outlined.Edit, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("Edit")
                        }
                    }
                }
                OutlinedButton(onClick = onAsk) {
                    Icon(Icons.AutoMirrored.Outlined.Chat, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(6.dp))
                    Text(if (isAlert) "What should I do?" else "Ask Dana")
                }
                TextButton(onClick = onDismiss) { Text(if (isAlert) "Got it" else "Dismiss") }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Chat attachment card
// ---------------------------------------------------------------------------

/**
 * Shown at the top of a chat opened from a suggestion / call / task: what is
 * attached, and one-tap prompts so a change is one tap (or one sentence) away.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ContextAttachmentCard(
    context: ChatContext,
    onPrompt: (String) -> Unit,
    modifier: Modifier = Modifier,
    onOpenSource: (() -> Unit)? = null,
    showPrompts: Boolean = true
) {
    val done = context.status == "accepted" || context.status == "dismissed"
    Column(
        modifier
            .fillMaxWidth()
            .padding(top = 12.dp, bottom = 4.dp)
            .clip(RoundedCornerShape(14.dp))
            .border(1.dp, MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(14.dp))
            .background(MaterialTheme.colorScheme.surfaceContainerLow)
    ) {
        Row(
            Modifier
                .fillMaxWidth()
                .then(if (onOpenSource != null) Modifier.clickable(onClick = onOpenSource) else Modifier)
                .padding(horizontal = 14.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box(Modifier.width(3.dp).height(36.dp).background(MaterialTheme.colorScheme.primary))
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                MonoLabel(
                    when (context.kind) {
                        "suggestion" -> if (done) "Attached · ${context.status}" else "Attached · suggestion"
                        "call" -> "Attached · call"
                        else -> "Attached · reminder"
                    },
                    color = MaterialTheme.colorScheme.primary
                )
                Spacer(Modifier.height(2.dp))
                Text(
                    "${context.emoji} ${context.title}",
                    style = MaterialTheme.typography.titleSmall.copy(textDirection = TextDirection.Content),
                    color = MaterialTheme.colorScheme.onSurface,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis
                )
                if (context.subtitle.isNotBlank()) {
                    Text(
                        context.subtitle,
                        style = MaterialTheme.typography.bodySmall.copy(textDirection = TextDirection.Content),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            }
        }
        if (showPrompts && !done && context.quickPrompts.isNotEmpty()) {
            Rule()
            FlowRow(
                Modifier.fillMaxWidth().padding(12.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                context.quickPrompts.forEach { p -> InkChip(p, onClick = { onPrompt(p) }) }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Edit sheet
// ---------------------------------------------------------------------------

/**
 * Where a suggestion came from: the app (or call), who, when, and the original
 * text with the words the suggestion was based on marked.
 */
@Composable
fun SuggestionSourceBlock(
    suggestion: CallSuggestion,
    modifier: Modifier = Modifier,
    icon: (@Composable () -> Unit)? = null,
    onOpen: (() -> Unit)? = null
) {
    val src = suggestion.source
    val fromNotification = suggestion.fromNotification
    val kicker = buildList {
        add(if (fromNotification) "From ${src?.contact?.let(::isolate) ?: "a notification"}" else "From the call with ${src?.contact?.let(::isolate) ?: "someone"}")
        src?.sender?.takeIf { fromNotification && it != src.contact }?.let { add(isolate(it)) }
        relativeAge(src?.callTimestamp).takeIf { it.isNotEmpty() }?.let { add(it) }
    }.joinToString(" · ")
    val text = src?.text?.trim().orEmpty()
    val quote = suggestion.evidence?.trim().orEmpty()
    val highlight = MaterialTheme.colorScheme.primary.copy(alpha = 0.22f)
    val body = remember(text, quote, highlight) {
        buildAnnotatedString {
            append(text)
            val at = if (quote.length >= 4) text.indexOf(quote, ignoreCase = true) else -1
            if (at >= 0) addStyle(SpanStyle(background = highlight, fontWeight = FontWeight.SemiBold), at, at + quote.length)
        }
    }
    val quoteInText = quote.length >= 4 && text.contains(quote, ignoreCase = true)
    var expanded by remember(suggestion.id) { mutableStateOf(false) }
    Column(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(MaterialTheme.colorScheme.surfaceContainerLow)
            .border(1.dp, MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(12.dp))
            .padding(12.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (icon != null) {
                icon()
                Spacer(Modifier.width(10.dp))
            }
            Column(Modifier.weight(1f)) {
                MonoLabel(kicker)
                src?.title?.takeIf { it.isNotBlank() }?.let {
                    Text(it, style = MaterialTheme.typography.titleSmall.copy(textDirection = TextDirection.Content),
                        color = MaterialTheme.colorScheme.onSurface, maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
            }
        }
        if (text.isNotEmpty()) {
            Spacer(Modifier.height(8.dp))
            Text(
                body,
                style = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = if (expanded) Int.MAX_VALUE else 8,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.clickable { expanded = !expanded }
            )
        }
        if (quote.isNotEmpty() && !quoteInText) {
            Spacer(Modifier.height(8.dp))
            Row(Modifier.height(IntrinsicSize.Min)) {
                Box(Modifier.width(2.dp).fillMaxHeight().background(MaterialTheme.colorScheme.primary))
                Spacer(Modifier.width(8.dp))
                Text("“$quote”", style = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content),
                    fontStyle = FontStyle.Italic, color = MaterialTheme.colorScheme.onSurface)
            }
        }
        if (onOpen != null) {
            TextButton(onClick = onOpen, contentPadding = PaddingValues(horizontal = 0.dp)) {
                Text(if (fromNotification) "Open the notification" else "Open the call")
            }
        }
    }
}

/**
 * Review a suggestion before creating it: where it came from ([sourceBlock])
 * and every field editable — title, dates, reminder, priority, group and
 * triggers for a reminder; title, time and place for a calendar event.
 * Anything more involved goes through "Ask Dana".
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SuggestionEditSheet(
    suggestion: CallSuggestion,
    groups: List<String>,
    onCreate: (SuggestionEdits) -> Unit,
    onAsk: () -> Unit,
    onCancel: () -> Unit,
    sourceBlock: (@Composable () -> Unit)? = null,
    onDismiss: (() -> Unit)? = null,
    busy: Boolean = false
) {
    val isEvent = suggestion.kind == "event"
    val isAlert = suggestion.kind == "alert"
    var title by rememberSaveable(suggestion.id) { mutableStateOf(suggestion.title) }
    var due by rememberSaveable(suggestion.id) { mutableStateOf(suggestion.dueDate.orEmpty()) }
    var priority by rememberSaveable(suggestion.id) { mutableStateOf(suggestion.priority) }
    var group by rememberSaveable(suggestion.id) { mutableStateOf(suggestion.group.orEmpty()) }
    var reminder by rememberSaveable(suggestion.id) { mutableStateOf(suggestion.triggers.time.orEmpty()) }
    val initialLoc = suggestion.triggers.location
    var placeOn by rememberSaveable(suggestion.id) { mutableStateOf(initialLoc != null) }
    var placeZone by rememberSaveable(suggestion.id) { mutableStateOf(initialLoc?.location.orEmpty().ifBlank { "Home" }) }
    var placeEvent by rememberSaveable(suggestion.id) { mutableStateOf(if (initialLoc?.event == "leave") "leave" else "arrive") }
    var carOn by rememberSaveable(suggestion.id) { mutableStateOf(!suggestion.triggers.car.isNullOrBlank()) }
    var carEvent by rememberSaveable(suggestion.id) { mutableStateOf(if (suggestion.triggers.car == "leave") "leave" else "enter") }
    var wakeOn by rememberSaveable(suggestion.id) { mutableStateOf(suggestion.triggers.wake == true) }
    val context = LocalContext.current
    // The user's Dana zones (Home, Work, …); fall back to the last cached list.
    var zones by remember { mutableStateOf(Zones.cached(context)) }
    LaunchedEffect(Unit) { zones = Zones.fetch(context.applicationContext) }
    // Events: shown as "YYYY-MM-DD HH:MM", sent back as "YYYY-MM-DDTHH:MM:00".
    val toField = { iso: String? -> iso?.replace('T', ' ')?.take(16).orEmpty() }
    var start by rememberSaveable(suggestion.id) { mutableStateOf(toField(suggestion.start)) }
    var end by rememberSaveable(suggestion.id) { mutableStateOf(toField(suggestion.end)) }
    var place by rememberSaveable(suggestion.id) { mutableStateOf(suggestion.location.orEmpty()) }
    val stamp = Regex("""\d{4}-\d{2}-\d{2} \d{2}:\d{2}""")
    val dueValid = due.isBlank() || runCatching { LocalDate.parse(due) }.isSuccess
    val reminderValid = reminder.isBlank() || stamp.matches(reminder)
    val startValid = stamp.matches(start)
    val endValid = end.isBlank() || stamp.matches(end)

    Column(
        Modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Space.xl, vertical = Space.sm)
            .navigationBarsPadding()
            .imePadding()
    ) {
        MonoLabel(if (sourceBlock != null) "Review suggestion" else "Edit before creating", color = MaterialTheme.colorScheme.primary)
        Spacer(Modifier.height(4.dp))
        Text(
            when { isEvent -> "Calendar event"; isAlert -> "Security alert"; else -> "Reminder" },
            style = MaterialTheme.typography.headlineSmall
        )
        if (sourceBlock != null) {
            Spacer(Modifier.height(12.dp))
            sourceBlock()
        }
        Spacer(Modifier.height(16.dp))
        if (isAlert) {
            Text(suggestion.alertText ?: suggestion.title,
                style = MaterialTheme.typography.bodyLarge.copy(textDirection = TextDirection.Content))
        } else {
            OutlinedTextField(
                value = title, onValueChange = { title = it }, label = { Text("Title") },
                textStyle = MaterialTheme.typography.bodyLarge.copy(textDirection = TextDirection.Content),
                modifier = Modifier.fillMaxWidth(), minLines = 1, maxLines = 3
            )
            Spacer(Modifier.height(10.dp))
        }
        if (isEvent) {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(
                    value = start, onValueChange = { start = it.take(16) }, label = { Text("Starts") },
                    placeholder = { Text("YYYY-MM-DD HH:MM") }, isError = !startValid, singleLine = true, modifier = Modifier.weight(1f)
                )
                OutlinedTextField(
                    value = end, onValueChange = { end = it.take(16) }, label = { Text("Ends") },
                    placeholder = { Text("YYYY-MM-DD HH:MM") }, isError = !endValid, singleLine = true, modifier = Modifier.weight(1f)
                )
            }
            Spacer(Modifier.height(10.dp))
            OutlinedTextField(
                value = place, onValueChange = { place = it }, label = { Text("Where") }, singleLine = true,
                textStyle = MaterialTheme.typography.bodyLarge.copy(textDirection = TextDirection.Content),
                modifier = Modifier.fillMaxWidth()
            )
        } else if (!isAlert) {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(
                    value = due, onValueChange = { due = it.take(10) }, label = { Text("Due (YYYY-MM-DD)") },
                    isError = !dueValid, singleLine = true, modifier = Modifier.weight(1f)
                )
                OutlinedTextField(
                    value = reminder, onValueChange = { reminder = it.take(16) }, label = { Text("Remind at") },
                    placeholder = { Text("YYYY-MM-DD HH:MM") }, isError = !reminderValid, singleLine = true, modifier = Modifier.weight(1f)
                )
            }
            Spacer(Modifier.height(14.dp))
            MonoLabel("Priority")
            Spacer(Modifier.height(6.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf("low", "medium", "high").forEach { p ->
                    InkChip(p.replaceFirstChar { it.uppercase() }, selected = priority == p, onClick = { priority = p },
                        accent = if (p == "high") Danger else if (p == "medium") Warning else MaterialTheme.colorScheme.primary)
                }
            }
            if (groups.isNotEmpty()) {
                Spacer(Modifier.height(14.dp))
                MonoLabel("Group")
                Spacer(Modifier.height(6.dp))
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    groups.forEach { g -> InkChip(g, selected = group.equals(g, true), onClick = { group = g }) }
                }
            }
            Spacer(Modifier.height(18.dp))
            MonoLabel("Remind me")
            Spacer(Modifier.height(6.dp))
            val cal = Calendar.getInstance()
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = {
                    DatePickerDialog(context, { _, y, m, d ->
                        val timePart = reminder.takeIf { stamp.matches(it) }?.substring(11) ?: "09:00"
                        reminder = "%04d-%02d-%02d %s".format(y, m + 1, d, timePart)
                    }, cal.get(Calendar.YEAR), cal.get(Calendar.MONTH), cal.get(Calendar.DAY_OF_MONTH)).show()
                }) { Text("At a date") }
                OutlinedButton(onClick = {
                    TimePickerDialog(context, { _, h, min ->
                        val datePart = reminder.takeIf { stamp.matches(it) }?.substring(0, 10)
                            ?: java.time.LocalDate.now().toString()
                        reminder = "%s %02d:%02d".format(datePart, h, min)
                    }, cal.get(Calendar.HOUR_OF_DAY), cal.get(Calendar.MINUTE), true).show()
                }) { Text("At a time") }
                if (reminder.isNotBlank()) TextButton(onClick = { reminder = "" }) { Text("Clear") }
            }
            Spacer(Modifier.height(10.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("At a place", style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
                Switch(checked = placeOn, onCheckedChange = { placeOn = it })
            }
            if (placeOn) {
                val zoneChoices = (zones + placeZone).distinctBy { it.lowercase() }
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    InkChip("Arrive", selected = placeEvent == "arrive", onClick = { placeEvent = "arrive" })
                    InkChip("Leave", selected = placeEvent == "leave", onClick = { placeEvent = "leave" })
                }
                Spacer(Modifier.height(6.dp))
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    zoneChoices.forEach { z ->
                        InkChip(z, selected = placeZone.equals(z, true), onClick = { placeZone = z })
                    }
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("In the car", style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
                Switch(checked = carOn, onCheckedChange = { carOn = it })
            }
            if (carOn) {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    InkChip("Get in the car", selected = carEvent == "enter", onClick = { carEvent = "enter" })
                    InkChip("Leave the car", selected = carEvent == "leave", onClick = { carEvent = "leave" })
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("When I wake up", style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
                Switch(checked = wakeOn, onCheckedChange = { wakeOn = it })
            }
        }
        Spacer(Modifier.height(20.dp))
        TextButton(onClick = onAsk, modifier = Modifier.fillMaxWidth()) {
            Icon(Icons.AutoMirrored.Outlined.Chat, contentDescription = null, modifier = Modifier.size(18.dp))
            Spacer(Modifier.width(8.dp))
            Text(if (isAlert) "Ask Dana what to do" else "Ask Dana to change it instead")
        }
        Spacer(Modifier.height(4.dp))
        if (busy) {
            LinearProgressIndicator(Modifier.fillMaxWidth().padding(vertical = 18.dp))
        } else Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            if (onDismiss != null) {
                OutlinedButton(onClick = onDismiss, modifier = Modifier.weight(1f)) { Text(if (isAlert) "Got it" else "Dismiss") }
            } else {
                OutlinedButton(onClick = onCancel, modifier = Modifier.weight(1f)) { Text("Cancel") }
            }
            if (!isAlert) Button(
                onClick = {
                    if (isEvent) {
                        onCreate(SuggestionEdits.Event(
                            title = title.trim(),
                            start = start.trim().replace(' ', 'T') + ":00",
                            end = end.trim().ifEmpty { null }?.let { it.replace(' ', 'T') + ":00" },
                            location = place.trim().ifEmpty { null }
                        ))
                        return@Button
                    }
                    val triggers = Triggers(
                        time = reminder.trim().takeIf { reminder.isNotBlank() },
                        location = if (placeOn) LocationTrigger(placeZone, placeEvent) else null,
                        car = carEvent.takeIf { carOn },
                        wake = true.takeIf { wakeOn }
                    )
                    onCreate(SuggestionEdits.Task(title.trim(), due.trim().ifEmpty { null }, priority, group.ifBlank { null }, triggers))
                },
                enabled = title.isNotBlank() && (if (isEvent) startValid && endValid else dueValid && reminderValid),
                modifier = Modifier.weight(1f)
            ) { Text(if (isEvent) "Add to calendar" else "Create") }
        }
        Spacer(Modifier.height(12.dp))
    }
}
