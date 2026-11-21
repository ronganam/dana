package app.ganam.assistant.ui.screens.money

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.ganam.assistant.ui.components.MonoLabel
import app.ganam.assistant.ui.theme.Danger
import app.ganam.assistant.ui.theme.Info
import app.ganam.assistant.ui.theme.Success
import app.ganam.assistant.ui.theme.Warning

// Small pieces shared by Transactions, the detail, Accounts, Save and the
// banners: badges, amounts, a three-way switch, a quiet text field, a
// section label and the pane's header row.

/** Ltr-isolated tabular money, so "−₪25.00" keeps its order next to Hebrew. */
internal val MoneyNumbers = TextStyle(fontFeatureSettings = "tnum", textDirection = TextDirection.Ltr)

/** A tone's colour: in (money in, good), out (default ink), muted, warn, bad, busy. */
@Composable
internal fun toneColor(tone: String): Color = when (tone) {
    "in", "good", "ok" -> Success
    "warn" -> Warning
    "bad" -> Danger
    "busy" -> Info
    "muted" -> MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f)
    else -> MaterialTheme.colorScheme.onSurface
}

/** A small mono badge (provisional, pending, 1/4, $5.00, One-off…): hairline, coloured by its type. */
@Composable
internal fun Badge(text: String, type: String, modifier: Modifier = Modifier) {
    val color = when (type) {
        "provisional", "new" -> MaterialTheme.colorScheme.primary
        "pending" -> Warning
        "unmatched" -> Danger
        "refund" -> Success
        "oneoff", "yearly", "fixed" -> Info
        else -> MaterialTheme.colorScheme.onSurfaceVariant
    }
    Text(
        text,
        style = MaterialTheme.typography.labelSmall.copy(textDirection = TextDirection.Content),
        color = color,
        maxLines = 1,
        modifier = modifier
            .border(1.dp, color.copy(alpha = 0.45f), RoundedCornerShape(6.dp))
            .padding(horizontal = 5.dp, vertical = 1.dp)
    )
}

/** A label above a block of the detail ("CATEGORY", "COUNTS AS"…). */
@Composable
internal fun Kicker(text: String, modifier: Modifier = Modifier) {
    MonoLabel(text, modifier.padding(top = 16.dp, bottom = 6.dp))
}

/** A row of segments (Yes / No / Automatic, Keep / Cancel): the chosen one in accent. */
@Composable
internal fun Segments(options: List<Pair<String, String>>, chosen: String?, enabled: Boolean = true, onPick: (String) -> Unit) {
    val accent = MaterialTheme.colorScheme.primary
    val line = MaterialTheme.colorScheme.outlineVariant
    val shape = RoundedCornerShape(10.dp)
    Row(Modifier.height(IntrinsicSize.Min).clip(shape).border(1.dp, line, shape)) {
        options.forEachIndexed { i, (value, label) ->
            val on = value == chosen
            if (i > 0) Box(Modifier.width(1.dp).fillMaxHeight().background(line))
            Text(
                label,
                style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium),
                color = if (on) accent else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = if (enabled) 1f else 0.5f),
                maxLines = 1,
                modifier = Modifier
                    .then(if (on) Modifier.background(accent.copy(alpha = 0.12f)) else Modifier)
                    .clickable(enabled = enabled, role = Role.RadioButton) { onPick(value) }
                    .semantics { selected = on }
                    .heightIn(min = 36.dp)
                    .padding(horizontal = 12.dp, vertical = 8.dp)
            )
        }
    }
}

/**
 * A quiet text field: hairline box, 10dp corners, a muted placeholder.
 * [secret] hides what is typed (password keyboard, no suggestions).
 */
@Composable
internal fun InkField(
    value: String,
    onValue: (String) -> Unit,
    placeholder: String,
    modifier: Modifier = Modifier,
    label: String? = null,
    secret: Boolean = false,
    keyboard: KeyboardType = KeyboardType.Text,
    imeAction: ImeAction = ImeAction.Done,
    onDone: () -> Unit = {},
    error: String? = null,
    singleLine: Boolean = true,
    minHeight: Dp = 42.dp,
    ltr: Boolean = false
) {
    val accent = MaterialTheme.colorScheme.primary
    Column(modifier) {
        label?.let { MonoLabel(it, Modifier.padding(bottom = 4.dp)) }
        val shape = RoundedCornerShape(10.dp)
        Box(
            Modifier
                .fillMaxWidth()
                .heightIn(min = minHeight)
                .clip(shape)
                .background(MaterialTheme.colorScheme.surface)
                .border(1.dp, if (error != null) Danger else MaterialTheme.colorScheme.outlineVariant, shape)
                .padding(horizontal = 12.dp, vertical = 10.dp),
            contentAlignment = Alignment.CenterStart
        ) {
            if (value.isEmpty()) {
                Text(placeholder, style = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content), color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f), maxLines = 1)
            }
            BasicTextField(
                value = value,
                onValueChange = onValue,
                singleLine = singleLine,
                textStyle = MaterialTheme.typography.bodyMedium.copy(
                    color = MaterialTheme.colorScheme.onSurface,
                    textDirection = if (ltr || secret) TextDirection.Ltr else TextDirection.Content
                ),
                cursorBrush = SolidColor(accent),
                visualTransformation = if (secret) PasswordVisualTransformation() else VisualTransformation.None,
                keyboardOptions = KeyboardOptions(
                    keyboardType = if (secret) KeyboardType.Password else keyboard,
                    autoCorrect = !secret && keyboard == KeyboardType.Text && !ltr,
                    imeAction = imeAction
                ),
                keyboardActions = KeyboardActions(onDone = { onDone() }, onSend = { onDone() }, onSearch = { onDone() }, onGo = { onDone() }),
                modifier = Modifier.fillMaxWidth().semantics { contentDescription = label ?: placeholder }
            )
        }
        error?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Danger, modifier = Modifier.padding(top = 4.dp)) }
    }
}

/** The detail pane's top row: a mono kicker and × (Back on the cover). */
@Composable
internal fun PaneTop(kicker: String, onClose: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(start = 18.dp, end = 4.dp, top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        MonoLabel(kicker, Modifier.weight(1f))
        Text(
            "×",
            style = MaterialTheme.typography.headlineSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier
                .clickable(role = Role.Button, onClick = onClose)
                .semantics { contentDescription = "Close" }
                .padding(horizontal = 14.dp, vertical = 6.dp)
        )
    }
}

/** A status dot + words ("Synced 2 h ago"). */
@Composable
internal fun StateLine(text: String, tone: String, modifier: Modifier = Modifier) {
    val c = toneColor(if (tone == "ok") "good" else tone)
    Row(modifier, verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(7.dp).clip(RoundedCornerShape(50)).background(if (tone == "ok") Success else c))
        Spacer(Modifier.width(7.dp))
        Text(text, style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium), color = if (tone == "ok") MaterialTheme.colorScheme.onSurface else c)
    }
}

/** A selectable row with the shell's look: canvas background and a 3dp accent bar when [selected]. */
internal fun Modifier.pickedRow(selected: Boolean, accent: Color, canvas: Color): Modifier =
    if (!selected) this else this.background(canvas).drawBehind {
        drawLine(accent, Offset(1.5.dp.toPx(), 0f), Offset(1.5.dp.toPx(), size.height), 3.dp.toPx())
    }

/** A quiet inline link ("See them", "Clear filters"): accent text, no box. */
@Composable
internal fun TextLink(text: String, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Text(
        text,
        style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium, textDirection = TextDirection.Content),
        color = MaterialTheme.colorScheme.primary,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
        modifier = modifier.clickable(role = Role.Button, onClick = onClick).padding(vertical = 6.dp)
    )
}

/** Muted body text that follows its own script. */
@Composable
internal fun Muted(text: String, modifier: Modifier = Modifier, maxLines: Int = Int.MAX_VALUE) {
    Text(
        text,
        style = MaterialTheme.typography.bodySmall.copy(textDirection = TextDirection.Content, fontSize = 13.sp),
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        maxLines = maxLines,
        overflow = TextOverflow.Ellipsis,
        modifier = modifier
    )
}

