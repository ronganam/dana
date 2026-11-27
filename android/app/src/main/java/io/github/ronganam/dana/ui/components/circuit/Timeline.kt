package io.github.ronganam.dana.ui.components.circuit

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.theme.tabular

/**
 * One row of a vertical timeline (Now's "Next", a reminder's moments): the
 * time | the node on the line | the content, in fixed columns so rows line
 * up. The line runs through the node column from row to row: it starts at
 * the node on the [first] row and ends there on the [last]. A [time] that is
 * a clock time is tabular ink; [timeIsPlace] (a place, "On waking") is
 * supporting text instead. [gap]: space under the row (the line crosses it).
 */
@Composable
fun TimelineRow(
    time: String?,
    node: LineNode,
    modifier: Modifier = Modifier,
    timeIsPlace: Boolean = false,
    first: Boolean = false,
    last: Boolean = false,
    timeColumn: Dp = 48.dp,
    gap: Dp = 8.dp,
    onClick: (() -> Unit)? = null,
    trailing: (@Composable () -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit
) {
    val c = circuitColors
    val size = if (node.icon != null) NodeSize.Timeline else NodeSize.Dot
    // The node sits on the content's first line (22dp).
    val nodeCenter = 11.dp
    Row(
        modifier
            .fillMaxWidth()
            .height(IntrinsicSize.Min)
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
    ) {
        Box(Modifier.width(timeColumn).padding(bottom = gap), contentAlignment = Alignment.TopEnd) {
            if (time != null) Text(
                time,
                style = if (timeIsPlace) MaterialTheme.typography.bodyMedium.copy(fontSize = 13.sp, lineHeight = 22.sp)
                else MaterialTheme.typography.titleMedium.copy(fontSize = 15.sp, lineHeight = 22.sp).tabular(),
                color = if (timeIsPlace) c.inkSoft else c.ink,
                textAlign = TextAlign.End,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
        }
        Spacer(Modifier.width(12.dp))
        Box(
            Modifier
                .width(24.dp)
                .fillMaxHeight()
                .drawBehind {
                    val x = this.size.width / 2f
                    val cy = nodeCenter.toPx()
                    val top = if (first) cy else 0f
                    val bottom = if (last) cy else this.size.height
                    if (bottom > top) drawLine(c.wire, Offset(x, top), Offset(x, bottom), strokeWidth = 1.5.dp.toPx())
                },
            contentAlignment = Alignment.TopCenter
        ) {
            // An offset, not padding: a 24dp node box centred on an 11dp line starts above the row.
            CircuitNode(node.shape, node.state, Modifier.offset(y = nodeCenter - size.box / 2), size = size, icon = node.icon)
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f).padding(bottom = gap), content = content)
        if (trailing != null) Box(Modifier.padding(start = 12.dp, bottom = gap)) { trailing() }
    }
}

/** A [TimelineRow] with a title and an optional subtitle. */
@Composable
fun TimelineRow(
    time: String?,
    node: LineNode,
    title: String,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    timeIsPlace: Boolean = false,
    first: Boolean = false,
    last: Boolean = false,
    timeColumn: Dp = 48.dp,
    onClick: (() -> Unit)? = null,
    trailing: (@Composable () -> Unit)? = null
) {
    val c = circuitColors
    TimelineRow(time, node, modifier, timeIsPlace, first, last, timeColumn, onClick = onClick, trailing = trailing) {
        Text(title, style = MaterialTheme.typography.titleMedium, color = c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis)
        if (subtitle != null) Text(subtitle, style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, maxLines = 2, overflow = TextOverflow.Ellipsis)
    }
}
