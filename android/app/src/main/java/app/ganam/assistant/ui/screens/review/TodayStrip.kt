package app.ganam.assistant.ui.screens.review

import android.content.Context
import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import app.ganam.assistant.data.models.Briefing
import app.ganam.assistant.data.models.WeeklyReview
import app.ganam.assistant.ui.components.MonoLabel
import app.ganam.assistant.ui.screens.people.DirBullet
import app.ganam.assistant.ui.screens.people.DirText
import app.ganam.assistant.ui.theme.Space
import app.ganam.assistant.ui.viewmodels.MainViewModel
import java.time.LocalDate
import java.time.LocalTime

private const val HIDDEN_KEY = "briefing_hidden_date"

/**
 * Top of Reminders: today's wake briefing (until 14:00, can be put away for the
 * day) and a "Weekly review is ready" row while one is pending.
 */
@Composable
fun TodayStrip(viewModel: MainViewModel) {
    val c = viewModel.reviewController
    val briefing by c.briefing.collectAsState()
    val review by c.review.collectAsState()
    val context = LocalContext.current
    val prefs = remember { context.getSharedPreferences("GanamPrefs", Context.MODE_PRIVATE) }
    val today = LocalDate.now().toString()
    var hidden by remember { mutableStateOf(prefs.getString(HIDDEN_KEY, null) == today) }

    LaunchedEffect(Unit) { c.fetchBriefing(); c.fetch() }

    val b = briefing?.takeIf { !hidden && it.date == today && LocalTime.now().hour < 14 && it.headline.isNotBlank() }
    val pending = review?.takeIf { it.status == "pending" && it.items.isNotEmpty() }
    if (b != null || pending != null) Column(
        Modifier.fillMaxWidth().padding(horizontal = Space.screen, vertical = Space.xs),
        verticalArrangement = Arrangement.spacedBy(Space.sm)
    ) {
        if (pending != null) ReviewReady(pending) { viewModel.openReview() }
        if (b != null) BriefingCard(b) {
            prefs.edit().putString(HIDDEN_KEY, today).apply()
            hidden = true
        }
    }
}

@Composable
private fun ReviewReady(r: WeeklyReview, onOpen: () -> Unit) {
    val proposed = r.items.count { it.enabled && it.action != "keep" }
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(MaterialTheme.colorScheme.primaryContainer)
            .clickable(onClick = onOpen).padding(start = Space.lg, end = Space.md, top = Space.md, bottom = Space.md),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(Modifier.weight(1f)) {
            MonoLabel("Weekly review", color = MaterialTheme.colorScheme.primary)
            Text("${r.items.size} things to go through", style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.onPrimaryContainer)
            if (proposed > 0) Text("Dana suggests $proposed changes", style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onPrimaryContainer.copy(alpha = 0.8f))
        }
        Spacer(Modifier.width(Space.sm))
        Box(Modifier.size(40.dp).clip(CircleShape).background(MaterialTheme.colorScheme.primary), contentAlignment = Alignment.Center) {
            Icon(Icons.AutoMirrored.Filled.ArrowForward, "Start the review", Modifier.size(20.dp), tint = MaterialTheme.colorScheme.onPrimary)
        }
    }
}

@Composable
private fun BriefingCard(b: Briefing, onHide: () -> Unit) {
    var expanded by rememberSaveable { mutableStateOf(false) }
    val greeting = if (LocalTime.now().hour < 12) "Good morning" else "Today"
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(MaterialTheme.colorScheme.surfaceContainerLow)
            .clickable { expanded = !expanded }.animateContentSize()
            .padding(start = Space.lg, end = Space.xs, top = Space.xs, bottom = Space.md)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            MonoLabel(greeting, Modifier.weight(1f), color = MaterialTheme.colorScheme.primary)
            IconButton(onClick = onHide, modifier = Modifier.size(40.dp)) {
                Icon(Icons.Filled.Close, "Hide for today", Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        Column(Modifier.padding(end = Space.md)) {
            DirText(b.headline, MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.onSurface)
            val shown = if (expanded) b.points else b.points.take(2)
            if (shown.isNotEmpty()) Spacer(Modifier.height(4.dp))
            shown.forEach { DirBullet(it, MaterialTheme.colorScheme.onSurfaceVariant, MaterialTheme.typography.bodySmall) }
            val facts = listOfNotNull(
                b.nextEvent?.takeIf { it.isNotBlank() }?.let { "Next $it" },
                b.due.takeIf { it > 0 }?.let { "$it due" },
                b.needsYou.takeIf { it > 0 }?.let { "$it need you" },
            )
            if (b.points.size > 2) Text(if (expanded) "Show less" else "${b.points.size - 2} more",
                style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(start = 15.dp, top = 2.dp))
            // Wraps instead of truncating in the narrow middle pane.
            if (facts.isNotEmpty()) Text(facts.joinToString(" · ").uppercase(), style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = Space.sm))
        }
    }
}
