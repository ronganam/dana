package io.github.ronganam.dana.ui.screens.money

import io.github.ronganam.dana.data.money.MoneyFormat
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.money.MoneyLedger
import io.github.ronganam.dana.data.money.Txn
import io.github.ronganam.dana.ui.components.InkButton
import io.github.ronganam.dana.ui.components.InkChoice
import io.github.ronganam.dana.ui.components.InkEmphasis
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.viewmodels.MoneyController
import io.github.ronganam.dana.ui.viewmodels.MoneyView
import io.github.ronganam.dana.ui.viewmodels.ThreadContext
import io.github.ronganam.dana.ui.viewmodels.TxnFilter
import java.time.LocalDate

/**
 * A transaction in full (web: the detail pane / sheet): amount, merchant,
 * dates, account, badges and why it shows so, the category (a picker: always
 * for this merchant or only this one), Counts as, One-off and Yearly, the
 * facts, the note (saved on Done) and Ask Dana. Every change is a PATCH with
 * an Undo snackbar.
 */
@Composable
internal fun TxnDetailPane(money: MoneyController, id: String, onClose: () -> Unit, modifier: Modifier = Modifier) {
    val ledger = money.ledger
    val revision by money.revision.collectAsState()
    val state by ledger.detail.collectAsState()
    val picker by ledger.picker.collectAsState()
    LaunchedEffect(id, revision) { ledger.loadDetail(id) }
    val s = state?.takeIf { it.id == id }
    val t = s?.txn
    Column(modifier.fillMaxSize()) {
        PaneTop(if (picker == id) "Category" else "Transaction", onClose = { if (picker == id) ledger.openPicker(null) else onClose() })
        when {
            t == null -> Muted(s?.error ?: "Loading…", Modifier.padding(18.dp))
            picker == id -> CategoryPicker(money, t)
            else -> TxnBody(money, t, s.full, s.error)
        }
    }
}

@Composable
private fun TxnBody(money: MoneyController, t: Txn, full: Boolean, error: String?) {
    val ledger = money.ledger
    val sources by money.sources.collectAsState()
    val accounts = remember(sources) { accountRefs(sources) }
    val account = accounts[t.accountId]
    val company = account?.company?.takeIf { t.accountKind == "card" }
    val tone = MoneyLedger.amountTone(t)
    val time = MoneyLedger.timeOf(t.occurredAt)
    val explain = MoneyLedger.statusExplain(t, company)
    val flow = MoneyLedger.flowChip(t)
    val today = LocalDate.now()
    val acct = account?.name ?: t.accountLabel

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 18.dp).padding(bottom = 28.dp)) {
        Text(
            t.title,
            style = MaterialTheme.typography.titleLarge.copy(fontWeight = FontWeight.SemiBold, textDirection = TextDirection.Content),
            modifier = Modifier.padding(top = 4.dp)
        )
        Text(
            MoneyLedger.amountText(t),
            style = MaterialTheme.typography.displaySmall.merge(MoneyNumbers),
            color = toneColor(tone),
            modifier = Modifier.padding(top = 4.dp)
        )
        MonoLabel(listOf(MoneyLedger.longDate(t.purchaseDate), time, acct).filter { it.isNotEmpty() }.joinToString(" · "), Modifier.padding(top = 2.dp))
        val badges = MoneyLedger.badges(t, company)
        if (badges.isNotEmpty()) BadgeRow(badges, Modifier.padding(top = 10.dp))
        if (explain.isNotEmpty()) Muted(explain, Modifier.padding(top = 10.dp))

        Kicker("Category")
        if (flow != null) {
            Text(flow, style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
        } else {
            Row(verticalAlignment = Alignment.CenterVertically) {
                InkChoice(if (t.category != null) "${t.categoryLabel}  ✎" else "Pick a category", selected = t.category == null) { ledger.openPicker(t.id) }
                val src = MoneyLedger.categorySourceLabel(t.categorySource)
                if (src.isNotEmpty()) Muted(src, Modifier.padding(start = 10.dp))
            }
        }

        if (full) Counts(money, t)

        Kicker("Details")
        Facts(money, t, full, company, today)

        Kicker("Note")
        NoteField(money, t)

        Row(Modifier.padding(top = 20.dp)) {
            InkButton("Ask Dana about this →", emphasis = InkEmphasis.PRIMARY) { money.ask(ThreadContext("transaction", t.id, t.title)) }
        }
        error?.let { Muted(it, Modifier.padding(top = 10.dp)) }
    }
}

/** Counts as (not on card bills) and the One-off / Yearly marks (only for spending). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Counts(money: MoneyController, t: Txn) {
    val ledger = money.ledger
    if (t.flow != "card_bill") {
        Kicker("Counts as")
        if (t.flow == null || t.flow !in MoneyLedger.FLOW_LABELS) Muted("Not set", Modifier.padding(bottom = 6.dp))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            MoneyLedger.flowChoices(t).forEach { (f, label) ->
                InkChoice(label, selected = f == t.flow) { if (f != t.flow) ledger.setFlow(t, f) }
            }
        }
    }
    if (t.spending || t.kind != null) {
        val x = t.extras
        Kicker("One-off")
        Segments(listOf("yes" to "Yes", "no" to "No", "auto" to "Automatic"), MoneyLedger.markState(x?.oneOff)) {
            if (it != MoneyLedger.markState(x?.oneOff)) ledger.setMark(t, "one_off", it)
        }
        Kicker("Yearly")
        Segments(listOf("yes" to "Yes", "no" to "No", "auto" to "Automatic"), MoneyLedger.markState(x?.yearly)) {
            if (it != MoneyLedger.markState(x?.yearly)) ledger.setMark(t, "yearly", it)
        }
        Muted(
            "${MoneyLedger.kindText(t.kind)} One-offs are left out of everyday spending and the forecast; yearly charges count a twelfth a month in fixed costs.".trim(),
            Modifier.padding(top = 8.dp)
        )
    }
}

@Composable
private fun Facts(money: MoneyController, t: Txn, full: Boolean, company: String?, today: LocalDate) {
    val x = t.extras
    val time = MoneyLedger.timeOf(t.occurredAt)
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (t.description.isNotEmpty() && t.description != t.merchant) Fact("As the bank wrote it", t.description)
        else if (full) Fact("Description", t.description.ifEmpty { "—" })
        t.memo?.let { Fact("Memo", it) }
        Fact("Bought", MoneyLedger.longDate(t.purchaseDate) + if (time.isNotEmpty()) ", $time" else "")
        if (t.chargeDate != null && t.chargeDate != t.purchaseDate) Fact("Charged", MoneyLedger.longDate(t.chargeDate))
        x?.installment?.let { i ->
            Fact("Installments", "Payment ${i.number} of ${i.total}" + (i.purchaseAmount?.let { " · ${MoneyFormat.amount(it, sign = MoneyFormat.Sign.NONE)} in all" } ?: ""))
        }
        x?.foreign?.takeIf { !MoneyFormat.isUsers(it.currency) }?.let { f -> Fact("Paid in", "${MoneyFormat.foreign(f.amount, f.currency)} → ${MoneyFormat.amount(t.amount, sign = MoneyFormat.Sign.NONE)}") }
        x?.bankCategory?.let { Fact("${company ?: "Card"}'s category", it) }
        x?.sameMerchant?.let { sm ->
            if (sm.count > 1) {
                Fact("History", "${sm.count} charges, ${MoneyFormat.amount(kotlin.math.abs(sm.total), sign = MoneyFormat.Sign.NONE)} since ${MoneyLedger.shortDate(sm.firstDate, today)}")
                TextLink("See them", Modifier.padding(start = 112.dp)) {
                    money.openTransactions(TxnFilter(q = t.merchant, allTime = true))
                    money.closeDetail()
                }
            } else if (sm.count == 1) Fact("History", "The first charge from this merchant")
        }
        x?.mealCycle?.let { mc ->
            val range = if (mc.start != null && mc.end != null) "${MoneyLedger.shortDate(mc.start, today)} – ${MoneyLedger.shortDate(mc.end, today)}" else ""
            val places = mc.places.take(3).joinToString(", ") + if (mc.places.size > 3) ", …" else ""
            Fact("Meal card", "What the budget did not cover${if (range.isNotEmpty()) ", $range" else ""}: ${mc.places.size} ${if (mc.places.size == 1) "meal" else "meals"} ($places)")
            if (money.hasAccount("meal")) TextLink("See the meal card", Modifier.padding(start = 112.dp)) { money.select(MoneyView.MEALS) }
        }
    }
}

@Composable
private fun Fact(label: String, value: String) {
    Row(Modifier.fillMaxWidth()) {
        Text(label, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.width(112.dp).padding(end = 8.dp, top = 1.dp))
        // Wrapped so a Hebrew value keeps to the start of its column, as on the web.
        Box(Modifier.weight(1f)) { Text(value, style = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content)) }
    }
}

@Composable
private fun NoteField(money: MoneyController, t: Txn) {
    val ledger = money.ledger
    val drafts by ledger.noteDrafts.collectAsState()
    val draft = drafts[t.id]
    val note = draft ?: t.note ?: ""
    val dirty = draft != null && draft != (t.note ?: "")
    InkField(
        value = note,
        onValue = { ledger.setNoteDraft(t.id, it) },
        placeholder = "Add a note — who it was with, what it was for",
        singleLine = false,
        minHeight = 64.dp,
        imeAction = ImeAction.Default
    )
    if (dirty) Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        InkButton("Done", emphasis = InkEmphasis.SOLID) { ledger.saveNote(t.id) }
        InkButton("Cancel") { ledger.setNoteDraft(t.id, null) }
    }
}

/** The category picker: always for this merchant (default) or only this one, search, the groups. */
@Composable
private fun CategoryPicker(money: MoneyController, t: Txn) {
    val ledger = money.ledger
    val forMerchant by ledger.forMerchant.collectAsState()
    val q by ledger.panelQuery.collectAsState()
    val categories by money.categories.collectAsState()
    val taxonomy = remember(categories) { MoneyLedger.taxonomy(categories) }
    BackHandler { ledger.openPicker(null) }
    Column(Modifier.fillMaxSize()) {
        Column(Modifier.padding(horizontal = 18.dp)) {
            Text(t.title, style = MaterialTheme.typography.titleMedium.copy(textDirection = TextDirection.Content), maxLines = 1, overflow = TextOverflow.Ellipsis)
            MonoLabel("${MoneyFormat.amount(t.amount)} · ${MoneyLedger.shortDate(t.purchaseDate)}", Modifier.padding(bottom = 10.dp))
            Segments(listOf("merchant" to "Always for this merchant", "one" to "Only this one"), if (forMerchant) "merchant" else "one") {
                ledger.setForMerchant(it == "merchant")
            }
            Muted(
                if (forMerchant) "Its other charges change too (unless you set them by hand), and new ones get it as they arrive." else "Only this transaction changes.",
                Modifier.padding(top = 6.dp, bottom = 10.dp)
            )
            InkField(q, { ledger.setPanelQuery(it) }, "Search — English or עברית")
        }
        Rule(Modifier.padding(top = 10.dp))
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState())) {
            CategoryList(taxonomy, q, income = t.amount > 0, current = t.category, groupsPickable = false) { key ->
                ledger.setCategory(t, key, MoneyLedger.categoryLabel(taxonomy, key), forMerchant)
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}
