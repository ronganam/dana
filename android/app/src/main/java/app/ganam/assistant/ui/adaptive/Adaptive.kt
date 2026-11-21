package app.ganam.assistant.ui.adaptive

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/**
 * Window tiers, driven by live window width (re-evaluates when a foldable opens/closes or the window is resized).
 *  COMPACT  < 600dp   phone / folded cover screen: bottom bar, one pane
 *  MEDIUM   600–799   unfolded inner screen (portrait): rail + list + content (2 panes)
 *  EXPANDED ≥ 800     unfolded landscape / tablet: rail + list + content + context (3 panes; context is
 *                     user-toggleable and open by default from 900dp)
 */
enum class WindowTier { COMPACT, MEDIUM, EXPANDED }

@Composable
fun windowTier(): WindowTier {
    val w = LocalConfiguration.current.screenWidthDp
    return when {
        w >= 800 -> WindowTier.EXPANDED
        w >= 600 -> WindowTier.MEDIUM
        else -> WindowTier.COMPACT
    }
}

/**
 * THE pane spec — every multi-pane screen follows it so columns line up from tab to tab:
 *
 *   [ one global header: kicker + serif title + actions, spanning everything right of the rail ]
 *   [ SIDE pane (tinted)  |  MAIN pane(s) (flex, untinted)  |  ASIDE pane (tinted, optional) ]
 *
 * - SIDE = the index/list/filter column: 280dp (248dp when the window is < 960dp wide).
 * - ASIDE = contextual extras (context, actions, inspector): 288dp (256dp when < 960dp).
 * - MAIN panes share the remaining width equally; they are never tinted.
 * - Panes carry no headers of their own; use a MonoLabel row for pane-level labels.
 * Use [sidePaneWidth] / [asidePaneWidth] — never hardcode a pane width in a screen.
 */
object PaneWidth {
    val side = 280.dp
    val sideNarrow = 248.dp
    val aside = 288.dp
    val asideNarrow = 256.dp
    // Back-compat aliases
    val list = side
    val listNarrow = sideNarrow
    val context = aside
}

@Composable
fun windowWidthDp(): Int = LocalConfiguration.current.screenWidthDp

@Composable
fun sidePaneWidth(): Dp = if (windowWidthDp() >= 960) PaneWidth.side else PaneWidth.sideNarrow

@Composable
fun asidePaneWidth(): Dp = if (windowWidthDp() >= 960) PaneWidth.aside else PaneWidth.asideNarrow

@Composable
fun listPaneWidth(): Dp = sidePaneWidth()

@Composable
fun contextPaneWidth(): Dp = asidePaneWidth()

/** Horizontal multi-pane container. Children are [Pane]s; hairlines separate them. */
@Composable
fun PaneRow(modifier: Modifier = Modifier, content: @Composable RowScope.() -> Unit) {
    Row(modifier = modifier.fillMaxSize(), content = content)
}

/** One pane: fixed [width] or flexible (weight 1) when null. Draws a trailing hairline when [divider]. */
@Composable
fun RowScope.Pane(
    modifier: Modifier = Modifier,
    width: Dp? = null,
    divider: Boolean = true,
    tinted: Boolean = false,
    content: @Composable BoxScope.() -> Unit
) {
    val line = MaterialTheme.colorScheme.outlineVariant
    val base = if (width != null) Modifier.width(width).fillMaxHeight() else Modifier.weight(1f).fillMaxHeight()
    Box(
        modifier = modifier
            .then(base)
            .then(if (tinted) Modifier.background(MaterialTheme.colorScheme.surfaceContainerLow) else Modifier)
            .then(
                if (divider) Modifier.drawBehind {
                    drawLine(line, Offset(size.width - 0.5f, 0f), Offset(size.width - 0.5f, size.height), 1.dp.toPx())
                } else Modifier
            ),
        content = content
    )
}
