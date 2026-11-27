package io.github.ronganam.dana.ui.screens.watching

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.watches.DryRun
import io.github.ronganam.dana.data.watches.Watch
import io.github.ronganam.dana.data.watches.circuitOf
import io.github.ronganam.dana.data.watches.whenWords
import io.github.ronganam.dana.ui.components.circuit.GroupedSection
import io.github.ronganam.dana.ui.components.circuit.SectionHeader
import io.github.ronganam.dana.ui.theme.Space
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.theme.tabular
import io.github.ronganam.dana.ui.viewmodels.NewWatchPhase
import io.github.ronganam.dana.ui.viewmodels.NewWatchState
import io.github.ronganam.dana.ui.viewmodels.WatchingController
import java.time.ZonedDateTime

/**
 * New watch: the owner's words, then the circuit Dana made of them (the
 * watch exists, off), what its rule would have let through over the last 14
 * days, and Turn on / Not now.
 */
@Composable
fun NewWatchPage(controller: WatchingController, showBack: Boolean, onBack: () -> Unit) {
    val state by controller.newWatch.collectAsState()
    val s = state ?: NewWatchState()
    val c = circuitColors
    val made = (s.phase as? NewWatchPhase.Made)?.watch

    Column(Modifier.fillMaxSize().imePadding()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 4.dp).heightIn(min = 56.dp), verticalAlignment = Alignment.CenterVertically) {
            if (showBack) IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Back", tint = c.ink) }
        }
        Column(
            Modifier
                .weight(1f)
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = Space.screen)
        ) {
            Text(if (made != null) "${made.title}, off" else "New watch", style = MaterialTheme.typography.headlineLarge, color = c.ink, modifier = Modifier.semantics { heading() })
            if (made == null) {
                Text("Say what to watch for and what Dana should do.", style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, modifier = Modifier.padding(top = 4.dp))
                WordsField(s.text, enabled = s.phase !is NewWatchPhase.Compiling, onChange = controller::setNewText, modifier = Modifier.padding(top = 20.dp))
                when (val p = s.phase) {
                    NewWatchPhase.Compiling -> Row(Modifier.padding(top = 20.dp), verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp, color = c.accent)
                        Text("Dana is making the rule…", style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, modifier = Modifier.padding(start = 12.dp))
                    }
                    is NewWatchPhase.Refused -> Column(Modifier.padding(top = 20.dp)) {
                        Text("Dana couldn't make a watch of that.", style = MaterialTheme.typography.titleMedium, color = c.ink)
                        p.errors.take(3).forEach { Text(it, style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, modifier = Modifier.padding(top = 4.dp)) }
                        Text("Try saying it another way.", style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, modifier = Modifier.padding(top = 8.dp))
                    }
                    else -> Examples { controller.setNewText(it) }
                }
            } else {
                Text(
                    made.request,
                    style = MaterialTheme.typography.bodyLarge.copy(textDirection = TextDirection.Content),
                    color = c.inkSoft,
                    modifier = Modifier.padding(top = 8.dp)
                )
                MadeCircuit(made, Modifier.padding(top = 20.dp))
                DryRunSection(made, s, Modifier.padding(top = 28.dp))
            }
            Spacer(Modifier.height(24.dp))
        }
        // The buttons at the bottom of the page.
        Row(Modifier.fillMaxWidth().padding(horizontal = Space.screen, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Spacer(Modifier.weight(1f))
            if (made != null) {
                TextButton(onClick = controller::leaveNewOff, modifier = Modifier.heightIn(min = 48.dp)) { Text("Not now", style = MaterialTheme.typography.titleMedium, color = c.accent) }
                Spacer(Modifier.width(8.dp))
                Button(
                    onClick = controller::turnOnNew,
                    colors = ButtonDefaults.buttonColors(containerColor = c.accentFilled, contentColor = c.onAccentFilled),
                    modifier = Modifier.heightIn(min = 48.dp)
                ) { Text("Turn on", style = MaterialTheme.typography.titleMedium) }
            } else {
                Button(
                    onClick = controller::compileNew,
                    enabled = s.text.isNotBlank() && s.phase !is NewWatchPhase.Compiling,
                    colors = ButtonDefaults.buttonColors(containerColor = c.accentFilled, contentColor = c.onAccentFilled),
                    modifier = Modifier.heightIn(min = 48.dp)
                ) { Text("Make the rule", style = MaterialTheme.typography.titleMedium) }
            }
        }
    }
}

@Composable
private fun WordsField(text: String, enabled: Boolean, onChange: (String) -> Unit, modifier: Modifier = Modifier) {
    val c = circuitColors
    Box(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(24.dp))
            .background(MaterialTheme.colorScheme.surfaceContainerHigh)
            .heightIn(min = 120.dp)
            .padding(20.dp)
    ) {
        if (text.isEmpty()) Text("When the laundry is done, tell me", style = MaterialTheme.typography.bodyLarge, color = c.mute)
        BasicTextField(
            value = text,
            onValueChange = onChange,
            enabled = enabled,
            textStyle = MaterialTheme.typography.bodyLarge.copy(color = c.ink, textDirection = TextDirection.Content),
            cursorBrush = SolidColor(c.accent),
            modifier = Modifier.fillMaxWidth()
        )
    }
}

private val EXAMPLES = listOf(
    "When a courier asks when to deliver, reply with a time I'm free. Ask me first.",
    "When a bill comes by email, add a reminder before it's due.",
    "If my card is charged over 500, tell me.",
)

@Composable
private fun Examples(onPick: (String) -> Unit) {
    val c = circuitColors
    Column(Modifier.padding(top = 24.dp)) {
        SectionHeader("For example")
        GroupedSection {
            EXAMPLES.forEach { e ->
                item {
                    Text(
                        e, style = MaterialTheme.typography.bodyLarge, color = c.ink,
                        modifier = Modifier.fillMaxWidth().clickable { onPick(e) }.heightIn(min = 56.dp).padding(horizontal = 16.dp, vertical = 14.dp)
                    )
                }
            }
        }
    }
}

/** The circuit Dana made, large, and what its action does in a sentence. */
@Composable
private fun MadeCircuit(w: Watch, modifier: Modifier = Modifier) {
    val c = circuitColors
    val circuit = remember(w.spec) { circuitOf(w.parsed) }
    val action = w.parsed.onMatch.firstOrNull()
    val note = when {
        action == null -> null
        action.type == "agent" && action.autonomy != "act_and_tell" -> "Dana asks you before it sends or changes anything."
        action.type == "agent" -> "Dana acts, then tells you."
        action.type == "notify" -> "Dana tells you. Nothing is sent."
        action.type == "create_task" -> "Adds it to your reminders. Nothing is sent."
        else -> null
    }
    Column(modifier) {
        SectionHeader("Dana made this rule")
        GroupedSection {
            item {
                Column(Modifier.fillMaxWidth().padding(vertical = 20.dp, horizontal = 8.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                    LargeCircuit(circuit, circuitNodes(circuit, WatchLive.Idle), null)
                    if (note != null) Text(note, style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, modifier = Modifier.fillMaxWidth().padding(top = 16.dp, start = 8.dp, end = 8.dp))
                    w.parsed.unsupported?.let { Text("What it can't do: $it", style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, modifier = Modifier.fillMaxWidth().padding(top = 8.dp, start = 8.dp, end = 8.dp)) }
                }
            }
        }
    }
}

/** "Tried on the last 14 days": how many past events the rule lets through, and the latest of them. */
@Composable
private fun DryRunSection(w: Watch, s: NewWatchState, modifier: Modifier = Modifier) {
    val c = circuitColors
    val dry: DryRun? = s.dryRun
    val ai = w.parsed.triggers.any { !it.semantic.isNullOrBlank() }
    Column(modifier) {
        SectionHeader("Tried on the last ${WatchingController.DRY_RUN_DAYS} days")
        when {
            s.dryRunning -> Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = c.accent)
                Text("Looking at your past events…", style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, modifier = Modifier.padding(start = 12.dp))
            }
            s.dryFailed -> Text("Couldn't try it now.", style = MaterialTheme.typography.bodyMedium, color = c.inkSoft)
            dry == null || dry.triggers.isEmpty() -> Text("It runs on time, so there is nothing to try it on.", style = MaterialTheme.typography.bodyMedium, color = c.inkSoft)
            else -> {
                val matches = dry.triggers.values.sumOf { it.matches }
                val perDay = dry.triggers.values.sumOf { it.perDay }
                val line = when {
                    matches == 0 -> "Nothing in the last ${WatchingController.DRY_RUN_DAYS} days got past the rule."
                    ai -> "The rule let $matches through (about ${fmt(perDay)} a day). The AI check would look at each of them."
                    else -> "It would have fired $matches ${if (matches == 1) "time" else "times"} (about ${fmt(perDay)} a day)."
                }
                Text(line, style = MaterialTheme.typography.bodyLarge, color = c.ink)
                if (dry.triggers.values.any { it.overBudget }) Text(
                    "That is more than the AI check should see. Add who it is from or where.",
                    style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, modifier = Modifier.padding(top = 4.dp)
                )
                val samples = dry.triggers.values.flatMap { it.sample }.sortedByDescending { it.atMs ?: 0 }
                if (samples.isNotEmpty()) {
                    var all by rememberSaveable { mutableStateOf(false) }
                    val now = ZonedDateTime.now()
                    GroupedSection(Modifier.padding(top = 12.dp)) {
                        (if (all) samples else samples.take(3)).forEach { e ->
                            item {
                                Row(Modifier.fillMaxWidth().heightIn(min = 52.dp).padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                                    Text(e.summary, style = MaterialTheme.typography.bodyLarge, color = c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                                    e.atMs?.let { Text(whenWords(it, now), style = MaterialTheme.typography.bodyMedium.tabular(), color = c.inkSoft, modifier = Modifier.padding(start = 12.dp)) }
                                }
                            }
                        }
                    }
                    if (samples.size > 3) Text(
                        if (all) "Show fewer" else "See the latest ${samples.size}",
                        style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold), color = c.accent,
                        modifier = Modifier.padding(top = 4.dp).clip(RoundedCornerShape(8.dp)).clickable { all = !all }.padding(vertical = 12.dp, horizontal = 4.dp)
                    )
                }
            }
        }
    }
}

private fun fmt(d: Double): String = if (d >= 10 || d == d.toLong().toDouble()) d.toLong().toString() else "%.1f".format(d)
