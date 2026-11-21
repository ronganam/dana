package app.ganam.assistant.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.ganam.assistant.ui.theme.Space

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
            if (kicker != null) MonoLabel(kicker, color = MaterialTheme.colorScheme.primary)
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

/** Friendly empty/zero-data state with an optional primary action. */
@Composable
fun EmptyState(
    icon: ImageVector,
    title: String,
    message: String,
    modifier: Modifier = Modifier,
    actionLabel: String? = null,
    onAction: (() -> Unit)? = null
) {
    Column(
        modifier = modifier.fillMaxWidth().padding(horizontal = 32.dp, vertical = 48.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        Box(
            modifier = Modifier.size(64.dp).clip(CircleShape).background(MaterialTheme.colorScheme.surfaceContainerHigh),
            contentAlignment = Alignment.Center
        ) {
            Icon(icon, contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(28.dp))
        }
        Spacer(Modifier.height(4.dp))
        Text(title, style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.onSurface, textAlign = TextAlign.Center)
        Text(message, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center)
        if (actionLabel != null && onAction != null) {
            Spacer(Modifier.height(8.dp))
            FilledTonalButton(onClick = onAction) { Text(actionLabel) }
        }
    }
}

/** Small uppercase-free section label used above groups of rows/cards. */
@Composable
fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    Text(
        text = text,
        style = MaterialTheme.typography.labelLarge,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = modifier.padding(horizontal = Space.xs, vertical = Space.sm)
    )
}

/** Compact status/tag pill tinted by [color] (semantic hue or category colour). */
@Composable
fun StatusPill(text: String, color: Color, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier
            .clip(RoundedCornerShape(50))
            .background(color.copy(alpha = 0.16f))
            .padding(horizontal = 10.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(Modifier.size(6.dp).clip(CircleShape).background(color))
        Spacer(Modifier.width(6.dp))
        Text(text, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurface, fontWeight = FontWeight.Medium)
    }
}

/** Mono, uppercase micro-label for metadata (counts, timestamps, section kickers). */
@Composable
fun MonoLabel(text: String, modifier: Modifier = Modifier, color: Color = MaterialTheme.colorScheme.onSurfaceVariant) {
    Text(
        text = text.uppercase(),
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
    Box(
        modifier = modifier
            .heightIn(min = 36.dp)
            .clip(shape)
            .then(if (solid) Modifier.background(accent) else Modifier)
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
            .then(if (emphasis == InkEmphasis.SOLID) Modifier.background(accent) else Modifier)
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
