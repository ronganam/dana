package io.github.ronganam.dana.ui.screens.people

import android.content.Intent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.CallMade
import androidx.compose.material.icons.automirrored.filled.CallMissed
import androidx.compose.material.icons.automirrored.filled.CallReceived
import androidx.compose.material.icons.automirrored.outlined.Chat
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Lightbulb
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.PushPin
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material.icons.outlined.Circle
import androidx.compose.material.icons.outlined.PushPin
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.models.Person
import io.github.ronganam.dana.data.models.PersonDetail
import io.github.ronganam.dana.ui.adaptive.asidePaneWidth
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.screens.common.auto
import io.github.ronganam.dana.ui.theme.Space
import io.github.ronganam.dana.ui.theme.Warning
import androidx.core.net.toUri

/** Max reading width so the page doesn't stretch on a tablet or the TV. */
private val PAGE_MAX = 720.dp

/** From this pane width the page splits into story | aside (the Fold's inner screen gives ~730dp). */
private val TWO_COLUMN_MIN = 600.dp

@Composable
fun PersonPage(
    detail: PersonDetail?, loading: Boolean, briefRefreshing: Boolean,
    onBack: (() -> Unit)?, onPin: (Person) -> Unit, onRefreshBrief: (String) -> Unit, onAsk: (String) -> Unit,
    onOpenCall: (String) -> Unit, onCall: (String) -> Unit, onCompleteTask: (String) -> Unit,
    onSave: (String, String, String, String, String, List<String>) -> Unit, onHide: (String) -> Unit,
) {
    val context = LocalContext.current
    var editing by remember { mutableStateOf(false) }
    var menu by remember { mutableStateOf(false) }
    val p = detail?.person

    val personActions: @Composable (Person) -> Unit = { person ->
        IconButton(onClick = { onPin(person) }) {
            Icon(if (person.pinned) Icons.Filled.PushPin else Icons.Outlined.PushPin, if (person.pinned) "Unpin" else "Pin to top",
                tint = if (person.pinned) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Box {
            IconButton(onClick = { menu = true }) { Icon(Icons.Filled.MoreVert, "More", tint = MaterialTheme.colorScheme.onSurfaceVariant) }
            DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                DropdownMenuItem(text = { Text("Edit details") }, leadingIcon = { Icon(Icons.Filled.Edit, null) }, onClick = { menu = false; editing = true })
                DropdownMenuItem(text = { Text("Hide from People") }, leadingIcon = { Icon(Icons.Filled.VisibilityOff, null) }, onClick = { menu = false; onHide(person.id) })
            }
        }
    }

    Column(Modifier.fillMaxSize()) {
        // Cover screen: a slim bar with back + actions. Inner screen: actions sit in the name row (saves height in landscape).
        if (onBack != null) Row(
            Modifier.fillMaxWidth().statusBarsPadding().height(52.dp).padding(horizontal = 4.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back to People") }
            Spacer(Modifier.weight(1f))
            if (p != null) personActions(p)
        }

        if (p == null) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                if (loading) CircularProgressIndicator(Modifier.size(28.dp), strokeWidth = 2.5.dp)
                else Text("Couldn't load this person.", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        } else BoxWithConstraints(Modifier.fillMaxSize(), contentAlignment = Alignment.TopCenter) {
            val whatsApp: (String) -> Unit = { ph ->
                runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, "https://wa.me/$ph".toUri()).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
            }
            val head: LazyListScope.() -> Unit = {
                item(key = "hero") { Hero(p, actions = if (onBack == null) ({ personActions(p) }) else null) }
                item(key = "actions") {
                    QuickActions(phone = p.phones.firstOrNull(), askLabel = "Ask Dana", onCall = onCall, onWhatsApp = whatsApp, onAsk = { onAsk(p.id) })
                }
            }
            val notes: LazyListScope.() -> Unit = {
                if (p.notes.isNotBlank()) item(key = "notes") {
                    SectionTitle("Notes")
                    DirText(p.notes, MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurface)
                }
            }
            if (maxWidth >= TWO_COLUMN_MIN) {
                // Inner screen: story on the left (summary + history), the facts and to-dos in a tinted aside.
                Row(Modifier.fillMaxSize()) {
                    LazyColumn(Modifier.weight(1f).fillMaxHeight(), contentPadding = PaddingValues(start = Space.xl, end = Space.xl, bottom = 32.dp)) {
                        head()
                        briefSection(detail, briefRefreshing) { onRefreshBrief(p.id) }
                        historySection(detail, onOpenCall)
                    }
                    LazyColumn(
                        Modifier.width(asidePaneWidth()).fillMaxHeight().background(MaterialTheme.colorScheme.surfaceContainerLow),
                        contentPadding = PaddingValues(start = Space.lg + 4.dp, end = Space.lg + 4.dp, top = Space.sm, bottom = 32.dp)
                    ) {
                        item(key = "facts") { Facts(p, stacked = true) }
                        remindersSection(detail, onCompleteTask)
                        notes()
                    }
                }
            } else LazyColumn(
                Modifier.fillMaxHeight().widthIn(max = PAGE_MAX),
                contentPadding = PaddingValues(start = Space.screen + 4.dp, end = Space.screen + 4.dp, bottom = 32.dp)
            ) {
                head()
                item(key = "facts") { Facts(p, stacked = false) }
                briefSection(detail, briefRefreshing) { onRefreshBrief(p.id) }
                remindersSection(detail, onCompleteTask)
                notes()
                historySection(detail, onOpenCall)
            }
        }
    }

    if (editing && p != null) EditPersonDialog(p, onDismiss = { editing = false }) { name, rel, notes, bday, aliases ->
        editing = false
        onSave(p.id, name, rel, notes, bday, aliases)
    }
}

@Composable
private fun Hero(p: Person, actions: (@Composable () -> Unit)?) {
    Row(Modifier.fillMaxWidth().padding(top = if (actions != null) Space.lg else 4.dp, bottom = Space.lg), verticalAlignment = Alignment.CenterVertically) {
        PersonAvatar(p, size = 64.dp)
        Spacer(Modifier.width(Space.lg))
        Column(Modifier.weight(1f)) {
            Text(p.name, style = MaterialTheme.typography.headlineMedium.auto(), color = MaterialTheme.colorScheme.onSurface,
                maxLines = 2, overflow = TextOverflow.Ellipsis)
            val sub = listOfNotNull(p.relation.takeIf { it.isNotBlank() }, p.phones.firstOrNull()?.let(::prettyPhone)).joinToString(" · ")
            if (sub.isNotBlank()) Text(sub, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
        }
        if (actions != null) Row(Modifier.offset(x = 8.dp)) { actions() }
    }
}

@Composable
private fun QuickActions(phone: String?, askLabel: String, onCall: (String) -> Unit, onWhatsApp: (String) -> Unit, onAsk: () -> Unit) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(Space.sm)) {
        if (phone != null) {
            ActionTile(Icons.Filled.Call, "Call", Modifier.weight(1f)) { onCall(phone) }
            ActionTile(Icons.AutoMirrored.Outlined.Chat, "WhatsApp", Modifier.weight(1f)) { onWhatsApp(phone) }
        }
        ActionTile(Icons.Filled.AutoAwesome, askLabel, Modifier.weight(1f), accent = true, onClick = onAsk)
    }
}

@Composable
private fun ActionTile(icon: ImageVector, label: String, modifier: Modifier, accent: Boolean = false, onClick: () -> Unit) {
    Column(
        modifier.clip(RoundedCornerShape(14.dp))
            .background(if (accent) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceContainerHigh)
            .clickable(onClick = onClick).padding(vertical = 12.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        val tint = if (accent) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurface
        Icon(icon, null, Modifier.size(22.dp), tint = if (accent) MaterialTheme.colorScheme.primary else tint)
        Spacer(Modifier.height(6.dp))
        Text(label, style = MaterialTheme.typography.labelLarge, color = tint, maxLines = 1)
    }
}

@Composable
private fun Facts(p: Person, stacked: Boolean) {
    val s = p.stats
    val last = when {
        s.lastContactAt == null -> "Never"
        else -> shortWhen(s.lastContactAt) + when (s.lastContactKind) { "call" -> " · call"; "message" -> " · message"; else -> "" }
    }
    val together = listOfNotNull(
        s.calls.takeIf { it > 0 }?.let { if (it == 1) "1 call" else "$it calls" },
        s.messages.takeIf { it > 0 }?.let { if (it == 1) "1 chat" else "$it chats" },
    ).joinToString(" · ").ifBlank { "—" }
    Column(Modifier.padding(top = if (stacked) Space.md else Space.lg)) {
        if (stacked) {
            MonoLabel("At a glance", color = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(top = Space.md, bottom = Space.sm))
            Column(Modifier.padding(bottom = Space.md), verticalArrangement = Arrangement.spacedBy(Space.md)) {
                Fact("Last in touch", last, Modifier.fillMaxWidth())
                Fact("Rhythm", rhythmLabel(s.typicalGapDays) ?: "—", Modifier.fillMaxWidth())
                Fact("Together", together, Modifier.fillMaxWidth())
            }
        } else {
            Rule()
            Row(Modifier.fillMaxWidth().padding(vertical = Space.md), horizontalArrangement = Arrangement.spacedBy(Space.md)) {
                Fact("Last in touch", last, Modifier.weight(1f))
                Fact("Rhythm", rhythmLabel(s.typicalGapDays) ?: "—", Modifier.weight(1f))
                Fact("Together", together, Modifier.weight(1f))
            }
        }
        val notes = buildList {
            if (s.drifting) add((s.daysSince?.let { "Quiet for $it days" } ?: "Been a while") + (rhythmLabel(s.typicalGapDays)?.let { " — usually ${it.lowercase()}" } ?: "") to Warning)
            p.birthdayInDays?.takeIf { it in 0..30 }?.let { add((if (it == 0) "Birthday today 🎂" else "Birthday in $it days 🎂") to Color.Unspecified) }
                ?: p.birthday.takeIf { it.isNotBlank() }?.let { add("Birthday ${it}" to Color.Unspecified) }
        }
        notes.forEach { (text, color) ->
            Row(Modifier.padding(bottom = Space.sm), verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(6.dp).clip(CircleShape).background(if (color == Color.Unspecified) MaterialTheme.colorScheme.primary else color))
                Spacer(Modifier.width(8.dp))
                Text(text, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurface)
            }
        }
        if (!stacked) Rule()
    }
}

@Composable
private fun Fact(label: String, value: String, modifier: Modifier) {
    Column(modifier) {
        MonoLabel(label)
        Spacer(Modifier.height(2.dp))
        Text(value, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurface, maxLines = 2)
    }
}

@Composable
private fun SectionTitle(title: String, trailing: @Composable RowScope.() -> Unit = {}) {
    Row(Modifier.fillMaxWidth().padding(top = Space.xl, bottom = Space.xs).heightIn(min = 32.dp), verticalAlignment = Alignment.CenterVertically) {
        MonoLabel(title, Modifier.weight(1f), color = MaterialTheme.colorScheme.primary)
        trailing()
    }
}

private fun LazyListScope.briefSection(detail: PersonDetail, refreshing: Boolean, onRefresh: () -> Unit) {
    val brief = detail.person.brief
    item(key = "brief") {
        SectionTitle("What's going on") {
            val updated = brief?.updatedAt?.let(::shortWhen)
            if (detail.briefStale) MonoLabel("Outdated", color = Warning)
            else if (!updated.isNullOrBlank()) MonoLabel("Updated $updated")
            IconButton(onClick = onRefresh, enabled = !refreshing, modifier = Modifier.size(36.dp)) {
                if (refreshing) CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                else Icon(Icons.Filled.Refresh, "Rewrite the summary", Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        if (brief == null || (brief.points.isEmpty() && brief.open.isEmpty())) {
            Text("No summary yet. Dana writes one after calls or messages with them.",
                style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        } else {
            brief.points.forEach { DirBullet(it, MaterialTheme.colorScheme.onSurfaceVariant) }
        }
    }
    if (brief != null && brief.open.isNotEmpty()) item(key = "open") {
        Column(
            Modifier.padding(top = Space.md).fillMaxWidth().clip(RoundedCornerShape(14.dp))
                .background(MaterialTheme.colorScheme.surfaceContainerLow).padding(horizontal = Space.lg, vertical = Space.md)
        ) {
            MonoLabel("Open between you")
            Spacer(Modifier.height(2.dp))
            brief.open.forEach { DirBullet(it, MaterialTheme.colorScheme.primary) }
        }
    }
}

private fun LazyListScope.remindersSection(detail: PersonDetail, onComplete: (String) -> Unit) {
    if (detail.tasks.isEmpty() && detail.suggestions.isEmpty()) return
    item(key = "rem-h") {
        SectionTitle("Reminders") { MonoLabel((detail.tasks.size + detail.suggestions.size).toString()) }
    }
    items(detail.tasks.size, key = { "t" + detail.tasks[it].id }) { i ->
        val t = detail.tasks[i]
        var done by remember(t.id) { mutableStateOf(false) }
        Row(Modifier.fillMaxWidth().heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = { done = true; onComplete(t.id) }, enabled = !done, modifier = Modifier.size(40.dp).offset(x = (-8).dp)) {
                Icon(if (done) Icons.Filled.CheckCircle else Icons.Outlined.Circle,
                    if (done) "Done" else "Mark done", Modifier.size(22.dp),
                    tint = if (done) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Column(Modifier.weight(1f)) {
                DirText(t.title, MaterialTheme.typography.bodyMedium, color = if (done) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface, maxLines = 2)
            }
            t.dueDate?.takeIf { it.length >= 10 }?.let { Spacer(Modifier.width(Space.sm)); MonoLabel(it.substring(8, 10) + "/" + it.substring(5, 7)) }
        }
    }
    items(detail.suggestions.size, key = { "s" + detail.suggestions[it].id }) { i ->
        val s = detail.suggestions[i]
        Row(Modifier.fillMaxWidth().heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(40.dp).offset(x = (-8).dp), contentAlignment = Alignment.Center) {
                Icon(Icons.Filled.Lightbulb, null, Modifier.size(20.dp), tint = MaterialTheme.colorScheme.primary)
            }
            Column(Modifier.weight(1f)) { DirText(s.title, MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurface, maxLines = 2) }
            Spacer(Modifier.width(Space.sm))
            Tag("Suggested", MaterialTheme.colorScheme.primary)
        }
    }
}

private fun LazyListScope.historySection(detail: PersonDetail, onOpenCall: (String) -> Unit) {
    val days = mergeHistory(detail.timeline)
    item(key = "hist-h") { SectionTitle("History") }
    if (days.isEmpty()) item(key = "hist-empty") {
        Text("Nothing yet.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
    days.forEach { (day, entries) ->
        item(key = "d-$day") {
            Text(day?.let(::dayHeader) ?: "Earlier", style = MaterialTheme.typography.titleSmall,
                color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.padding(top = Space.md, bottom = Space.xs))
        }
        items(entries.size, key = { entries[it].key }) { i ->
            HistoryRow(entries[i], last = i == entries.lastIndex) { if (entries[i].event.type == "call") onOpenCall(entries[i].event.id) }
        }
    }
}

@Composable
private fun HistoryRow(h: HistoryEntry, last: Boolean, onClick: () -> Unit) {
    val e = h.event
    val isCall = e.type == "call"
    val line = MaterialTheme.colorScheme.outlineVariant
    Row(
        Modifier.fillMaxWidth().height(IntrinsicSize.Min).clip(RoundedCornerShape(10.dp))
            .clickable(enabled = isCall, onClick = onClick)
    ) {
        // Rail: icon bubble with a thin line down to the next entry of the day.
        Column(Modifier.width(36.dp).fillMaxHeight(), horizontalAlignment = Alignment.CenterHorizontally) {
            Box(Modifier.padding(top = 8.dp).size(28.dp).clip(CircleShape).background(MaterialTheme.colorScheme.surfaceContainerHigh), contentAlignment = Alignment.Center) {
                val icon = when {
                    !isCall -> Icons.AutoMirrored.Outlined.Chat
                    e.direction == "outgoing" -> Icons.AutoMirrored.Filled.CallMade
                    e.direction == "missed" -> Icons.AutoMirrored.Filled.CallMissed
                    else -> Icons.AutoMirrored.Filled.CallReceived
                }
                Icon(icon, null, Modifier.size(15.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (!last) Box(Modifier.padding(top = 4.dp).width(1.dp).weight(1f).background(line))
        }
        Spacer(Modifier.width(Space.sm))
        Column(Modifier.weight(1f).padding(top = 10.dp, bottom = Space.md)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                val kind = if (isCall) {
                    val mins = (e.durationSeconds + 30) / 60
                    val dir = when (e.direction) { "outgoing" -> "Outgoing call"; "missed" -> "Missed call"; else -> "Incoming call" }
                    dir + if (e.durationSeconds > 0) " · " + (if (mins > 0) "$mins min" else "${e.durationSeconds} s") else ""
                } else e.app.ifBlank { "Messages" } + if (h.count > 1) " · ${h.count} messages" else ""
                Text(kind, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                Spacer(Modifier.width(Space.sm))
                MonoLabel(timeOf(e.at))
            }
            if (isCall) {
                if (e.title.isNotBlank()) DirText(e.title, MaterialTheme.typography.titleSmall, Modifier.padding(top = 2.dp), color = MaterialTheme.colorScheme.onSurface)
                if (e.summary.isNotBlank()) DirText(e.summary, MaterialTheme.typography.bodySmall, Modifier.padding(top = 2.dp),
                    color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 3)
            } else {
                // The chat name on its own line: mixing it into the Latin label scrambles the bidi order.
                if (h.chat.isNotBlank()) DirText(h.chat, MaterialTheme.typography.titleSmall, Modifier.padding(top = 2.dp), color = MaterialTheme.colorScheme.onSurface, maxLines = 1)
                h.texts.map(::tidyMessage).filter { it.isNotBlank() }.take(2).forEach {
                    DirText(it, MaterialTheme.typography.bodySmall, Modifier.padding(top = 2.dp), color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.8f), maxLines = 2)
                }
            }
        }
    }
}

@Composable
private fun EditPersonDialog(p: Person, onDismiss: () -> Unit, onSave: (String, String, String, String, List<String>) -> Unit) {
    var name by rememberSaveable { mutableStateOf(p.name) }
    var relation by rememberSaveable { mutableStateOf(p.relation) }
    var birthday by rememberSaveable { mutableStateOf(p.birthday) }
    var aliases by rememberSaveable { mutableStateOf(p.aliases.joinToString(", ")) }
    var notes by rememberSaveable { mutableStateOf(p.notes) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Edit details") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(Space.sm)) {
                OutlinedTextField(name, { name = it }, label = { Text("Name") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(relation, { relation = it }, label = { Text("Relation") }, placeholder = { Text("Mom, colleague, plumber…") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(birthday, { birthday = it }, label = { Text("Birthday") }, placeholder = { Text("MM-DD or YYYY-MM-DD") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(aliases, { aliases = it }, label = { Text("Other names") }, supportingText = { Text("Comma separated — helps Dana match messages") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(notes, { notes = it }, label = { Text("Notes") }, minLines = 2, maxLines = 5, modifier = Modifier.fillMaxWidth())
            }
        },
        confirmButton = {
            TextButton(enabled = name.isNotBlank(), onClick = {
                onSave(name.trim(), relation.trim(), notes.trim(), birthday.trim(), aliases.split(',').map { it.trim() }.filter { it.isNotEmpty() })
            }) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}

private val URL = Regex("""https?://\S+|www\.\S+""")

/** Drop raw links and stray link glyphs so message previews read as text. */
private fun tidyMessage(t: String): String =
    t.replace(URL, "").replace(Regex("""\(\s*\)"""), "").replace(Regex("""\s{2,}"""), " ").trim()
