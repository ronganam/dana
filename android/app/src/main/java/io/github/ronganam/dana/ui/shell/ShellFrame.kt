package io.github.ronganam.dana.ui.shell

import androidx.activity.compose.BackHandler
import android.content.res.Configuration
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.layout.layoutId
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.ui.adaptive.asidePaneWidth
import io.github.ronganam.dana.ui.adaptive.sidePaneWidth
import io.github.ronganam.dana.ui.viewmodels.Place

/**
 * How the shell arranges itself, from the window width (not the device):
 *  STACKED < 600dp  cover screen: one column, a pick drills down (Back returns), thread is a sheet
 *  SPLIT  600–899   unfolded portrait: list | stage, thread is a sheet, day strip on top
 *  FULL   ≥ 900     unfolded landscape: list | stage | thread, under the day strip
 *                   (a [Destination.wide] place is drawn like SPLIT, with the whole width for itself)
 */
enum class ShellLayout { STACKED, SPLIT, FULL }

fun shellLayoutFor(widthDp: Float): ShellLayout = when {
    widthDp >= 900f -> ShellLayout.FULL
    widthDp >= 600f -> ShellLayout.SPLIT
    else -> ShellLayout.STACKED
}

/** The arrangement the current destination is drawn in (read it to adapt a pane's content). */
val LocalShellLayout = compositionLocalOf { ShellLayout.STACKED }

/**
 * What one place of the shell shows. Build it with a `rememberXDestination()`
 * function (see `destinations/`); the shell decides where the panes go.
 *
 * - [list]: the left pane (tinted when unfolded). Its own header (kicker + serif title) scrolls with it.
 * - [stage]: what is picked, or the place's empty state. On the cover screen it replaces the list
 *   while [picked] is true; unfolded it is always shown next to the list.
 * - [stage] == null: [list] fills the list + stage area on its own (a screen with its own panes,
 *   e.g. today's Inbox); unfolded landscape then puts [title] in the top-left cell.
 * - [onBack]: what the cover screen's Back does while [backable] (usually clears the pick).
 * - [backable]: Back has something to close; by default while [picked] (a More item that takes
 *   over the place is backable from its own list, back to More).
 * - [wide]: unfolded landscape gives the content the whole width (list | stage, the thread as a
 *   sheet and the ask bar under them, as in portrait) instead of three columns: Projects, Money.
 */
class Destination(
    val place: Place,
    val picked: Boolean,
    val onBack: () -> Unit,
    val list: @Composable () -> Unit,
    val stage: (@Composable () -> Unit)?,
    val title: String = place.label,
    val kicker: String? = null,
    val backable: Boolean = picked,
    val wide: Boolean = false
)

/**
 * Hosts an existing full screen (Calls, People, Inbox…) inside a pane: it sees
 * the pane's size as the window size, so its own `windowTier()` /
 * `isWideLayout()` pick the layout that fits the pane, not the whole display.
 */
@Composable
fun PaneWindow(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    BoxWithConstraints(modifier.fillMaxSize()) {
        val base = LocalConfiguration.current
        val w = maxWidth.value.toInt()
        val h = maxHeight.value.toInt()
        val config = remember(base, w, h) {
            Configuration(base).apply {
                screenWidthDp = w
                screenHeightDp = h
                smallestScreenWidthDp = minOf(w, h)
            }
        }
        CompositionLocalProvider(LocalConfiguration provides config) { content() }
    }
}

/** A hairline along one edge, in the theme's rule colour. */
@Composable
internal fun Modifier.edgeRule(top: Boolean = false, bottom: Boolean = false, start: Boolean = false, end: Boolean = false): Modifier {
    val line = MaterialTheme.colorScheme.outlineVariant
    return drawBehind {
        val w = 1.dp.toPx()
        if (top) drawLine(line, Offset(0f, w / 2), Offset(size.width, w / 2), w)
        if (bottom) drawLine(line, Offset(0f, size.height - w / 2), Offset(size.width, size.height - w / 2), w)
        if (start) drawLine(line, Offset(w / 2, 0f), Offset(w / 2, size.height), w)
        if (end) drawLine(line, Offset(size.width - w / 2, 0f), Offset(size.width - w / 2, size.height), w)
    }
}

/** The pieces every arrangement places; the frame functions only decide where. */
class ShellSlots(
    val thread: @Composable (onClose: (() -> Unit)?) -> Unit,
    val ask: @Composable () -> Unit,
    val nav: @Composable () -> Unit,
    val strip: @Composable () -> Unit,
    val snackbar: SnackbarHostState,
    /** Kept free under the snackbar (a place's own bar at the bottom of its content). */
    val snackbarLift: Dp = 0.dp
)

private const val STAGE_KEY = "stage"
private const val LIST_KEY = "list"

/** Cover screen: content, then the ask bar, then the places. The thread slides up over the content. */
@Composable
internal fun StackedFrame(dest: Destination, slots: ShellSlots, threadOpen: Boolean, onCloseThread: () -> Unit, showNav: Boolean) {
    val holder = rememberSaveableStateHolder()
    Column(Modifier.fillMaxSize()) {
        Box(Modifier.weight(1f).fillMaxWidth()) {
            val stage = dest.stage
            // The list keeps its scroll position while a pick is open.
            if (stage != null && dest.picked) holder.SaveableStateProvider("${dest.place}-$STAGE_KEY") { stage() }
            else holder.SaveableStateProvider("${dest.place}-$LIST_KEY") { dest.list() }
            SnackbarHost(slots.snackbar, Modifier.align(Alignment.BottomCenter).padding(start = 12.dp, end = 12.dp, bottom = slots.snackbarLift))
            ThreadSheet(threadOpen, onCloseThread) { slots.thread(onCloseThread) }
        }
        slots.ask()
        if (showNav) slots.nav()
    }
}

/** Unfolded portrait: day strip, list | stage, ask bar, places. The thread is a sheet over the panes. */
@Composable
internal fun SplitFrame(dest: Destination, slots: ShellSlots, threadOpen: Boolean, onCloseThread: () -> Unit) {
    Column(Modifier.fillMaxSize()) {
        slots.strip()
        Box(Modifier.weight(1f).fillMaxWidth()) {
            val stage = dest.stage
            if (stage == null) {
                dest.list()
            } else Row(Modifier.fillMaxSize()) {
                Box(
                    Modifier.width(sidePaneWidth()).fillMaxHeight()
                        .background(MaterialTheme.colorScheme.surfaceContainerLow).edgeRule(end = true)
                ) { dest.list() }
                Box(Modifier.weight(1f).fillMaxHeight()) { stage() }
            }
            SnackbarHost(slots.snackbar, Modifier.align(Alignment.BottomStart).padding(start = 12.dp, end = 12.dp, top = 12.dp, bottom = 12.dp + slots.snackbarLift).widthIn(max = 520.dp))
            ThreadSheet(threadOpen, onCloseThread) { slots.thread(onCloseThread) }
        }
        slots.ask()
        slots.nav()
    }
}

@Composable
private fun BoxScope.ThreadSheet(open: Boolean, onClose: () -> Unit, content: @Composable () -> Unit) {
    // Composed after the stage, so it beats a drilled-in screen's own Back handler.
    BackHandler(enabled = open, onBack = onClose)
    AnimatedVisibility(
        visible = open,
        enter = slideInVertically(tween(260)) { it },
        exit = slideOutVertically(tween(220)) { it },
        modifier = Modifier.matchParentSize()
    ) {
        Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) { content() }
    }
}

/**
 * Unfolded landscape — one rule everywhere:
 *
 *   [ list (tinted)  | day strip ··························· ]
 *   [                | stage              | thread (tinted)  ]
 *   [ places         |                    | ask bar          ]
 *
 * A destination without a stage gets its title in the top-left cell and its
 * content across list + stage (above the places row).
 */
@Composable
internal fun FullFrame(dest: Destination, slots: ShellSlots, titleCell: @Composable () -> Unit) {
    val side: Dp = sidePaneWidth()
    val aside: Dp = asidePaneWidth()
    val tint = MaterialTheme.colorScheme.surfaceContainerLow
    val whole = dest.stage == null
    Box(Modifier.fillMaxSize()) {
        Layout(
            content = {
                if (whole) {
                    Box(Modifier.layoutId("title").background(tint).edgeRule(end = true, bottom = true)) { titleCell() }
                    Box(Modifier.layoutId("whole")) { dest.list() }
                } else {
                    Box(Modifier.layoutId("list").background(tint).edgeRule(end = true)) { dest.list() }
                    Box(Modifier.layoutId("stage")) { dest.stage?.invoke() }
                }
                Box(Modifier.layoutId("nav").edgeRule(end = true)) { slots.nav() }
                Box(Modifier.layoutId("strip").edgeRule(bottom = true)) { slots.strip() }
                Box(Modifier.layoutId("thread").background(tint).edgeRule(start = true)) { slots.thread(null) }
                Box(Modifier.layoutId("ask").edgeRule(start = true)) { slots.ask() }
            }
        ) { measurables, c ->
            val w = c.maxWidth
            val h = c.maxHeight
            val sideW = side.roundToPx().coerceAtMost(w / 3)
            val asideW = aside.roundToPx().coerceAtMost(w / 3)
            val stageW = (w - sideW - asideW).coerceAtLeast(0)
            fun m(id: String) = measurables.firstOrNull { it.layoutId == id }
            fun fixed(width: Int, height: Int) = Constraints.fixed(width.coerceAtLeast(0), height.coerceAtLeast(0))

            val strip = m("strip")!!.measure(Constraints(minWidth = stageW + asideW, maxWidth = stageW + asideW, maxHeight = h / 3))
            val stripH = strip.height
            val nav = m("nav")!!.measure(Constraints(minWidth = sideW, maxWidth = sideW, maxHeight = h / 4))
            val navH = nav.height
            val ask = m("ask")!!.measure(Constraints(minWidth = asideW, maxWidth = asideW, maxHeight = h / 2))
            val askH = ask.height
            val thread = m("thread")!!.measure(fixed(asideW, h - stripH - askH))
            val title = m("title")?.measure(fixed(sideW, stripH))
            val wholeP = m("whole")?.measure(fixed(sideW + stageW, h - stripH - navH))
            val list = m("list")?.measure(fixed(sideW, h - navH))
            val stage = m("stage")?.measure(fixed(stageW, h - stripH))

            layout(w, h) {
                list?.place(0, 0)
                title?.place(0, 0)
                wholeP?.place(0, stripH)
                stage?.place(sideW, stripH)
                nav.place(0, h - navH)
                strip.place(sideW, 0)
                thread.place(sideW + stageW, stripH)
                ask.place(sideW + stageW, h - askH)
            }
        }
        SnackbarHost(
            slots.snackbar,
            Modifier.align(Alignment.BottomStart).padding(start = 12.dp, bottom = 60.dp + slots.snackbarLift).widthIn(max = side + 200.dp)
        )
    }
}
