package io.github.ronganam.dana.ui.screens.review

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Event
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.models.ReviewItem
import io.github.ronganam.dana.data.models.WeeklyReview
import io.github.ronganam.dana.ui.adaptive.Pane
import io.github.ronganam.dana.ui.adaptive.PaneRow
import io.github.ronganam.dana.ui.adaptive.WindowTier
import io.github.ronganam.dana.ui.adaptive.sidePaneWidth
import io.github.ronganam.dana.ui.adaptive.windowTier
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.components.ScreenTopBar
import io.github.ronganam.dana.ui.screens.common.QuietEmptyState
import io.github.ronganam.dana.ui.screens.people.DirText
import io.github.ronganam.dana.ui.screens.people.Tag
import io.github.ronganam.dana.ui.theme.DanaSerif
import io.github.ronganam.dana.ui.theme.Space
import io.github.ronganam.dana.ui.theme.Warning
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import kotlinx.coroutines.launch
import java.time.DayOfWeek
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.time.temporal.IsoFields
import java.time.temporal.TemporalAdjusters
import java.util.Locale

private val SECTIONS = listOf("task" to "Reminders", "suggestion" to "Suggestions waiting", "person" to "People")

/** Short, plain choices (the server's action ids stay the same). */
private val CHOICE = mapOf(
    "reschedule" to "New date", "complete" to "Done", "move_group" to "Move", "delete" to "Delete", "keep" to "Leave it",
    "accept" to "Accept", "dismiss" to "Dismiss", "reach_out" to "Remind me",
)

/** Why an item is in the review (server `why` codes). */
private fun whyLabel(why: String): Pair<String, Boolean> = when (why) {
    "overdue" -> "Overdue" to true
    "snoozed_often" -> "Snoozed a lot" to true
    "stale" -> "Untouched for weeks" to false
    "old_suggestion" -> "Waiting on you" to false
    "drifting" -> "Been a while" to true
    else -> why.replace('_', ' ').replaceFirstChar { it.uppercase() } to false
}

private val NICE_DAY: DateTimeFormatter get() = DateTimeFormatter.ofPattern("EEE d MMM", Locale.getDefault())
private val RANGE_DAY: DateTimeFormatter get() = DateTimeFormatter.ofPattern("d MMM", Locale.getDefault())

private fun niceDate(iso: String?): String? = iso?.take(10)?.let { runCatching { NICE_DAY.format(LocalDate.parse(it)) }.getOrNull() }

/** "2026-W46" → "Week 46 · 9–15 Nov". */
private fun weekLabel(week: String?): String? {
    val m = Regex("""(\d{4})-W(\d{1,2})""").find(week ?: return null) ?: return week
    val (y, w) = m.destructured
    return runCatching {
        val monday = LocalDate.of(y.toInt(), 6, 1).with(IsoFields.WEEK_OF_WEEK_BASED_YEAR, w.toLong()).with(DayOfWeek.MONDAY)
        "Week ${w.toInt()} · ${monday.dayOfMonth}–${RANGE_DAY.format(monday.plusDays(6))}"
    }.getOrDefault("Week ${w.toInt()}")
}

/** Weekly review: go through what slipped, adjust each proposal, apply in one go. */
@Composable
fun ReviewScreen(viewModel: MainViewModel, onBack: () -> Unit) {
    val c = viewModel.reviewController
    val review by c.review.collectAsState()
    val items by c.items.collectAsState()
    val loading by c.loading.collectAsState()
    val busy by c.busy.collectAsState()
    val compact = windowTier() == WindowTier.COMPACT

    LaunchedEffect(Unit) { c.fetch() }

    val r = review
    val pending = r != null && r.status == "pending"
    val changes = items.count { it.enabled && it.action != "keep" }
    val edit: (String, (ReviewItem) -> ReviewItem) -> Unit = { ref, f -> c.edit(ref, f) }

    Column(Modifier.fillMaxSize()) {
        ScreenTopBar(
            title = "Weekly review",
            kicker = weekLabel(r?.week),
            showSettings = false,
            navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") } },
        )
        when {
            r == null && loading -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator(Modifier.size(28.dp), strokeWidth = 2.5.dp)
            }
            r == null || !pending -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.TopCenter) {
                QuietEmptyState(
                    modifier = Modifier.widthIn(max = 560.dp),
                    title = when (r?.status) { "applied" -> "Reviewed."; "dismissed" -> "Skipped this week."; else -> "Nothing to review." },
                    message = when (r?.status) {
                        "applied" -> "${r.applied ?: 0} changes applied" + ((r.failed ?: 0).takeIf { it > 0 }?.let { ", $it failed" } ?: "") +
                            ". The next review is ready Saturday evening."
                        else -> "Dana prepares one every Saturday evening: what slipped, what's waiting on you, and who you haven't talked to."
                    },
                    actionLabel = if (busy) "Preparing…" else "Review now",
                    onAction = { if (!busy) c.generate() },
                )
            }
            compact -> {
                Box(Modifier.weight(1f)) {
                    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(start = Space.screen, end = Space.screen, bottom = Space.xl)) {
                        item(key = "intro") { Intro(r, items, changes) }
                        reviewItems(items, r.groups, edit)
                    }
                }
                ApplyBar(changes, busy, onSkip = c::dismiss, onApply = { c.apply() })
            }
            else -> {
                val listState = rememberLazyListState()
                val scope = rememberCoroutineScope()
                // Index of each section header in the list, for the side pane's jump links.
                val headerIndex = remember(items) {
                    var i = 0
                    buildMap {
                        SECTIONS.forEach { (type, _) ->
                            val n = items.count { it.type == type }
                            if (n > 0) { put(type, i); i += 1 + n }
                        }
                    }
                }
                PaneRow(Modifier.weight(1f)) {
                    Pane(width = sidePaneWidth(), tinted = true) {
                        Column(Modifier.fillMaxSize()) {
                            Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = Space.screen + 4.dp)) {
                                Intro(r, items, changes)
                                Spacer(Modifier.height(Space.lg))
                                SECTIONS.forEach { (type, label) ->
                                    val n = items.count { it.type == type }
                                    if (n > 0) Row(
                                        Modifier.fillMaxWidth().heightIn(min = 44.dp).clip(RoundedCornerShape(10.dp))
                                            .clickable { headerIndex[type]?.let { scope.launch { listState.animateScrollToItem(it) } } }
                                            .padding(horizontal = 4.dp),
                                        verticalAlignment = Alignment.CenterVertically
                                    ) {
                                        Text(label, style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
                                        val picked = items.count { it.type == type && it.enabled && it.action != "keep" }
                                        MonoLabel(if (picked > 0) "$picked/$n" else "$n")
                                    }
                                }
                            }
                            ApplyBar(changes, busy, onSkip = c::dismiss, onApply = { c.apply() }, stacked = true)
                        }
                    }
                    Pane(divider = false) {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.TopCenter) {
                            LazyColumn(
                                Modifier.fillMaxHeight().widthIn(max = 760.dp), state = listState,
                                contentPadding = PaddingValues(start = Space.xl, end = Space.xl, bottom = Space.xl)
                            ) { reviewItems(items, r.groups, edit, firstHeaderTop = false) }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun Intro(r: WeeklyReview, items: List<ReviewItem>, changes: Int) {
    Column(Modifier.padding(top = Space.sm)) {
        DirText(r.headline, MaterialTheme.typography.headlineSmall.copy(fontFamily = DanaSerif, fontStyle = FontStyle.Italic),
            color = MaterialTheme.colorScheme.onSurface)
        r.summary.forEach {
            DirText(it, MaterialTheme.typography.bodyMedium, Modifier.padding(top = 4.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Spacer(Modifier.height(Space.md))
        MonoLabel(
            "${items.size} to go through · " + if (changes == 0) "no changes yet" else if (changes == 1) "1 change" else "$changes changes",
            color = MaterialTheme.colorScheme.primary
        )
    }
}

@Composable
private fun ApplyBar(changes: Int, busy: Boolean, onSkip: () -> Unit, onApply: () -> Unit, stacked: Boolean = false) {
    val apply: @Composable (Modifier) -> Unit = { m ->
        Button(onClick = onApply, enabled = !busy && changes > 0, modifier = m.heightIn(min = 48.dp)) {
            if (busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary)
            else Text(when (changes) { 0 -> "Nothing to apply"; 1 -> "Apply 1 change"; else -> "Apply $changes changes" })
        }
    }
    if (stacked) Column(Modifier.fillMaxWidth().padding(Space.screen), verticalArrangement = Arrangement.spacedBy(Space.xs)) {
        apply(Modifier.fillMaxWidth())
        TextButton(onClick = onSkip, enabled = !busy, modifier = Modifier.fillMaxWidth()) { Text("Skip this week") }
    } else Surface(color = MaterialTheme.colorScheme.surfaceContainer) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = Space.screen, vertical = Space.sm),
            horizontalArrangement = Arrangement.spacedBy(Space.md), verticalAlignment = Alignment.CenterVertically
        ) {
            TextButton(onClick = onSkip, enabled = !busy) { Text("Skip this week") }
            Spacer(Modifier.weight(1f))
            apply(Modifier)
        }
    }
}

private fun LazyListScope.reviewItems(
    items: List<ReviewItem>, groups: List<String>, edit: (String, (ReviewItem) -> ReviewItem) -> Unit, firstHeaderTop: Boolean = true,
) {
    if (items.isEmpty()) item(key = "none") {
        Text("Nothing slipped this week. 🎉", style = MaterialTheme.typography.bodyLarge, modifier = Modifier.padding(top = Space.xl))
    }
    var first = true
    SECTIONS.forEach { (type, label) ->
        val list = items.filter { it.type == type }
        if (list.isEmpty()) return@forEach
        val top = if (first && !firstHeaderTop) Space.lg else Space.xl
        first = false
        item(key = "h-$type") {
            Row(Modifier.fillMaxWidth().padding(top = top, bottom = Space.xs), verticalAlignment = Alignment.CenterVertically) {
                MonoLabel(label, Modifier.weight(1f), color = MaterialTheme.colorScheme.primary)
                MonoLabel(list.size.toString())
            }
        }
        list.forEachIndexed { i, it ->
            item(key = it.ref) {
                ReviewRow(it, groups) { f -> edit(it.ref, f) }
                if (i < list.lastIndex) Rule()
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3Api::class)
@Composable
private fun ReviewRow(item: ReviewItem, groups: List<String>, edit: ((ReviewItem) -> ReviewItem) -> Unit) {
    var picking by remember { mutableStateOf(false) }
    val leaving = !item.enabled || item.action == "keep"
    val choose: (String) -> Unit = { a ->
        edit {
            it.copy(
                action = a, enabled = a != "keep",
                dueDate = if ((a == "reschedule" || a == "reach_out") && it.dueDate.isNullOrBlank()) LocalDate.now().plusDays(1).toString() else it.dueDate,
                group = if (a == "move_group") it.group?.takeIf { g -> g in groups } ?: groups.firstOrNull() else it.group,
            )
        }
    }
    Column(Modifier.fillMaxWidth().animateContentSize().padding(vertical = Space.md)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            val (why, warn) = whyLabel(item.why)
            Tag(why, if (warn) Warning else MaterialTheme.colorScheme.onSurfaceVariant)
            item.group?.takeIf { it.isNotBlank() && item.type == "task" }?.let {
                Spacer(Modifier.width(Space.sm)); MonoLabel(it)
            }
        }
        Column(Modifier.alpha(if (leaving) 0.62f else 1f)) {
            DirText(item.title, MaterialTheme.typography.titleMedium, Modifier.padding(top = Space.sm), color = MaterialTheme.colorScheme.onSurface, maxLines = 3)
            val facts = item.facts.filter { it.isNotBlank() }
            if (facts.isNotEmpty()) Text(facts.joinToString(" · "), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 2.dp))
        }
        if (item.reason.isNotBlank()) {
            Row(Modifier.padding(top = Space.sm)) {
                Text("Dana: ", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary)
                Text(item.reason, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurface)
            }
        }
        FlowRow(Modifier.padding(top = Space.md), horizontalArrangement = Arrangement.spacedBy(Space.sm), verticalArrangement = Arrangement.spacedBy(Space.sm)) {
            ReviewItem.ACTIONS[item.type].orEmpty().filter { it != "move_group" || groups.isNotEmpty() }.forEach { a ->
                Choice(CHOICE[a] ?: a, selected = item.action == a, danger = a == "delete") { choose(a) }
            }
        }
        if (item.action == "reschedule" || item.action == "reach_out") {
            val today = LocalDate.now()
            val nextMonday = today.with(TemporalAdjusters.next(DayOfWeek.MONDAY))
            val picks = listOf("Tomorrow" to today.plusDays(1), "Mon" to nextMonday, "In a week" to today.plusWeeks(1)).distinctBy { it.second }
            val current = item.dueDate?.take(10)
            FlowRow(Modifier.padding(top = Space.sm), horizontalArrangement = Arrangement.spacedBy(Space.sm), verticalArrangement = Arrangement.spacedBy(Space.sm)) {
                picks.forEach { (label, d) ->
                    Choice(label, selected = current == d.toString(), subtle = true) { edit { it.copy(dueDate = d.toString()) } }
                }
                val custom = current != null && picks.none { it.second.toString() == current }
                Choice(if (custom) niceDate(current) ?: "Pick a date" else "Pick a date…", selected = custom, subtle = true, icon = true) { picking = true }
            }
            niceDate(current)?.let {
                MonoLabel((if (item.action == "reach_out") "Reminder on " else "Moves to ") + it, Modifier.padding(top = Space.sm))
            }
        }
        if (item.action == "move_group" && groups.isNotEmpty()) {
            FlowRow(Modifier.padding(top = Space.sm), horizontalArrangement = Arrangement.spacedBy(Space.sm), verticalArrangement = Arrangement.spacedBy(Space.sm)) {
                groups.forEach { g -> Choice(g, selected = item.group == g, subtle = true) { edit { it.copy(group = g) } } }
            }
        }
    }

    if (picking) {
        val initial = runCatching { LocalDate.parse(item.dueDate?.take(10)) }.getOrNull() ?: LocalDate.now().plusDays(1)
        val state = rememberDatePickerState(
            initialSelectedDateMillis = initial.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli(),
            selectableDates = object : SelectableDates {
                override fun isSelectableDate(utcTimeMillis: Long) =
                    !Instant.ofEpochMilli(utcTimeMillis).atZone(ZoneOffset.UTC).toLocalDate().isBefore(LocalDate.now().plusDays(1))
            }
        )
        DatePickerDialog(
            onDismissRequest = { picking = false },
            confirmButton = {
                TextButton(onClick = {
                    state.selectedDateMillis?.let { ms ->
                        val d = Instant.ofEpochMilli(ms).atZone(ZoneOffset.UTC).toLocalDate().toString()
                        edit { it.copy(dueDate = d) }
                    }
                    picking = false
                }) { Text("OK") }
            },
            dismissButton = { TextButton(onClick = { picking = false }) { Text("Cancel") } }
        ) { DatePicker(state) }
    }
}

/** One choice pill. Primary choices fill with the accent; date/group picks use a quieter outline. */
@Composable
private fun Choice(label: String, selected: Boolean, subtle: Boolean = false, danger: Boolean = false, icon: Boolean = false, onClick: () -> Unit) {
    val accent = if (danger) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary
    val bg = when {
        selected && !subtle -> accent.copy(alpha = 0.18f)
        selected -> MaterialTheme.colorScheme.surfaceContainerHighest
        else -> Color.Transparent
    }
    val border = if (selected) accent else MaterialTheme.colorScheme.outlineVariant
    Row(
        Modifier.heightIn(min = 36.dp).clip(RoundedCornerShape(10.dp)).background(bg)
            .border(1.dp, border, RoundedCornerShape(10.dp)).clickable(onClick = onClick)
            .padding(horizontal = 12.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        if (selected && !subtle) { Icon(Icons.Filled.Check, null, Modifier.size(16.dp), tint = accent); Spacer(Modifier.width(6.dp)) }
        if (icon) { Icon(Icons.Filled.Event, null, Modifier.size(16.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant); Spacer(Modifier.width(6.dp)) }
        Text(label, style = MaterialTheme.typography.labelLarge,
            color = if (selected) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
