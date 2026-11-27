package io.github.ronganam.dana.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MoreHoriz
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.theme.Space
import io.github.ronganam.dana.ui.theme.hues

/** Set by the app shell; non-null when a Settings shortcut should appear in top bars. */
val LocalOpenSettings = compositionLocalOf<(() -> Unit)?> { null }

/**
 * The one top bar every screen uses: left-aligned title, optional subtitle,
 * screen actions, then the Settings shortcut. Replaces ad-hoc CenterAlignedTopAppBars.
 */
@Composable
fun ScreenTopBar(
    title: String,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    kicker: String? = null,
    showSettings: Boolean = true,
    navigationIcon: @Composable (() -> Unit)? = null,
    actions: @Composable RowScope.() -> Unit = {}
) {
    val openSettings = LocalOpenSettings.current
    CompositionLocalProvider(LocalContentColor provides MaterialTheme.colorScheme.onBackground) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .statusBarsPadding()
            .heightIn(min = 64.dp)
            .padding(start = if (navigationIcon != null) 4.dp else Space.screen, end = 12.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        navigationIcon?.invoke()
        Column(modifier = Modifier.weight(1f).padding(vertical = 8.dp)) {
            if (kicker != null) MetaLabel(kicker, color = MaterialTheme.colorScheme.primary)
            Text(
                text = title,
                style = MaterialTheme.typography.headlineMedium,
                color = MaterialTheme.colorScheme.onBackground,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
            if (subtitle != null) {
                Text(
                    text = subtitle,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
            }
        }
        actions()
        if (showSettings && openSettings != null) {
            IconButton(onClick = openSettings) {
                Icon(Icons.Filled.Settings, contentDescription = "Settings", tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
    }
}

/**
 * The one small hue mark an empty state may carry above its line (an
 * 18 × 3dp bar), never an icon in a circle. It grows in from its start once,
 * as it appears ([growIn]; phone only, not with animations off).
 */
@Composable
fun HueMark(color: Color, modifier: Modifier = Modifier) {
    Box(modifier.growIn().size(18.dp, 3.dp).clip(RoundedCornerShape(2.dp)).background(color))
}

/**
 * Small section label above a group of cards (Settings), with a dot in the
 * group's [hue] (the kind it is about; the accent by default).
 */
@Composable
fun SectionLabel(text: String, modifier: Modifier = Modifier, hue: Color = MaterialTheme.colorScheme.primary) {
    Row(modifier.padding(horizontal = Space.xs, vertical = Space.sm), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(6.dp).clip(CircleShape).background(hue))
        Spacer(Modifier.width(8.dp))
        Text(text = text, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/**
 * Compact status/tag pill in [color] (a hue or a category colour): the hue's
 * tint, a dot at the full hue and the text in the hue (darkened on Paper to
 * keep 4.5:1).
 */
@Composable
fun StatusPill(text: String, color: Color, modifier: Modifier = Modifier) {
    val h = hues
    Row(
        modifier = modifier
            .clip(RoundedCornerShape(50))
            .background(h.tint(color))
            .padding(horizontal = 10.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(Modifier.size(6.dp).clip(CircleShape).background(color))
        Spacer(Modifier.width(6.dp))
        Text(text, style = MaterialTheme.typography.labelMedium, color = h.text(color), fontWeight = FontWeight.Medium)
    }
}

/**
 * Initials on a hue picked from the name (the same name, the same hue on
 * every device): the hue's tint, the letters in the hue. People, callers.
 */
@Composable
fun HueInitials(name: String, modifier: Modifier = Modifier, size: Dp = 36.dp, letters: String = initialsOf(name)) {
    val h = hues
    val color = h.forName(name)
    Box(
        modifier.size(size).clip(CircleShape).background(h.tint(color)),
        contentAlignment = Alignment.Center
    ) {
        Text(
            letters,
            style = MaterialTheme.typography.labelLarge.copy(fontSize = (size.value * 0.36f).sp),
            color = h.text(color),
            fontWeight = FontWeight.SemiBold,
            maxLines = 1
        )
    }
}

/** "Sam Example" → "SE", "דנה" → "ד", "" → "·". */
fun initialsOf(name: String): String {
    val words = name.trim().split(Regex("\\s+")).filter { it.isNotEmpty() && it.first().isLetterOrDigit() }
    return when {
        words.isEmpty() -> "·"
        words.size == 1 -> words[0].take(1).uppercase()
        else -> (words.first().take(1) + words.last().take(1)).uppercase()
    }
}

/**
 * A one-line metadata label (counts, times, a short kicker): 12sp medium with
 * tabular figures, in sentence case — never tiny uppercase.
 */
@Composable
fun MetaLabel(text: String, modifier: Modifier = Modifier, color: Color = MaterialTheme.colorScheme.onSurfaceVariant) {
    Text(
        text = text.replaceFirstChar { it.uppercaseChar() },
        style = MaterialTheme.typography.labelMedium,
        color = color,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
        modifier = modifier
    )
}

/** Full-width 1dp rule in the theme's hairline colour. */
@Composable
fun Rule(modifier: Modifier = Modifier) {
    HorizontalDivider(modifier = modifier, thickness = 1.dp, color = MaterialTheme.colorScheme.outlineVariant)
}

/**
 * A choice button (outlined, 10dp corners): the editor's "Remind me" options,
 * lists, priority, quick times. Selected = vermilion outline and text.
 * [solid] fills it with the accent (a primary action such as "Done").
 */
@Composable
fun InkChoice(
    text: String,
    selected: Boolean = false,
    modifier: Modifier = Modifier,
    solid: Boolean = false,
    onClick: () -> Unit
) {
    val accent = MaterialTheme.colorScheme.primary
    val shape = RoundedCornerShape(10.dp)
    val fill = when {
        solid -> Modifier.background(accentFill())
        selected -> Modifier.background(hues.tint(accent))
        else -> Modifier
    }
    Box(
        modifier = modifier
            .heightIn(min = 36.dp)
            .clip(shape)
            .then(fill)
            .border(1.dp, if (selected || solid) accent else MaterialTheme.colorScheme.outlineVariant, shape)
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 7.dp),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text,
            style = MaterialTheme.typography.bodyMedium.copy(
                fontWeight = FontWeight.Medium,
                textDirection = androidx.compose.ui.text.style.TextDirection.Content
            ),
            color = when {
                solid -> MaterialTheme.colorScheme.onPrimary
                selected -> accent
                else -> MaterialTheme.colorScheme.onSurfaceVariant
            },
            maxLines = 2,
            overflow = TextOverflow.Ellipsis
        )
    }
}

/**
 * A filled button's container: the filled accent leaning towards plum at its
 * end (`circuitColors.accentFilled` → `accentFilledEnd`, mirrored in RTL;
 * dark text on it in the dark theme, [MaterialTheme]'s onPrimary). Static.
 * Dimmed and flat when disabled.
 */
@Composable
fun accentFill(enabled: Boolean = true): Brush {
    val c = circuitColors
    if (!enabled) return SolidColor(c.accentFilled.copy(alpha = 0.5f))
    val stops = listOf(c.accentFilled, c.accentFilledEnd)
    return Brush.horizontalGradient(if (androidx.compose.ui.platform.LocalLayoutDirection.current == androidx.compose.ui.unit.LayoutDirection.Rtl) stops.reversed() else stops)
}

/** How loud an [InkButton] is. */
enum class InkEmphasis { PLAIN, PRIMARY, SOLID, BARE }

/**
 * An action button of the main screen (32dp, 10dp corners, hairline): PLAIN
 * muted text, PRIMARY vermilion outline + text (a row's main action), SOLID
 * filled vermilion (the decision on the stage), BARE text only ("Clear",
 * "Ask Dana about this →"). Text direction follows the label (Hebrew labels).
 */
@Composable
fun InkButton(
    text: String,
    modifier: Modifier = Modifier,
    emphasis: InkEmphasis = InkEmphasis.PLAIN,
    enabled: Boolean = true,
    onClick: () -> Unit
) {
    val accent = MaterialTheme.colorScheme.primary
    val shape = RoundedCornerShape(10.dp)
    val border = when (emphasis) {
        InkEmphasis.PLAIN -> MaterialTheme.colorScheme.outlineVariant
        InkEmphasis.PRIMARY, InkEmphasis.SOLID -> accent
        InkEmphasis.BARE -> Color.Transparent
    }
    Box(
        modifier = modifier
            .heightIn(min = 34.dp)
            .clip(shape)
            .then(if (emphasis == InkEmphasis.SOLID) Modifier.background(accentFill(enabled)) else Modifier)
            .border(1.dp, border, shape)
            .clickable(enabled = enabled, onClick = onClick)
            .padding(horizontal = if (emphasis == InkEmphasis.BARE) 4.dp else 12.dp, vertical = 7.dp),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text,
            style = MaterialTheme.typography.bodyMedium.copy(
                fontWeight = FontWeight.Medium,
                textDirection = androidx.compose.ui.text.style.TextDirection.Content
            ),
            color = when (emphasis) {
                InkEmphasis.SOLID -> MaterialTheme.colorScheme.onPrimary
                InkEmphasis.PRIMARY -> accent
                InkEmphasis.BARE, InkEmphasis.PLAIN -> MaterialTheme.colorScheme.onSurfaceVariant
            }.let { if (enabled) it else it.copy(alpha = 0.5f) },
            maxLines = 1,
            overflow = TextOverflow.Ellipsis
        )
    }
}

/**
 * A row's one visible action ("the showreel look"): a quiet pill, the accent's
 * tint with the accent's text; [solid] fills it with the accent (approvals).
 * 34dp tall.
 */
@Composable
fun ActionPill(text: String, modifier: Modifier = Modifier, solid: Boolean = false, enabled: Boolean = true, onClick: () -> Unit) {
    val accent = MaterialTheme.colorScheme.primary
    val shape = RoundedCornerShape(50)
    Box(
        modifier = modifier
            .heightIn(min = 34.dp)
            .clip(shape)
            .then(if (solid) Modifier.background(accentFill(enabled)) else Modifier.background(hues.tint(accent)))
            .clickable(enabled = enabled, role = androidx.compose.ui.semantics.Role.Button, onClick = onClick)
            .padding(horizontal = 15.dp, vertical = 7.dp),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text,
            style = MaterialTheme.typography.bodyMedium.copy(
                fontSize = 13.5.sp,
                fontWeight = FontWeight.Medium,
                textDirection = androidx.compose.ui.text.style.TextDirection.Content
            ),
            color = (if (solid) MaterialTheme.colorScheme.onPrimary else hues.text(accent)).let { if (enabled) it else it.copy(alpha = 0.5f) },
            maxLines = 1,
            overflow = TextOverflow.Ellipsis
        )
    }
}

/** One entry of an [OverflowMenu]. */
class MenuEntry(val label: String, val onClick: () -> Unit)

/**
 * The ⋯ that holds a row's other actions: a 40dp button ("More actions" to a
 * screen reader) opening a menu of [entries], in order; [divideBefore] puts a
 * hairline before that index (e.g. before "Ask Dana about this").
 */
@Composable
fun OverflowMenu(entries: List<MenuEntry>, modifier: Modifier = Modifier, divideBefore: Int = -1, label: String = "More actions") {
    var open by rememberSaveable { mutableStateOf(false) }
    Box(modifier) {
        IconButton(onClick = { open = true }, modifier = Modifier.size(40.dp)) {
            Icon(
                Icons.Filled.MoreHoriz,
                contentDescription = label,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(20.dp)
            )
        }
        DropdownMenu(
            expanded = open,
            onDismissRequest = { open = false },
            shape = RoundedCornerShape(14.dp),
            containerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
            tonalElevation = 0.dp,
            border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant)
        ) {
            entries.forEachIndexed { i, e ->
                if (i == divideBefore && i > 0) HorizontalDivider(Modifier.padding(vertical = 4.dp), thickness = 1.dp, color = MaterialTheme.colorScheme.outlineVariant)
                DropdownMenuItem(
                    text = {
                        Text(
                            e.label,
                            style = MaterialTheme.typography.bodyMedium.copy(textDirection = androidx.compose.ui.text.style.TextDirection.Content),
                            color = MaterialTheme.colorScheme.onSurface
                        )
                    },
                    onClick = { open = false; e.onClick() }
                )
            }
        }
    }
}
