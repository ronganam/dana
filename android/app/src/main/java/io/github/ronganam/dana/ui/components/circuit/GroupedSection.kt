package io.github.ronganam.dana.ui.components.circuit

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.takeOrElse
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.ui.theme.Group
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.theme.tabular

/**
 * The shape of row [index] of [count] joined rows: the group's outer radius
 * on its outside corners, a small inner radius where rows meet.
 */
fun groupItemShape(index: Int, count: Int, outer: Dp = Group.outer, inner: Dp = Group.inner): Shape = when {
    count <= 1 -> RoundedCornerShape(outer)
    index == 0 -> RoundedCornerShape(topStart = outer, topEnd = outer, bottomStart = inner, bottomEnd = inner)
    index == count - 1 -> RoundedCornerShape(topStart = inner, topEnd = inner, bottomStart = outer, bottomEnd = outer)
    else -> RoundedCornerShape(inner)
}

/** A section's heading: its name (18sp) and an optional count or word at the end. */
@Composable
fun SectionHeader(
    title: String,
    modifier: Modifier = Modifier,
    meta: String? = null,
    metaColor: Color = Color.Unspecified,
    onMeta: (() -> Unit)? = null,
    titleColor: Color = Color.Unspecified
) {
    val c = circuitColors
    Row(modifier.fillMaxWidth().heightIn(min = 32.dp).padding(bottom = 8.dp), verticalAlignment = Alignment.Bottom) {
        Text(
            title,
            style = MaterialTheme.typography.titleMedium.copy(fontSize = 18.sp, lineHeight = 24.sp, fontWeight = FontWeight.SemiBold),
            color = titleColor.takeOrElse { c.ink },
            modifier = Modifier.weight(1f),
            maxLines = 1,
            overflow = TextOverflow.Ellipsis
        )
        if (meta != null) Text(
            meta,
            modifier = (if (onMeta != null) Modifier.clickable(onClick = onMeta) else Modifier).padding(start = 12.dp),
            style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold).tabular(),
            color = metaColor.takeOrElse { c.inkSoft },
            maxLines = 1
        )
    }
}

/** What [GroupedSection] collects: its rows, in order. */
class GroupedSectionScope internal constructor() {
    internal class Item(val key: Any?, val color: Color?, val content: @Composable () -> Unit)
    internal val items = mutableListOf<Item>()

    /**
     * One joined row. [color]: its own surface (a "needs you" row on
     * `circuitColors.warmSurface`); the group's by default.
     */
    fun item(key: Any? = null, color: Color? = null, content: @Composable () -> Unit) {
        items += Item(key, color, content)
    }
}

/**
 * A tonal group without borders: an optional [SectionHeader], then its rows
 * joined with [gap] between them (24dp outer / 8dp inner corners). Each row
 * is clipped to its shape and painted in [containerColor] (or its own), and
 * nodes inside it punch out on that colour ([LocalNodeSurface]).
 */
@Composable
fun GroupedSection(
    modifier: Modifier = Modifier,
    title: String? = null,
    meta: String? = null,
    metaColor: Color = Color.Unspecified,
    containerColor: Color = MaterialTheme.colorScheme.surfaceContainerLow,
    gap: Dp = Group.gap,
    content: GroupedSectionScope.() -> Unit
) {
    val scope = GroupedSectionScope().apply(content)
    val items = scope.items
    Column(modifier.fillMaxWidth()) {
        if (title != null) SectionHeader(title, meta = meta, metaColor = metaColor)
        Column(verticalArrangement = Arrangement.spacedBy(gap)) {
            items.forEachIndexed { i, item ->
                androidx.compose.runtime.key(item.key ?: i) {
                    val color = item.color ?: containerColor
                    Box(Modifier.fillMaxWidth().clip(groupItemShape(i, items.size)).background(color)) {
                        CompositionLocalProvider(LocalNodeSurface provides color) { item.content() }
                    }
                }
            }
        }
    }
}

/**
 * A row of a group on the shared columns: [leading] (a node or an icon,
 * 32dp), the title and its supporting line, then [trailing] (a time, a
 * state or one action). 56dp at least.
 */
@Composable
fun GroupRow(
    title: String,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    leading: (@Composable () -> Unit)? = null,
    trailing: (@Composable () -> Unit)? = null,
    titleColor: Color = Color.Unspecified,
    onClick: (() -> Unit)? = null
) {
    val c = circuitColors
    Row(
        modifier
            .fillMaxWidth()
            .heightIn(min = 56.dp)
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(start = 16.dp, end = if (trailing != null) 4.dp else 16.dp, top = 6.dp, bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        if (leading != null) {
            Box(Modifier.width(32.dp), contentAlignment = Alignment.Center) { leading() }
            Spacer(Modifier.width(12.dp))
        }
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleMedium, color = titleColor.takeOrElse { c.ink }, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (subtitle != null) Text(subtitle, style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        if (trailing != null) {
            Spacer(Modifier.width(8.dp))
            Box(Modifier.padding(end = 12.dp)) { trailing() }
        }
    }
}
