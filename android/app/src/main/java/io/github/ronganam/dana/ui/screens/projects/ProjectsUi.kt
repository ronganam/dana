package io.github.ronganam.dana.ui.screens.projects

import io.github.ronganam.dana.ui.theme.hues
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.projects.Lang
import io.github.ronganam.dana.data.projects.ProjectsCore
import io.github.ronganam.dana.data.projects.ValueShown
import io.github.ronganam.dana.ui.components.InkButton
import io.github.ronganam.dana.ui.components.InkEmphasis
import io.github.ronganam.dana.ui.screens.common.isolatedParts
import io.github.ronganam.dana.ui.theme.Success
import io.github.ronganam.dana.ui.viewmodels.ProjectsController

/** The open project's language (Hebrew projects speak Hebrew inside). */
internal val LocalProjectLang = compositionLocalOf { Lang.EN }

/** Lays [content] out in [lang]: right to left for Hebrew. */
@Composable
internal fun ProjectScope(lang: Lang, content: @Composable () -> Unit) {
    CompositionLocalProvider(
        LocalProjectLang provides lang,
        LocalLayoutDirection provides if (lang == Lang.HE) LayoutDirection.Rtl else LayoutDirection.Ltr,
        content = content
    )
}

@Composable
internal fun t(en: String, he: String): String = LocalProjectLang.current.t(en, he)

/** Plain body text for descriptions and quiet statements. */
@Composable
internal fun quietText(size: Int = 17): TextStyle = MaterialTheme.typography.bodyLarge.copy(
    fontSize = size.sp, lineHeight = (size + 6).sp
)

/** Body text whose direction follows its own script. */
internal fun TextStyle.byContent(): TextStyle = copy(textDirection = TextDirection.Content)

/** A mono kicker line whose " · " parts stay in order whatever their script. */
@Composable
internal fun Kicker(text: String, modifier: Modifier = Modifier, color: Color = MaterialTheme.colorScheme.onSurfaceVariant) {
    Text(
        isolatedParts(text.uppercase()),
        style = MaterialTheme.typography.labelMedium.byContent(),
        color = color, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = modifier
    )
}

/** "← Projects": the cover screen's way one level up (Back does the same). */
@Composable
internal fun BackLink(label: String, onClick: () -> Unit) {
    Text(
        (if (LocalLayoutDirection.current == LayoutDirection.Rtl) "→ " else "← ") + label,
        style = MaterialTheme.typography.labelLarge.byContent(),
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        maxLines = 1, overflow = TextOverflow.Ellipsis,
        modifier = Modifier.heightIn(min = 44.dp).clickable(role = Role.Button, onClick = onClick)
            .padding(vertical = 12.dp, horizontal = 2.dp)
    )
}

/** A project's emoji on a tile tinted with its colour. */
@Composable
internal fun ProjectTile(icon: String, color: String, size: Dp = 32.dp) {
    // The project's colour in this theme's value when it is one of the hues; its tint behind the icon.
    val tint = hues.fromHex(color) ?: MaterialTheme.colorScheme.primary
    Box(
        Modifier.size(size).clip(RoundedCornerShape(8.dp)).background(hues.tint(tint)),
        contentAlignment = Alignment.Center
    ) { Text(icon.ifBlank { "📁" }, fontSize = (size.value * 0.52f).sp) }
}

/** How far a project's decisions are: a ring that fills with the decided share. */
@Composable
internal fun DecidedRing(decided: Int, total: Int, size: Dp = 22.dp) {
    val track = MaterialTheme.colorScheme.outlineVariant
    val accent = MaterialTheme.colorScheme.primary
    Canvas(Modifier.size(size)) {
        val w = 2.5.dp.toPx()
        val inset = w / 2
        val arcSize = Size(this.size.width - w, this.size.height - w)
        drawArc(track, 0f, 360f, false, Offset(inset, inset), arcSize, style = Stroke(w))
        if (total > 0 && decided > 0) {
            drawArc(accent, -90f, 360f * decided / total, false, Offset(inset, inset), arcSize, style = Stroke(w, cap = StrokeCap.Round))
        }
    }
}

/** A thin progress bar (budget). Over 100% the bar is full and red. */
@Composable
internal fun Bar(pct: Int, over: Boolean, modifier: Modifier = Modifier) {
    val track = MaterialTheme.colorScheme.outlineVariant
    val fill = if (over) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary
    val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
    Canvas(modifier.fillMaxWidth().height(4.dp)) {
        drawRoundRect(track, cornerRadius = androidx.compose.ui.geometry.CornerRadius(2.dp.toPx()))
        val w = size.width * pct.coerceIn(0, 100) / 100f
        drawRoundRect(fill, topLeft = Offset(if (rtl) size.width - w else 0f, 0f), size = Size(w, size.height),
            cornerRadius = androidx.compose.ui.geometry.CornerRadius(2.dp.toPx()))
    }
}

/** Colour of a decision: chosen = success, shortlist = accent, rejected / option = muted. */
@Composable
internal fun decisionColor(d: String): Color = when (d) {
    "chosen" -> Success
    "shortlist" -> MaterialTheme.colorScheme.primary
    else -> MaterialTheme.colorScheme.onSurfaceVariant
}

/** "✓ CHOSEN" / "SHORTLIST" / "REJECTED" / "OPTION" in mono. */
@Composable
internal fun DecisionPill(decision: String, modifier: Modifier = Modifier) {
    val lang = LocalProjectLang.current
    val c = decisionColor(decision)
    Text(
        (if (decision == "chosen") "✓ " else "") + ProjectsCore.decisionLabel(decision, lang).uppercase(),
        style = MaterialTheme.typography.labelSmall,
        color = c,
        maxLines = 1,
        modifier = modifier.border(1.dp, c.copy(alpha = if (decision.isEmpty() || decision == "rejected") 0.35f else 0.7f), RoundedCornerShape(6.dp))
            .padding(horizontal = 6.dp, vertical = 2.dp)
    )
}

/** One value as its column type shows it; [best] marks it in the accent. */
@Composable
internal fun ValueText(
    shown: ValueShown,
    modifier: Modifier = Modifier,
    best: Boolean = false,
    maxLines: Int = 2,
    style: TextStyle = MaterialTheme.typography.bodyMedium,
    onLink: ((String) -> Unit)? = null
) {
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    val accent = MaterialTheme.colorScheme.primary
    when (shown.style) {
        ValueShown.Style.STARS -> Row(modifier) {
            Text("★".repeat(shown.stars), style = style, color = if (best) accent else MaterialTheme.colorScheme.onSurface)
            Text("★".repeat(5 - shown.stars), style = style, color = MaterialTheme.colorScheme.outlineVariant)
        }
        else -> {
            val color = when {
                best -> accent
                shown.style == ValueShown.Style.NONE -> muted.copy(alpha = 0.6f)
                shown.style == ValueShown.Style.YES -> Success
                shown.style == ValueShown.Style.NO -> muted
                else -> MaterialTheme.colorScheme.onSurface
            }
            val numeric = shown.style == ValueShown.Style.NUMBER || shown.style == ValueShown.Style.DATE
            val link = shown.link
            Text(
                shown.text,
                style = style.copy(
                    fontWeight = if (best || numeric) FontWeight.Medium else style.fontWeight,
                    textDirection = if (numeric || shown.style == ValueShown.Style.LINK) TextDirection.Ltr else TextDirection.Content,
                    fontFeatureSettings = if (numeric) "tnum" else null,
                    // Links are underlined, not coloured: the accent marks the best values.
                    textDecoration = if (shown.style == ValueShown.Style.LINK) androidx.compose.ui.text.style.TextDecoration.Underline else null
                ),
                color = color,
                maxLines = maxLines,
                overflow = TextOverflow.Ellipsis,
                modifier = if (link != null && onLink != null) modifier.clickable { onLink(link) } else modifier
            )
        }
    }
}

/** A hairline-bordered box (budget, section cards). */
@Composable
internal fun Boxed(modifier: Modifier = Modifier, accent: Boolean = false, onClick: (() -> Unit)? = null, content: @Composable ColumnScope.() -> Unit) {
    val shape = RoundedCornerShape(10.dp)
    Column(
        modifier.clip(shape)
            .border(1.dp, if (accent) MaterialTheme.colorScheme.primary.copy(alpha = 0.6f) else MaterialTheme.colorScheme.outlineVariant, shape)
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(14.dp),
        content = content
    )
}

/** The editors and the "Delete …?" question; composed once wherever the place shows. */
@Composable
internal fun ProjectsOverlays(c: ProjectsController) {
    val editor by c.editor.collectAsState()
    val confirm by c.confirm.collectAsState()
    editor?.let { ProjectsEditorDialog(c, it) }
    confirm?.let { q ->
        AlertDialog(
            onDismissRequest = { c.dismissConfirm() },
            title = { Text(q.title, style = MaterialTheme.typography.headlineSmall.byContent()) },
            text = if (q.message.isNotEmpty()) ({ Text(q.message, style = MaterialTheme.typography.bodyMedium.byContent()) }) else null,
            confirmButton = {
                InkButton(q.action, emphasis = InkEmphasis.SOLID) { c.dismissConfirm(); q.onYes() }
            },
            dismissButton = { InkButton(q.cancel, emphasis = InkEmphasis.BARE) { c.dismissConfirm() } },
            containerColor = MaterialTheme.colorScheme.surface
        )
    }
}
