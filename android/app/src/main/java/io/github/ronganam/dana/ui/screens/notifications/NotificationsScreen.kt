package io.github.ronganam.dana.ui.screens.notifications

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.pulltorefresh.PullToRefreshDefaults
import androidx.compose.material3.pulltorefresh.rememberPullToRefreshState
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.models.NotificationsOverview
import io.github.ronganam.dana.data.models.OverviewApp
import io.github.ronganam.dana.data.models.PhoneNotification
import io.github.ronganam.dana.data.models.RuleSuggestion
import io.github.ronganam.dana.data.notifications.AppIcons
import io.github.ronganam.dana.data.notifications.PhoneNotificationActions
import io.github.ronganam.dana.ui.components.InkButton
import io.github.ronganam.dana.ui.components.InkChoice
import io.github.ronganam.dana.ui.components.InkEmphasis
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.components.relativeAge
import io.github.ronganam.dana.ui.screens.common.byScript
import io.github.ronganam.dana.ui.theme.DanaSerif
import io.github.ronganam.dana.ui.viewmodels.AppRule
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

private val GUTTER = 18.dp

private val NEEDS_FLAG = Regex("""\s*\[needs [^\]]*\]\s*""", RegexOption.IGNORE_CASE)

/**
 * A summary point without the "[needs <name>]" flag the server's model sometimes
 * copies from its input (core/notification_summary.py), and whether it had it.
 */
internal fun summaryPoint(raw: String): Pair<String, Boolean> =
    if (NEEDS_FLAG.containsMatchIn(raw)) NEEDS_FLAG.replace(raw, " ").trim() to true else raw to false

/** What the Notifications list and an app's page can do. */
class NotificationsActions(
    val refresh: () -> Unit,
    val openApp: (OverviewApp) -> Unit,
    val clearNoise: () -> Unit,
    val clearApp: (OverviewApp) -> Unit,
    val answer: (RuleSuggestion, Boolean) -> Unit,
    val toggleQuietApps: () -> Unit,
    val setRule: (OverviewApp, AppRule) -> Unit,
    val clearOne: (PhoneNotification) -> Unit,
    val snooze: (PhoneNotification) -> Unit,
    val open: (PhoneNotification) -> Unit,
    val reply: (PhoneNotification, String) -> Unit,
    val press: (PhoneNotification, String) -> Unit,
    val allowAccess: () -> Unit
)

/**
 * Notifications, the list: counts, "Notifications", the noise box (Clear the
 * noise · M, or the calm line when there is none), "Dana noticed" for the
 * first rule suggestion, the apps (name, count, needs you, summary, Clear) and
 * the quiet apps folded at the end.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NotificationsListPane(
    overview: NotificationsOverview?,
    selected: String?,
    wide: Boolean,
    loading: Boolean,
    failed: Boolean,
    accessGranted: Boolean,
    quietOpen: Boolean,
    actions: NotificationsActions
) {
    val pull = rememberPullToRefreshState()
    var pullRefreshing by remember { mutableStateOf(false) }
    LaunchedEffect(loading) { if (!loading) pullRefreshing = false }
    val counts = overview?.counts

    PullToRefreshBox(
        isRefreshing = pullRefreshing, onRefresh = { pullRefreshing = true; actions.refresh() }, state = pull, modifier = Modifier.fillMaxSize(),
        indicator = {
            PullToRefreshDefaults.Indicator(
                state = pull, isRefreshing = pullRefreshing, modifier = Modifier.align(Alignment.TopCenter),
                containerColor = MaterialTheme.colorScheme.surfaceContainerHigh, color = MaterialTheme.colorScheme.primary
            )
        }
    ) {
        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(top = 20.dp, bottom = 32.dp)) {
            item(key = "head") {
                Column(Modifier.padding(horizontal = GUTTER)) {
                    MonoLabel(if (counts != null) "${counts.total} on your phone · ${counts.noise} noise" else "Your phone's notifications")
                    Text("Notifications", style = MaterialTheme.typography.displayMedium, modifier = Modifier.padding(top = 2.dp))
                    if (!accessGranted) AccessBox(actions.allowAccess)
                    if (overview != null) NoiseBox(overview, wide, actions.clearNoise)
                    overview?.ruleSuggestions?.firstOrNull()?.let { s -> NoticedBox(s, actions.answer) }
                }
            }
            when {
                overview == null && failed -> item(key = "unreachable") {
                    Calm("Can't reach Dana.", "Your apps show here once the server answers.", Modifier.padding(horizontal = GUTTER))
                }
                overview == null -> item(key = "loading") {
                    Text("Looking…", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = GUTTER, vertical = 16.dp))
                }
                else -> {
                    item(key = "apps_head") {
                        Row(Modifier.fillMaxWidth().padding(horizontal = GUTTER).padding(top = 22.dp, bottom = 8.dp)) {
                            MonoLabel("By app", modifier = Modifier.weight(1f))
                            MonoLabel(overview.apps.size.toString())
                        }
                        Rule(Modifier.padding(horizontal = GUTTER))
                    }
                    if (overview.apps.isEmpty()) item(key = "clear") {
                        Calm("All clear.", "Nothing is waiting on your phone.", Modifier.padding(horizontal = GUTTER))
                    }
                    items(overview.apps, key = { "a_" + it.packageName }) { a ->
                        AppRow(a, selected = wide && selected == a.packageName, onOpen = { actions.openApp(a) }, onClear = { actions.clearApp(a) })
                    }
                    if (overview.quietApps.isNotEmpty()) {
                        item(key = "quiet_fold") {
                            Row(
                                Modifier.fillMaxWidth().clickable(onClick = actions.toggleQuietApps).padding(horizontal = GUTTER, vertical = 13.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text("Quiet apps · ${overview.quietApps.size}", style = MaterialTheme.typography.bodyMedium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
                                MonoLabel(if (quietOpen) "hide" else "show")
                            }
                            Rule(Modifier.padding(horizontal = GUTTER))
                        }
                        if (quietOpen) items(overview.quietApps, key = { "qa_" + it.packageName }) { a ->
                            AppRow(a, selected = wide && selected == a.packageName, onOpen = { actions.openApp(a) }, onClear = { actions.clearApp(a) })
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun AccessBox(onAllow: () -> Unit) {
    Column(
        Modifier.padding(top = 14.dp).fillMaxWidth().clip(RoundedCornerShape(14.dp))
            .border(1.dp, MaterialTheme.colorScheme.primary, RoundedCornerShape(14.dp)).padding(13.dp)
    ) {
        Text("Notification access is off, so nothing new reaches Dana.", style = MaterialTheme.typography.bodyMedium)
        InkButton("Allow access", emphasis = InkEmphasis.PRIMARY, modifier = Modifier.padding(top = 10.dp), onClick = onAllow)
    }
}

/** "M are noise: X, Instagram, Messages." + Clear the noise · M; or the calm line. */
@Composable
private fun NoiseBox(o: NotificationsOverview, wide: Boolean, onClear: () -> Unit) {
    val noise = o.counts.noise
    val noisiest = (o.apps + o.quietApps).filter { it.noise > 0 }.sortedByDescending { it.noise }.take(3).map { it.app }
    Column(
        Modifier.padding(top = 14.dp).fillMaxWidth().clip(RoundedCornerShape(14.dp))
            .background(if (wide) MaterialTheme.colorScheme.background else MaterialTheme.colorScheme.surfaceContainerLow)
            .padding(13.dp)
    ) {
        Text(
            if (noise > 0) "$noise ${if (noise == 1) "is" else "are"} noise" + (if (noisiest.isNotEmpty()) ": ${noisiest.joinToString(", ")}." else ".")
            else "No noise left. What remains is worth a look.",
            style = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content)
        )
        if (noise > 0) InkButton("Clear the noise · $noise", emphasis = InkEmphasis.SOLID, modifier = Modifier.padding(top = 10.dp), onClick = onClear)
    }
}

/** "Dana noticed": the server saw a pattern and offers a rule. */
@Composable
private fun NoticedBox(s: RuleSuggestion, onAnswer: (RuleSuggestion, Boolean) -> Unit) {
    Column(
        Modifier.padding(top = 10.dp).fillMaxWidth().clip(RoundedCornerShape(14.dp))
            .border(1.dp, MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(14.dp)).padding(13.dp)
    ) {
        MonoLabel("Dana noticed")
        Text("${s.reason} Keep it quiet?", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 4.dp))
        Row(Modifier.padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            InkButton("Keep it quiet", emphasis = InkEmphasis.PRIMARY) { onAnswer(s, true) }
            InkButton("No") { onAnswer(s, false) }
        }
    }
}

@Composable
private fun Calm(title: String, line: String, modifier: Modifier = Modifier) {
    Column(modifier.padding(top = 26.dp, bottom = 10.dp)) {
        Text(title, style = MaterialTheme.typography.headlineSmall.copy(fontFamily = DanaSerif, fontStyle = FontStyle.Italic, fontSize = 26.sp))
        Text(line, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 4.dp))
    }
}

/** The app's real launcher icon (cached by [AppIcons]), else its first two letters on a tile. */
@Composable
fun AppTile(pkg: String, name: String, size: Dp = 30.dp) {
    val ctx = LocalContext.current.applicationContext
    val icon by produceState<ImageBitmap?>(AppIcons.cached(pkg)?.asImageBitmap(), pkg) {
        if (value == null) value = withContext(Dispatchers.IO) { AppIcons.load(ctx, pkg) }?.asImageBitmap()
    }
    val bmp = icon
    Box(
        Modifier.size(size).clip(RoundedCornerShape(8.dp)).background(MaterialTheme.colorScheme.surfaceContainerHigh),
        contentAlignment = Alignment.Center
    ) {
        if (bmp != null) Image(bmp, contentDescription = null, modifier = Modifier.fillMaxSize())
        else Text(name.take(2), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun AppRow(a: OverviewApp, selected: Boolean, onOpen: () -> Unit, onClear: () -> Unit) {
    val accent = MaterialTheme.colorScheme.primary
    Column(
        Modifier
            .fillMaxWidth()
            .then(
                if (selected) Modifier.background(MaterialTheme.colorScheme.background).drawBehind {
                    drawLine(accent, Offset(1.5.dp.toPx(), 0f), Offset(1.5.dp.toPx(), size.height), 3.dp.toPx())
                } else Modifier
            )
            .clickable(onClick = onOpen)
    ) {
        Row(Modifier.fillMaxWidth().padding(horizontal = GUTTER, vertical = 12.dp), verticalAlignment = Alignment.Top) {
            AppTile(a.packageName, a.app)
            Spacer(Modifier.width(11.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    buildAnnotatedString {
                        pushStyle(SpanStyle(fontWeight = FontWeight.SemiBold)); append(a.app); pop()
                        pushStyle(SpanStyle(fontFamily = MaterialTheme.typography.labelMedium.fontFamily, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant))
                        append("  ${a.count}")
                        if (a.needsYou > 0) { pushStyle(SpanStyle(color = accent)); append(" · ${a.needsYou} NEEDS YOU"); pop() }
                        if (a.rule == "alert") append(" · INTERRUPTS")
                        pop()
                    },
                    style = MaterialTheme.typography.bodyLarge.copy(fontSize = 14.5.sp),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                a.summary.take(2).forEach { raw ->
                    val (p, flagged) = summaryPoint(raw)
                    Text(
                        p,
                        style = MaterialTheme.typography.bodyMedium.byScript(p),
                        color = if (flagged) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.fillMaxWidth().padding(top = 3.dp)
                    )
                }
            }
            if (a.count > 0) InkButton("Clear", emphasis = InkEmphasis.BARE, modifier = Modifier.padding(start = 6.dp), onClick = onClear)
        }
        Rule(Modifier.padding(horizontal = GUTTER))
    }
}

/**
 * One app: its summary, how it behaves (Interrupt me / Let Dana decide /
 * Keep quiet, with a sentence for the chosen one), what's waiting with Reply
 * (when it can be answered from here) / Open / In 1 h / Clear, and Clear all.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun AppDetail(app: OverviewApp, items: List<PhoneNotification>?, onBack: (() -> Unit)?, actions: NotificationsActions) {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState())
            .padding(horizontal = if (onBack != null) GUTTER else 26.dp, vertical = 20.dp).widthIn(max = 720.dp)
    ) {
        if (onBack != null) Text(
            "← Notifications",
            style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.clickable(onClick = onBack).padding(bottom = 10.dp, top = 2.dp)
        )
        Row(verticalAlignment = Alignment.Bottom) {
            Column(Modifier.weight(1f)) {
                MonoLabel("${app.count} waiting")
                Text(app.app, style = MaterialTheme.typography.displayMedium, maxLines = 2, modifier = Modifier.padding(top = 2.dp))
            }
            if (app.count > 0) InkButton("Clear all", modifier = Modifier.padding(bottom = 6.dp)) { actions.clearApp(app) }
        }
        if (app.summary.isNotEmpty() && app.summarySource == "ai") Column(Modifier.padding(top = 12.dp)) {
            app.summary.forEach { raw ->
                val p = summaryPoint(raw).first
                Text(
                    p,
                    style = MaterialTheme.typography.bodyLarge.copy(fontSize = 16.sp, lineHeight = 22.sp).byScript(p),
                    modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp)
                )
            }
        }

        Row(Modifier.padding(top = 20.dp, bottom = 8.dp)) { MonoLabel("From this app") }
        val rule = AppRule.of(app.rule)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            AppRule.values().forEach { r -> InkChoice(r.label, selected = r == rule) { actions.setRule(app, r) } }
        }
        Text(rule.sentence, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 9.dp))

        Row(Modifier.fillMaxWidth().padding(top = 22.dp, bottom = 8.dp)) { MonoLabel("Waiting") }
        Rule()
        when {
            items == null && app.count > 0 -> Text("Loading…", style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(vertical = 12.dp))
            items.isNullOrEmpty() -> Text("Nothing waiting from this app.", style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(vertical = 12.dp))
            else -> items.forEach { n -> WaitingItem(n, app.rule == "quiet", actions) }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun WaitingItem(n: PhoneNotification, quietApp: Boolean, actions: NotificationsActions) {
    val replyButton = remember(n.key) { PhoneNotificationActions.replyButton(n.key) }
    val quick = remember(n.key, replyButton) { if (replyButton == null) emptyList() else PhoneNotificationActions.quickReplies(n.key) }
    val appButtons = remember(n.key) { PhoneNotificationActions.buttons(n.key) }
    var replying by rememberSaveable(n.id) { mutableStateOf(false) }
    var text by rememberSaveable(n.id) { mutableStateOf("") }
    val who = n.sender ?: n.title ?: n.appName
    val body = listOfNotNull(n.title?.takeIf { it != who && n.sender != null }, n.text).joinToString(" · ")
    val accent = MaterialTheme.colorScheme.primary
    Column(Modifier.fillMaxWidth().padding(vertical = 12.dp)) {
        Text(
            buildAnnotatedString {
                pushStyle(SpanStyle(fontWeight = FontWeight.SemiBold)); append(who); pop()
                pushStyle(SpanStyle(fontFamily = MaterialTheme.typography.labelMedium.fontFamily, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant))
                append("  " + relativeAge(n.postedAt).uppercase())
                if (n.bucket == "needs_you" && !quietApp) { pushStyle(SpanStyle(color = accent)); append(" · NEEDS YOU"); pop() }
                if (n.bucket == "dismiss" || quietApp) append(" · NOISE")
                pop()
            },
            style = MaterialTheme.typography.bodyLarge.byScript(who),
            modifier = Modifier.fillMaxWidth()
        )
        if (body.isNotBlank()) Text(
            body,
            style = MaterialTheme.typography.bodyMedium.byScript(body),
            maxLines = 6,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.fillMaxWidth().padding(top = 2.dp)
        )
        FlowRow(Modifier.padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            if (replyButton != null) InkButton("Reply", emphasis = InkEmphasis.PRIMARY) { replying = !replying }
            appButtons.forEach { t -> InkButton(t) { actions.press(n, t) } }
            InkButton("Open") { actions.open(n) }
            InkButton("In 1 h") { actions.snooze(n) }
            InkButton("Clear") { actions.clearOne(n) }
        }
        if (replying && replyButton != null) {
            if (quick.isNotEmpty()) FlowRow(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                quick.forEach { q -> InkButton(q) { text = q } }
            }
            val shape = RoundedCornerShape(10.dp)
            fun send() {
                if (text.isBlank()) return
                actions.reply(n, text.trim())
                text = ""
                replying = false
            }
            BasicTextField(
                value = text,
                onValueChange = { text = it },
                textStyle = MaterialTheme.typography.bodyMedium.copy(color = MaterialTheme.colorScheme.onSurface, textDirection = TextDirection.Content),
                cursorBrush = SolidColor(accent),
                maxLines = 4,
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { send() }),
                modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                decorationBox = { inner ->
                    Box(
                        Modifier.fillMaxWidth().clip(shape).background(MaterialTheme.colorScheme.background)
                            .border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape).padding(horizontal = 11.dp, vertical = 9.dp)
                    ) {
                        if (text.isEmpty()) Text("Reply to $who…", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        inner()
                    }
                }
            )
            InkButton("Send", emphasis = InkEmphasis.SOLID, enabled = text.isNotBlank(), modifier = Modifier.padding(top = 8.dp)) { send() }
        }
    }
    Rule()
}

/** The stage with no app picked (unfolded): calm. */
@Composable
fun NoAppPicked(allClear: Boolean) {
    Column(Modifier.padding(horizontal = 26.dp, vertical = 28.dp)) {
        Text(
            if (allClear) "All clear." else "Pick an app on the left.",
            style = MaterialTheme.typography.headlineSmall.copy(fontFamily = DanaSerif, fontStyle = FontStyle.Italic)
        )
        Text(
            if (allClear) "Nothing is waiting on your phone." else "Its notifications and how it behaves open here.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}
