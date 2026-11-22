package io.github.ronganam.dana.ui.screens.common

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.ui.components.HueMark
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.theme.DanaSerif
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Quiet, editorial empty state: a small hue mark ([hue], the accent by
 * default), one serif sentence and a muted line, no icon-in-circle.
 */
@Composable
fun QuietEmptyState(
    title: String,
    message: String,
    modifier: Modifier = Modifier,
    actionLabel: String? = null,
    hue: Color = MaterialTheme.colorScheme.primary,
    onAction: (() -> Unit)? = null
) {
    Column(
        modifier = modifier.fillMaxWidth().padding(horizontal = 28.dp, vertical = 40.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        HueMark(hue, Modifier.padding(bottom = 4.dp))
        Text(
            text = title,
            style = MaterialTheme.typography.headlineMedium.copy(fontFamily = DanaSerif, fontStyle = FontStyle.Italic),
            color = MaterialTheme.colorScheme.onSurface
        )
        Text(
            text = message,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        if (actionLabel != null && onAction != null) {
            TextButton(onClick = onAction, contentPadding = PaddingValues(0.dp)) {
                Text(actionLabel, color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.titleSmall)
            }
        }
    }
}

/** Follow the content's script for alignment/direction (Hebrew items right-align, Latin left-align). */
fun TextStyle.auto(): TextStyle = copy(textDirection = TextDirection.Content)

/** Small coloured dot followed by a mono label. */
@Composable
fun DotLabel(text: String, color: Color, modifier: Modifier = Modifier) {
    Row(modifier = modifier, verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(6.dp).clip(CircleShape).background(color))
        Spacer(Modifier.width(6.dp))
        MonoLabel(text, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/**
 * True when most letters of [s] are Hebrew / Arabic — not just the first:
 * "אלייה needs a different date" is an English line, so it stays left to right.
 */
fun mostlyRtl(s: String): Boolean {
    var rtl = 0
    var ltr = 0
    for (ch in s) {
        when (Character.getDirectionality(ch)) {
            Character.DIRECTIONALITY_RIGHT_TO_LEFT, Character.DIRECTIONALITY_RIGHT_TO_LEFT_ARABIC -> rtl++
            Character.DIRECTIONALITY_LEFT_TO_RIGHT -> ltr++
        }
    }
    return rtl > ltr
}

/** A text style laid out by the line's majority script (see [mostlyRtl]). */
fun TextStyle.byScript(s: String): TextStyle = copy(textDirection = if (mostlyRtl(s)) TextDirection.Rtl else TextDirection.Ltr)

/**
 * A "WhatsApp · גל · 7 min ago" kicker kept in its order: each part is
 * isolated and the separators are strong left-to-right, so a Hebrew name
 * doesn't pull the number next to it across.
 */
fun isolatedParts(s: String): String =
    s.split(" \u00B7 ").joinToString(" \u200E\u00B7\u200E ") { io.github.ronganam.dana.ui.components.isolate(it) }
