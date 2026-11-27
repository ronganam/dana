package io.github.ronganam.dana.ui.screens.about

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.models.AboutFact
import io.github.ronganam.dana.data.models.LearnedPhrase
import io.github.ronganam.dana.data.models.SelfCheck
import io.github.ronganam.dana.ui.components.InkButton
import io.github.ronganam.dana.ui.components.InkChoice
import io.github.ronganam.dana.ui.components.InkEmphasis
import io.github.ronganam.dana.ui.components.MetaLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.screens.common.byScript
import io.github.ronganam.dana.ui.theme.Success
import io.github.ronganam.dana.ui.theme.Warning
import io.github.ronganam.dana.ui.viewmodels.MainViewModel

/**
 * About you: what Dana keeps about the owner, one editable line per fact
 * (saved when the line is left or Done is pressed, × forgets it with Undo),
 * how Dana acts, the phrases she runs without the model, the self-check and,
 * last, the way to Settings. One scrolling page; unfolded it keeps a reading
 * width.
 */
@Composable
fun AboutScreen(viewModel: MainViewModel, onBack: (() -> Unit)?, onOpenSettings: () -> Unit) {
    val about = viewModel.aboutController
    val page by about.page.collectAsState()
    val phrases by about.phrases.collectAsState()
    val checks by about.checks.collectAsState()
    val loading by about.loading.collectAsState()
    val failed by about.failed.collectAsState()
    LaunchedEffect(Unit) { about.refresh() }

    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.TopCenter) {
        Column(
            Modifier
                .widthIn(max = 680.dp)
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .imePadding()
                .padding(horizontal = 18.dp)
                .padding(top = if (onBack != null) 0.dp else 20.dp, bottom = 32.dp)
        ) {
            if (onBack != null) {
                Text(
                    "← More",
                    style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.clickable(onClick = onBack).padding(vertical = 12.dp)
                )
            }
            MetaLabel("Dana keeps this · edit any line")
            Text("About you", style = MaterialTheme.typography.displayMedium, modifier = Modifier.padding(top = 2.dp, bottom = 4.dp))

            val p = page
            if (p == null) {
                Note(
                    when {
                        failed -> "Couldn't load what Dana keeps. Check the connection, then try again."
                        else -> "Loading…"
                    }
                )
                if (failed && !loading) InkButton("Try again", modifier = Modifier.padding(top = 8.dp)) { about.refresh() }
            } else {
                val filled = p.sections.filter { it.facts.isNotEmpty() }
                if (filled.isEmpty()) Note("Nothing yet. Tell Dana something about you, or add it below.")
                filled.forEach { section ->
                    Section(section.label)
                    section.facts.forEach { fact ->
                        key(fact.id) { FactLine(fact, onSave = { about.edit(fact, it) }, onForget = { about.remove(fact) }) }
                    }
                }
                AddLine(onAdd = { about.add(it) })
                if (p.maxChars > 0) MetaLabel("${p.usedChars} of ${p.maxChars} characters", modifier = Modifier.padding(top = 6.dp))
            }

            Section("How Dana acts")
            Line("Does things at once and shows Undo.")
            Line("Asks first only before sending a message or deleting.")
            Line("Interrupts only for urgent things. The rest waits in Now.")

            Section("What Dana does without thinking")
            val learned = phrases.filter { it.active }
            if (learned.isEmpty()) Note("Nothing yet. When you ask for the same thing a few times, Dana learns to do it at once.")
            learned.forEach { ph -> key(ph.id) { PhraseLine(ph, onRemove = { about.removePhrase(ph) }) } }

            Section("Self-check")
            if (checks.isEmpty()) Note(if (loading) "Checking…" else "No checks from the server.")
            checks.forEach { c -> key(c.key) { CheckLine(c, onIgnore = { about.ignore(c) }) } }

            Rule(Modifier.padding(top = 22.dp))
            Row(
                Modifier.fillMaxWidth().clickable(onClick = onOpenSettings).padding(vertical = 14.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(Modifier.weight(1f)) {
                    Text("Settings", style = MaterialTheme.typography.titleMedium)
                    MetaLabel("server, permissions, updates", modifier = Modifier.padding(top = 2.dp))
                }
                MetaLabel("→", color = MaterialTheme.colorScheme.primary)
            }
            Rule()
        }
    }
}

@Composable
private fun Section(label: String) {
    Column(Modifier.padding(top = 22.dp)) {
        MetaLabel(label, modifier = Modifier.padding(bottom = 6.dp))
        Rule()
    }
}

@Composable
private fun Note(text: String) {
    Text(text, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(vertical = 10.dp))
}

@Composable
private fun Line(text: String) {
    Text(text, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.padding(vertical = 7.dp))
}

/** The small "where it came from" line, in the sentence's own direction (names may be Hebrew). */
@Composable
private fun Caption(text: String, modifier: Modifier = Modifier) {
    Text(
        text,
        style = MaterialTheme.typography.labelMedium.copy(textDirection = TextDirection.Content),
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        maxLines = 2,
        overflow = TextOverflow.Ellipsis,
        modifier = modifier
    )
}

/** A fact, editable in place: saved when the line is left or on Done; × forgets it. */
@Composable
private fun FactLine(fact: AboutFact, onSave: (String) -> Unit, onForget: () -> Unit) {
    var text by rememberSaveable(fact.id, fact.text) { mutableStateOf(fact.text) }
    var focused by remember { mutableStateOf(false) }
    val focus = LocalFocusManager.current
    Column {
        Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.Top) {
            Column(Modifier.weight(1f).padding(top = 2.dp)) {
                BasicTextField(
                    value = text,
                    onValueChange = { text = it },
                    textStyle = MaterialTheme.typography.bodyLarge.byScript(text).copy(color = MaterialTheme.colorScheme.onSurface),
                    cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                    keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Done),
                    keyboardActions = KeyboardActions(onDone = { focus.clearFocus() }),
                    modifier = Modifier.fillMaxWidth().onFocusChanged { f ->
                        if (focused && !f.isFocused) {
                            if (text.isBlank()) text = fact.text else onSave(text)
                        }
                        focused = f.isFocused
                    }
                )
                Caption(if (focused) "editing · Done saves" else fact.sourceLine, Modifier.padding(top = 3.dp))
            }
            Box(Modifier.size(44.dp).clickable(onClick = onForget), contentAlignment = Alignment.Center) {
                Icon(Icons.Filled.Close, contentDescription = "Forget this", tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(18.dp))
            }
        }
        Rule(Modifier.padding(top = 6.dp))
    }
}

@Composable
private fun AddLine(onAdd: (String) -> Unit) {
    var text by rememberSaveable { mutableStateOf("") }
    val shape = RoundedCornerShape(12.dp)
    fun add() {
        if (text.isNotBlank()) {
            onAdd(text)
            text = ""
        }
    }
    Row(Modifier.fillMaxWidth().padding(top = 14.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        BasicTextField(
            value = text,
            onValueChange = { text = it },
            textStyle = MaterialTheme.typography.bodyLarge.copy(color = MaterialTheme.colorScheme.onSurface, textDirection = TextDirection.Content),
            cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { add() }),
            modifier = Modifier.weight(1f),
            decorationBox = { inner ->
                Box(
                    Modifier
                        .heightIn(min = 44.dp)
                        .background(MaterialTheme.colorScheme.background, shape)
                        .border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape)
                        .padding(horizontal = 12.dp, vertical = 10.dp),
                    contentAlignment = Alignment.CenterStart
                ) {
                    if (text.isEmpty()) Text(
                        "Something Dana should know…",
                        style = MaterialTheme.typography.bodyLarge,
                        color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f)
                    )
                    // Full width, so a Hebrew line starts at the right.
                    Box(Modifier.fillMaxWidth()) { inner() }
                }
            }
        )
        InkChoice("Add", selected = text.isNotBlank(), onClick = { add() })
    }
}

@Composable
private fun PhraseLine(p: LearnedPhrase, onRemove: () -> Unit) {
    Column {
        Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.Top) {
            Column(Modifier.weight(1f).padding(top = 2.dp)) {
                Text("“${p.phrase}”", style = MaterialTheme.typography.bodyLarge.byScript(p.phrase))
                val used = when (p.timesUsed) {
                    0 -> "not used yet"
                    1 -> "used once"
                    else -> "used ${p.timesUsed} times"
                }
                Caption(listOf(p.does, used).filter { it.isNotBlank() }.joinToString(" · "), Modifier.padding(top = 3.dp))
            }
            Box(Modifier.size(44.dp).clickable(onClick = onRemove), contentAlignment = Alignment.Center) {
                Icon(Icons.Filled.Close, contentDescription = "Send this phrase through Dana again", tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(18.dp))
            }
        }
        Rule(Modifier.padding(top = 6.dp))
    }
}

@Composable
private fun CheckLine(c: SelfCheck, onIgnore: () -> Unit) {
    val dot: Color = when {
        c.ok -> Success
        c.ignoredUntil != null -> MaterialTheme.colorScheme.outline
        else -> Warning
    }
    Column(Modifier.padding(vertical = 7.dp)) {
        Row(verticalAlignment = Alignment.Top) {
            Box(Modifier.padding(top = 7.dp, end = 10.dp).size(8.dp).background(dot, CircleShape))
            Column(Modifier.weight(1f)) {
                Text(c.label, style = MaterialTheme.typography.bodyLarge)
                val detail = when {
                    c.ignoredUntil != null && !c.ok -> listOf(c.detail, "ignored until ${c.ignoredUntil.take(10)}").filter { it.isNotBlank() }.joinToString(" · ")
                    else -> c.detail
                }
                if (detail.isNotBlank()) Caption(detail, Modifier.padding(top = 2.dp))
                if (c.failing) InkButton("Ignore for a week", emphasis = InkEmphasis.PLAIN, modifier = Modifier.padding(top = 6.dp), onClick = onIgnore)
            }
        }
    }
}
