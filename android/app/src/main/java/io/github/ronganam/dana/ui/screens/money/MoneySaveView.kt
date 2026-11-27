package io.github.ronganam.dana.ui.screens.money

import io.github.ronganam.dana.ui.components.rememberCopyText
import io.github.ronganam.dana.data.money.MoneyFormat
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.money.MoneyLedger
import io.github.ronganam.dana.data.money.MoneySave
import io.github.ronganam.dana.data.money.OfferTable
import io.github.ronganam.dana.data.money.SavePart
import io.github.ronganam.dana.data.money.SavingItem
import io.github.ronganam.dana.ui.components.InkButton
import io.github.ronganam.dana.ui.components.InkChoice
import io.github.ronganam.dana.ui.components.InkEmphasis
import io.github.ronganam.dana.ui.components.MetaLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.theme.Danger
import io.github.ronganam.dana.ui.theme.Info
import io.github.ronganam.dana.ui.theme.Success
import io.github.ronganam.dana.ui.theme.Warning
import io.github.ronganam.dana.ui.viewmodels.MoneyController
import io.github.ronganam.dana.ui.viewmodels.MoneyDetail
import io.github.ronganam.dana.ui.viewmodels.MoneyView
import io.github.ronganam.dana.ui.viewmodels.ThreadContext
import io.github.ronganam.dana.ui.viewmodels.TxnFilter
import java.time.LocalDate

/** English sentences with Hebrew runs (titles, lines): left to right, the Hebrew isolated. */
private val Sentence = androidx.compose.ui.text.TextStyle(textDirection = TextDirection.Ltr)

private fun today(asOf: String?): LocalDate = runCatching { LocalDate.parse(asOf) }.getOrDefault(LocalDate.now())

/**
 * Save (web: the Save view): the two tiles and the source line, then Ways to
 * save and Spending to watch, each in sections (Now, Coming up, In progress,
 * Watching, Done, Your choices) that collapse to their header and count.
 * Every item stays listed whatever its status; Your choices keep their way
 * back on the card. A card opens the item ([MoneyDetail.Saving]). The two
 * parts sit side by side from 640dp.
 */
@Composable
internal fun SaveView(money: MoneyController, modifier: Modifier = Modifier) {
    val ledger = money.ledger
    val revision by money.revision.collectAsState()
    val sv by ledger.savings.collectAsState()
    val error by ledger.savingsError.collectAsState()
    LaunchedEffect(revision) { ledger.loadSavings() }
    BoxWithConstraints(modifier.fillMaxSize()) {
        val wide = maxWidth >= 640.dp
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 18.dp, vertical = 14.dp)) {
            val s = sv
            if (s == null) Muted(error ?: "Loading…")
            else {
                val day = s.asOf
                val t = MoneySave.tiles(s, today(day))
                Row(horizontalArrangement = Arrangement.spacedBy(14.dp), modifier = Modifier.fillMaxWidth().height(IntrinsicSize.Min)) {
                    Tile("Could save", t.could, t.couldSub, Success, Modifier.weight(1f).fillMaxHeight())
                    Tile("Saved so far", MoneyFormat.whole(t.saved), t.savedSub, MaterialTheme.colorScheme.onSurface, Modifier.weight(1f).fillMaxHeight())
                }
                Muted(t.source, Modifier.padding(top = 8.dp))
                val parts = remember(s) { MoneySave.parts(s.items, day) }
                if (wide) Row(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(22.dp)) {
                    parts.forEach { p -> Column(Modifier.weight(1f)) { Part(money, p, day) } }
                } else parts.forEach { p -> Part(money, p, day) }
            }
        }
    }
}

@Composable
private fun Tile(label: String, value: String, sub: String, color: Color, modifier: Modifier) {
    Column(modifier.border(1.dp, MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(14.dp)).padding(14.dp)) {
        MetaLabel(label)
        Text(value, style = MaterialTheme.typography.headlineMedium.merge(MoneyNumbers), color = color, modifier = Modifier.padding(top = 4.dp))
        if (sub.isNotEmpty()) Muted(sub, Modifier.padding(top = 2.dp))
    }
}

@Composable
private fun Part(money: MoneyController, p: SavePart, day: String?) {
    val ledger = money.ledger
    val collapsed by ledger.collapsed.collectAsState()
    val detail by money.detail.collectAsState()
    val open = (detail as? MoneyDetail.Saving)?.key
    Column(Modifier.fillMaxWidth().padding(top = 18.dp)) {
        Row(verticalAlignment = Alignment.Bottom) {
            Text(p.label, style = MaterialTheme.typography.headlineSmall, modifier = Modifier.weight(1f))
            if (p.count > 0) MetaLabel("${p.count}")
        }
        Rule(Modifier.padding(top = 6.dp))
        if (p.count == 0) Muted(
            (if (p.group == "watch") "Nothing in your spending stands out right now." else "No ways to save found right now.") + " Dana looks again after every sync.",
            Modifier.padding(vertical = 12.dp)
        )
        p.sections.forEach { s ->
            val id = "${p.group}:${s.key}"
            val shut = id in collapsed
            Row(
                Modifier.fillMaxWidth().clickable(role = Role.Button) { ledger.toggleSection(id) }.heightIn(min = 40.dp).padding(vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(if (shut) "›" else "⌄", color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.width(18.dp))
                MetaLabel(s.label, Modifier.weight(1f), color = MaterialTheme.colorScheme.onSurface)
                MetaLabel("${s.items.size}")
            }
            if (!shut) s.items.forEach { it2 -> SavingCard(money, it2, s.key, day, selected = it2.key == open) }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun SavingCard(money: MoneyController, it: SavingItem, section: String, day: String?, selected: Boolean) {
    val ledger = money.ledger
    val busy by ledger.saveBusy.collectAsState()
    val today = today(day)
    val line = MoneySave.statusLine(it, today)
    val tp = if (it.target != null) MoneySave.targetProgress(it.target) else null
    val oc = if (section == "done" || section == "doing") MoneySave.outcomeText(it.outcome) else null
    val accent = MaterialTheme.colorScheme.primary
    Column(
        Modifier.fillMaxWidth()
            .pickedRow(selected, accent, MaterialTheme.colorScheme.surfaceContainerLow)
            .clickable(role = Role.Button) { money.openSaving(it.key, it.title) }
            .padding(start = 12.dp, end = 6.dp, top = 10.dp, bottom = 12.dp)
    ) {
        Text(
            MoneySave.isolateHebrew(it.title),
            style = MaterialTheme.typography.bodyLarge.merge(Sentence).copy(fontWeight = FontWeight.SemiBold),
            color = if (it.active) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant
        )
        if (it.summary.isNotEmpty()) Text(
            MoneySave.isolateHebrew(it.summary), style = MaterialTheme.typography.bodySmall.merge(Sentence),
            color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 3, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 2.dp)
        )
        Chips(it, Modifier.padding(top = 6.dp))
        if (tp != null) Column(Modifier.padding(top = 8.dp)) {
            Bar(tp.bar, tp.over)
            Muted(listOf(tp.text, tp.track, tp.left).filter { s -> s.isNotEmpty() }.joinToString(" · "), Modifier.padding(top = 4.dp))
        } else if (oc != null) Text(
            oc.title + if (oc.sub.isNotEmpty()) " · ${oc.sub}" else "",
            style = MaterialTheme.typography.bodySmall.copy(fontWeight = FontWeight.Medium),
            color = outcomeColor(oc.tone), modifier = Modifier.padding(top = 6.dp)
        )
        if (line.isNotEmpty() || !it.active) Row(Modifier.padding(top = 6.dp)) {
            if (line.isNotEmpty()) MetaLabel(line, color = MaterialTheme.colorScheme.onSurface)
            if (line.isNotEmpty() && !it.active) MetaLabel("  ·  ")
            if (!it.active) MetaLabel("Dana no longer sees this")
        }
        if (section == "choices") FlowRow(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            MoneySave.actions(it).filter { a -> !a.input && a.body != null }.forEach { a ->
                InkButton(a.label, enabled = it.key !in busy) { ledger.patchSaving(it.key, a.body!!) }
            }
        }
    }
    Rule(Modifier.padding(start = 12.dp))
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Chips(it: SavingItem, modifier: Modifier = Modifier) {
    val chip = MoneySave.savingChip(it)
    val effort = MoneySave.effortText(it.effort)
    val basis = MoneySave.basisChip(it)
    FlowRow(modifier, horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        chip?.let { c -> Badge(c.text, if (c.kind == "save") "refund" else "plain") }
        if (effort.isNotEmpty()) Badge(effort, "plain")
        basis?.let { b -> Badge(b, "plain") }
        it.timing?.label?.let { l -> Badge(MoneySave.isolateHebrew(l), "pending") }
        if (it.isNew && it.status == "open") Badge("New", "new")
    }
}

@Composable
private fun Bar(pct: Int, over: Boolean) {
    val track = MaterialTheme.colorScheme.surfaceContainerHigh
    Box(Modifier.fillMaxWidth().height(6.dp).clip(RoundedCornerShape(3.dp)).background(track)) {
        Box(Modifier.fillMaxWidth(pct.coerceIn(0, 100) / 100f).fillMaxHeight().background(if (over) Danger else Success))
    }
}

@Composable
private fun outcomeColor(tone: String): Color = when (tone) {
    "good" -> Success
    "warn" -> Warning
    else -> MaterialTheme.colorScheme.onSurfaceVariant
}

// ---------- the item ----------

/**
 * A saving opened: outcome, target (progress, past months, Cut back / Edit
 * target), why (sparkline, lines, a link), the math, questions, prices (only
 * on "Check prices"), subscription lines, steps, what to say (Hebrew, Copy),
 * the actions of its status and Ask Dana. Each write updates in place, with Undo.
 */
@Composable
internal fun SavingDetailPane(money: MoneyController, key: String, onClose: () -> Unit, modifier: Modifier = Modifier) {
    val ledger = money.ledger
    val sv by ledger.savings.collectAsState()
    val revision by money.revision.collectAsState()
    // Full screen on the cover the list is not composed, so the pane reloads after a write or an Undo itself.
    LaunchedEffect(key, revision) { ledger.loadSavings() }
    val item = sv?.item(key)
    Column(modifier.fillMaxSize()) {
        PaneTop(if (item?.watch == true) "Spending to watch" else "Way to save", onClose)
        if (item == null) Muted(if (sv == null) "Loading…" else "It isn't there any more.", Modifier.padding(18.dp))
        else SavingBody(money, item, sv?.asOf)
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun SavingBody(money: MoneyController, it: SavingItem, day: String?) {
    val ledger = money.ledger
    val busySet by ledger.saveBusy.collectAsState()
    val targetFor by ledger.targetFor.collectAsState()
    val busy = it.key in busySet
    val today = today(day)
    val uri = LocalUriHandler.current
    val copy = rememberCopyText()
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 18.dp).padding(bottom = 28.dp)) {
        Text(MoneySave.isolateHebrew(it.title), style = MaterialTheme.typography.titleLarge.merge(Sentence).copy(fontWeight = FontWeight.SemiBold))
        if (it.summary.isNotEmpty()) Text(MoneySave.isolateHebrew(it.summary), style = MaterialTheme.typography.bodyMedium.merge(Sentence), color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 4.dp))
        Chips(it, Modifier.padding(top = 8.dp))
        val line = MoneySave.statusLine(it, today)
        if (line.isNotEmpty() || !it.active) MetaLabel(listOf(line, if (!it.active) "Dana no longer sees this" else "").filter { s -> s.isNotEmpty() }.joinToString(" · "), Modifier.padding(top = 8.dp), color = MaterialTheme.colorScheme.onSurface)

        MoneySave.outcomeText(it.outcome)?.let { oc ->
            Column(Modifier.padding(top = 14.dp).fillMaxWidth().border(1.dp, outcomeColor(oc.tone).copy(alpha = 0.5f), RoundedCornerShape(10.dp)).padding(12.dp)) {
                Text(oc.title + if (oc.sub.isNotEmpty()) " · ${oc.sub}" else "", style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold), color = outcomeColor(oc.tone))
                if (oc.detail.isNotEmpty()) Muted(MoneySave.isolateHebrew(oc.detail), Modifier.padding(top = 2.dp))
            }
        }

        if (it.watch && (it.target != null || targetFor == it.key)) {
            Kicker("Monthly target")
            TargetBlock(money, it, editing = targetFor == it.key, busy = busy)
        }

        val ev = it.evidence
        val spark = MoneySave.spark(ev.series, 180f, 44f, today = today)
        val link = MoneySave.link(ev.link)
        if (ev.lines.isNotEmpty() || spark != null || link != null) {
            Kicker("Why")
            spark?.let { sp ->
                Sparkline(sp, Modifier.padding(bottom = 2.dp))
                MetaLabel(sp.range, Modifier.padding(bottom = 6.dp))
            }
            ev.lines.forEach { l -> Bullet(l) }
            link?.let { l ->
                TextLink(l.label) {
                    if (l.view == "transactions") money.openTransactions(TxnFilter(q = l.q, category = l.category, allTime = true))
                    else viewOf(l.view)?.let { v -> money.select(v) }
                }
            }
        }
        if (it.math.isNotEmpty()) {
            Kicker("The math")
            it.math.forEach { l -> Bullet(l) }
        }
        val needs = it.market?.needsAnswer != null
        if (it.questions.isNotEmpty() && !needs) {
            Kicker(if (it.questions.size > 1) "A few questions" else "A question")
            Questions(money, it, busy)
        }
        if (it.market != null) {
            Kicker("Prices")
            Prices(money, it, today, busy) { url -> runCatching { uri.openUri(url) } }
        }
        if (it.subitems.isNotEmpty()) {
            Kicker("Your subscriptions")
            it.subitems.forEach { s ->
                val x = MoneySave.subitemText(s, today)
                Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f).padding(end = 8.dp)) {
                        Text(s.merchant, style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium, textDirection = TextDirection.Content),
                            color = if (s.active) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant)
                        Muted(listOf(x.amount, x.yearly, x.next).filter { t -> t.isNotEmpty() }.joinToString(" · "))
                    }
                    Segments(listOf("keep" to "Keep", "cancel" to "Cancel"), s.choice, enabled = !busy) { c -> ledger.chooseSub(it, s.key, c) }
                }
                Rule()
            }
        }
        if (it.steps.isNotEmpty()) {
            Kicker("What to do")
            it.steps.forEachIndexed { i, s ->
                Row(Modifier.padding(vertical = 3.dp)) {
                    Text("${i + 1}.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.width(22.dp))
                    Column(Modifier.weight(1f)) {
                        Text(MoneySave.isolateHebrew(s.text), style = MaterialTheme.typography.bodyMedium.merge(Sentence))
                        val host = MoneySave.hostOf(s.url)
                        if (host.isNotEmpty()) TextLink("$host ↗") { runCatching { uri.openUri(s.url!!) } }
                    }
                }
            }
        }
        it.script?.let { sc ->
            Kicker("What to say")
            Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainerLow, RoundedCornerShape(10.dp)).padding(12.dp)) {
                Text(sc.text, style = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Rtl), modifier = Modifier.fillMaxWidth())
                InkButton("Copy", modifier = Modifier.padding(top = 8.dp)) {
                    copy(sc.text)
                    money.ctx.messages.post("Copied")
                }
            }
        }
        Rule(Modifier.padding(top = 18.dp, bottom = 12.dp))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            MoneySave.actions(it).forEach { a ->
                if (a.input && targetFor == it.key) Unit
                else InkButton(a.label, emphasis = if (a.primary) InkEmphasis.SOLID else InkEmphasis.PLAIN, enabled = !busy) {
                    if (a.input) ledger.openTarget(it) else a.body?.let { b -> ledger.patchSaving(it.key, b) }
                }
            }
        }
        Row(Modifier.padding(top = 14.dp)) {
            InkButton("Ask Dana about this →", emphasis = InkEmphasis.PRIMARY) { money.ask(ThreadContext("saving", it.key, it.title)) }
        }
    }
}

private fun viewOf(view: String): MoneyView? = when (view) {
    "overview" -> MoneyView.OVERVIEW
    "spending" -> MoneyView.SPENDING
    "cashflow", "plan" -> MoneyView.CASHFLOW
    "save" -> MoneyView.SAVE
    "investments" -> MoneyView.INVESTMENTS
    "meals" -> MoneyView.MEALS
    "transactions" -> MoneyView.TRANSACTIONS
    else -> null
}

@Composable
private fun Bullet(text: String) {
    Row(Modifier.padding(vertical = 2.dp)) {
        Text("·", color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.width(14.dp))
        Text(MoneySave.isolateHebrew(text), style = MaterialTheme.typography.bodyMedium.merge(Sentence), modifier = Modifier.weight(1f))
    }
}

@Composable
private fun Sparkline(sp: io.github.ronganam.dana.data.money.Spark, modifier: Modifier = Modifier) {
    val line = MaterialTheme.colorScheme.primary
    Canvas(modifier.size(180.dp, 44.dp).semantics { contentDescription = "${sp.count} amounts, ${sp.range}" }) {
        val sx = size.width / 180f
        val sy = size.height / 44f
        val path = Path()
        sp.points.forEachIndexed { i, (x, y) -> if (i == 0) path.moveTo(x * sx, y * sy) else path.lineTo(x * sx, y * sy) }
        val area = Path().apply {
            addPath(path)
            lineTo(sp.points.last().first * sx, sp.base * sy)
            lineTo(sp.points.first().first * sx, sp.base * sy)
            close()
        }
        drawPath(area, line.copy(alpha = 0.12f))
        drawPath(path, line, style = Stroke(width = 1.5.dp.toPx()))
        val (lx, ly) = sp.points.last()
        drawCircle(line, 2.6.dp.toPx(), Offset(lx * sx, ly * sy))
    }
}

@Composable
private fun TargetBlock(money: MoneyController, it: SavingItem, editing: Boolean, busy: Boolean) {
    val ledger = money.ledger
    val draft by ledger.targetDraft.collectAsState()
    val error by ledger.targetError.collectAsState()
    MoneySave.targetProgress(it.target)?.let { tp ->
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(tp.text, style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium), modifier = Modifier.weight(1f))
            if (tp.track.isNotEmpty()) Text(tp.track, style = MaterialTheme.typography.bodySmall, color = if (tp.tone == "bad") Danger else if (tp.tone == "good") Success else MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Bar(tp.bar, tp.over)
        if (tp.left.isNotEmpty()) Muted(tp.left, Modifier.padding(top = 4.dp))
        if (tp.months.isNotEmpty()) Row(Modifier.padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            tp.months.forEach { m -> Text("${if (m.under) "✓" else "×"} ${m.label} ${m.text}", style = MaterialTheme.typography.bodySmall, color = if (m.under) Success else Danger) }
        }
        if (tp.saved.isNotEmpty()) Text(tp.saved, style = MaterialTheme.typography.bodySmall.copy(fontWeight = FontWeight.Medium), color = Success, modifier = Modifier.padding(top = 4.dp))
    }
    if (editing) Column(Modifier.padding(top = 10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Spend at most ${MoneyFormat.symbol}", style = MaterialTheme.typography.bodyMedium)
            InkField(draft, { v -> ledger.setTargetDraft(v) }, "650", Modifier.width(110.dp).padding(horizontal = 6.dp), keyboard = KeyboardType.Number, onDone = { ledger.submitTarget(it.key) }, ltr = true)
            Text("a month", style = MaterialTheme.typography.bodyMedium)
        }
        it.suggestedTarget?.let { s -> Muted("Halfway back to before is ${MoneyFormat.whole(s)}.", Modifier.padding(top = 6.dp)) }
        error?.let { e -> Text(e, color = Danger, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 4.dp)) }
        Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            InkButton(if (it.target != null) "Save target" else "Set target", emphasis = InkEmphasis.SOLID, enabled = !busy) { ledger.submitTarget(it.key) }
            InkButton("Cancel") { ledger.closeTarget() }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Questions(money: MoneyController, it: SavingItem, busy: Boolean) {
    it.questions.forEach { q ->
        Text(MoneySave.isolateHebrew(q.text), style = MaterialTheme.typography.bodyMedium.merge(Sentence), modifier = Modifier.padding(top = 4.dp, bottom = 6.dp))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            q.options.forEach { o -> InkChoice(o.label, selected = q.isAnswer(o)) { if (!busy) money.ledger.answer(it, q.id, o.value) } }
        }
    }
}

@Composable
private fun Prices(money: MoneyController, it: SavingItem, today: LocalDate, busy: Boolean, open: (String) -> Unit) {
    val ledger = money.ledger
    val notes by ledger.checkNotes.collectAsState()
    MoneySave.marketView(it.market, today)?.let { v -> PricesBody(money, it, v, today, busy, notes[it.key], open) }
}

@Composable
private fun PricesBody(money: MoneyController, it: SavingItem, v: io.github.ronganam.dana.data.money.MarketView, today: LocalDate, busy: Boolean, note: io.github.ronganam.dana.data.money.CheckReply?, open: (String) -> Unit) {
    val ledger = money.ledger
    if (v.state == "checking") Row(verticalAlignment = Alignment.CenterVertically) {
        CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 1.5.dp, color = Info)
        Spacer(Modifier.width(8.dp))
        Muted(v.text)
    } else if (v.text.isNotEmpty()) Text(v.text, style = MaterialTheme.typography.bodyMedium, color = if (v.state == "failed") Warning else MaterialTheme.colorScheme.onSurfaceVariant)
    if (v.question != null && it.questions.isNotEmpty()) Questions(money, it, busy)
    val table = if (v.offers) MoneySave.offerRows(it.market, today) else null
    table?.let { Offers(it, open) }
    if (table != null) {
        Muted(v.checked + if (v.stale.isNotEmpty()) " · ${v.stale}" else "", Modifier.padding(top = 8.dp))
        if (table.sources.isNotEmpty()) {
            MetaLabel("Sources", Modifier.padding(top = 6.dp))
            table.sources.forEach { s -> TextLink("${s.title} ↗") { open(s.url) } }
        }
    }
    note?.takeIf { n -> n.text.isNotEmpty() }?.let { n ->
        Text(n.text, style = MaterialTheme.typography.bodyMedium, color = if (n.kind == "error") Danger else Warning, modifier = Modifier.padding(top = 6.dp))
    }
    if (v.button != null && v.state != "checking") InkButton(if (busy) "…" else v.button, modifier = Modifier.padding(top = 10.dp), emphasis = InkEmphasis.PRIMARY, enabled = !busy) { ledger.startCheck(it.key) }
}

/** The offers as stacked rows (the pane is narrow): name and plan, then each cell as "label: text", the source. */
@Composable
private fun Offers(t: OfferTable, open: (String) -> Unit) {
    Column(Modifier.padding(top = 8.dp)) {
        t.rows.forEach { r ->
            Rule()
            Column(Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
                Text(r.name, style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold, textDirection = TextDirection.Content))
                if (t.hasPlan && r.plan.isNotEmpty()) Text(r.plan, style = MaterialTheme.typography.bodySmall.copy(textDirection = TextDirection.Content), color = MaterialTheme.colorScheme.onSurfaceVariant)
                r.cells.filter { c -> c.text.isNotEmpty() }.forEach { c ->
                    Row(Modifier.padding(top = 3.dp)) {
                        Text(c.label, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.width(118.dp))
                        Column(Modifier.weight(1f)) {
                            Text(
                                MoneySave.isolateHebrew(c.text),
                                style = MaterialTheme.typography.bodySmall.copy(fontWeight = if (c.strong) FontWeight.SemiBold else FontWeight.Normal, textDirection = TextDirection.Content),
                                color = if (c.muted) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface
                            )
                            if (c.sub.isNotEmpty()) Muted(c.sub)
                        }
                    }
                }
                r.source?.let { s -> TextLink("${s.title} ↗") { open(s.url) } }
            }
        }
        Rule()
    }
}
