package app.ganam.assistant.ui.screens.money

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.ganam.assistant.data.money.MoneyLedger
import app.ganam.assistant.data.money.MoneyText
import app.ganam.assistant.data.money.TaxGroup
import app.ganam.assistant.data.money.Txn
import app.ganam.assistant.data.money.TxnDay
import app.ganam.assistant.ui.components.InkButton
import app.ganam.assistant.ui.components.InkEmphasis
import app.ganam.assistant.ui.components.MonoLabel
import app.ganam.assistant.ui.components.Rule
import app.ganam.assistant.ui.screens.common.QuietEmptyState
import app.ganam.assistant.ui.viewmodels.FilterPanel
import app.ganam.assistant.ui.viewmodels.MoneyController
import app.ganam.assistant.ui.viewmodels.MoneyDetail
import app.ganam.assistant.ui.viewmodels.MoneySource
import app.ganam.assistant.ui.viewmodels.TxnFilter
import kotlinx.coroutines.delay
import java.time.LocalDate

/** An account the list and the filters can name: id → "Cal ••8763", its card company. */
internal data class AccountRef(val id: String, val kind: String, val name: String, val company: String)

internal fun accountRefs(sources: List<MoneySource>?): Map<String, AccountRef> =
    sources.orEmpty().flatMap { s ->
        s.accounts.map { a -> AccountRef(a.id, a.kind, MoneyLedger.accountName(a.kind, a.number, a.label, s.companyName), s.companyName) }
    }.associateBy { it.id }

/**
 * Transactions (web: the Transactions view): search, the filter chips bound
 * to [MoneyController.filter], the rows by purchase day under sticky headers
 * carrying the day's spending, cursor paging near the end. From 600dp of
 * width the rows spread into columns (merchant · category · account · amount).
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
internal fun TransactionsView(money: MoneyController, modifier: Modifier = Modifier) {
    val ledger = money.ledger
    val filter by money.filter.collectAsState()
    val month by money.month.collectAsState()
    val revision by money.revision.collectAsState()
    val list by ledger.txns.collectAsState()
    val sources by money.sources.collectAsState()
    val categories by money.categories.collectAsState()
    val detail by money.detail.collectAsState()
    val panel by ledger.panel.collectAsState()
    val taxonomy = remember(categories) { MoneyLedger.taxonomy(categories) }
    val accounts = remember(sources) { accountRefs(sources) }
    val openId = (detail as? MoneyDetail.Transaction)?.id
    val today = LocalDate.now()

    LaunchedEffect(filter, month, revision) { ledger.loadTxns(filter, month) }

    val shown = remember(list.rows, filter.category) {
        if (filter.category == "uncategorized") list.rows.filter { MoneyLedger.needsCategory(it) } else list.rows
    }
    val days = remember(shown) { MoneyLedger.groupByDay(shown) }
    val state = rememberLazyListState()
    // Page on: the end is near.
    val nearEnd by remember { derivedStateOf { state.layoutInfo.visibleItemsInfo.lastOrNull()?.let { it.index >= state.layoutInfo.totalItemsCount - 6 } == true } }
    LaunchedEffect(nearEnd, list.next, list.loading) { if (nearEnd && list.next != null && !list.loading) ledger.more(filter, month) }
    if (panel != null) BackHandler { ledger.openPanel(null) }

    BoxWithConstraints(modifier.fillMaxSize()) {
        val columns = maxWidth >= 600.dp
        val where = if (filter.allTime) "" else " in ${MoneyText.monthLabel(month)}"
        val filtered = !filter.copy(allTime = false).isEmpty
        LazyColumn(state = state, modifier = Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 24.dp)) {
            item(key = "tools") { TxnTools(money, filter, month, taxonomy, accounts, panel) }
            when {
                !list.loaded && list.error == null -> item(key = "loading") { Skeleton() }
                list.error != null && list.rows.isEmpty() -> item(key = "error") {
                    Column(Modifier.padding(18.dp)) {
                        Text(list.error ?: "", style = MaterialTheme.typography.bodyMedium)
                        TextLink("Try again") { ledger.loadTxns(filter, month) }
                    }
                }
                shown.isEmpty() && list.next == null -> item(key = "empty") {
                    Column {
                        QuietEmptyState(
                            title = if (filtered) "Nothing matches$where." else "Nothing$where yet.",
                            message = if (filtered) "Try fewer filters." else "New transactions show up here after the next sync."
                        )
                        Row(Modifier.padding(horizontal = 28.dp), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                            if (filtered) TextLink("Clear filters") { money.setFilter(TxnFilter(allTime = filter.allTime)) }
                            if (filtered && !filter.allTime) TextLink("Search all time") { money.setFilter(filter.copy(allTime = true)) }
                        }
                    }
                }
                else -> dayRows(days, columns, openId, accounts, today) { t -> money.openTransaction(t.id, t.title) }
            }
            if (list.loaded) item(key = "end") {
                Box(Modifier.fillMaxWidth().padding(vertical = 18.dp), contentAlignment = Alignment.Center) {
                    when {
                        list.next != null -> if (list.loading) MonoLabel("Loading…") else TextLink("Show more") { ledger.more(filter, month) }
                        list.rows.size > 8 -> Muted("That's everything$where.")
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
private fun LazyListScope.dayRows(
    days: List<TxnDay>,
    columns: Boolean,
    openId: String?,
    accounts: Map<String, AccountRef>,
    today: LocalDate,
    onOpen: (Txn) -> Unit
) {
    days.forEach { d ->
        stickyHeader(key = "day_${d.date}") { DayHeader(d, today) }
        items(d.items, key = { it.id }) { t ->
            TxnRow(t, accounts[t.accountId], columns, selected = t.id == openId) { onOpen(t) }
        }
    }
}

@Composable
private fun DayHeader(d: TxnDay, today: LocalDate) {
    Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.background)) {
        Row(Modifier.fillMaxWidth().padding(start = 18.dp, end = 18.dp, top = 14.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            MonoLabel(MoneyLedger.dayLabel(d.date, today), Modifier.weight(1f))
            if (d.income > 0) Text(MoneyLedger.money(d.income, sign = "plus"), style = MaterialTheme.typography.labelMedium.merge(MoneyNumbers), color = toneColor("in"))
            if (d.income > 0 && d.spent != 0L) Spacer(Modifier.width(10.dp))
            when {
                d.spent > 0 -> Text(MoneyLedger.money(d.spent, sign = "none"), style = MaterialTheme.typography.labelMedium.merge(MoneyNumbers), color = MaterialTheme.colorScheme.onSurfaceVariant)
                d.spent < 0 -> Text(MoneyLedger.money(-d.spent, sign = "plus"), style = MaterialTheme.typography.labelMedium.merge(MoneyNumbers), color = toneColor("in"))
            }
        }
        Rule()
    }
}

/** One transaction: merchant in its own direction, category, account, time, badges, the amount. */
@Composable
private fun TxnRow(t: Txn, account: AccountRef?, columns: Boolean, selected: Boolean, onClick: () -> Unit) {
    val tone = MoneyLedger.amountTone(t)
    val badges = MoneyLedger.badges(t, account?.company?.takeIf { t.accountKind == "card" }) + listOfNotNull(MoneyLedger.kindBadge(t))
    val category = MoneyLedger.flowChip(t) ?: if (t.category != null) t.categoryLabel else "Uncategorized"
    val uncategorized = t.category == null && MoneyLedger.flowChip(t) == null
    val acct = account?.name ?: t.accountLabel
    val time = MoneyLedger.timeOf(t.occurredAt)
    val meta = listOf(acct, time, if (t.note != null) "note" else "").filter { it.isNotEmpty() }.joinToString(" · ")
    val accent = MaterialTheme.colorScheme.primary
    val canvas = MaterialTheme.colorScheme.surfaceContainerLow
    Column(
        Modifier
            .fillMaxWidth()
            .pickedRow(selected, accent, canvas)
            .clickable(role = Role.Button, onClick = onClick)
            .alpha(if (t.status == "unmatched") 0.55f else 1f)
    ) {
        if (columns) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 11.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1.7f)) {
                    Merchant(t)
                    if (badges.isNotEmpty()) BadgeRow(badges, Modifier.padding(top = 4.dp))
                }
                Text(
                    category, style = MaterialTheme.typography.bodyMedium,
                    color = if (uncategorized) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f).padding(start = 12.dp)
                )
                Text(
                    meta.ifEmpty { "—" }, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f).padding(start = 12.dp)
                )
                Amount(t, tone, Modifier.widthIn(min = 96.dp).padding(start = 12.dp))
            }
        } else {
            Row(Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 10.dp), verticalAlignment = Alignment.Top) {
                Column(Modifier.weight(1f)) {
                    Merchant(t)
                    Row(Modifier.padding(top = 2.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            category, style = MaterialTheme.typography.bodySmall.copy(fontSize = 13.sp),
                            color = if (uncategorized) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1
                        )
                        if (meta.isNotEmpty()) Muted(" · $meta", maxLines = 1)
                    }
                    if (badges.isNotEmpty()) BadgeRow(badges, Modifier.padding(top = 5.dp))
                }
                Amount(t, tone, Modifier.padding(start = 12.dp))
            }
        }
        Rule(Modifier.padding(start = 18.dp))
    }
}

@Composable
private fun Merchant(t: Txn) {
    Text(
        t.title,
        style = MaterialTheme.typography.bodyLarge.copy(fontWeight = FontWeight.Medium, textDirection = TextDirection.Content),
        maxLines = 1,
        overflow = TextOverflow.Ellipsis
    )
}

@Composable
private fun Amount(t: Txn, tone: String, modifier: Modifier = Modifier) {
    Text(
        MoneyLedger.amountText(t),
        style = MaterialTheme.typography.bodyLarge.merge(MoneyNumbers).copy(fontWeight = FontWeight.Medium, textAlign = TextAlign.End),
        color = toneColor(tone),
        maxLines = 1,
        modifier = modifier
    )
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun BadgeRow(badges: List<app.ganam.assistant.data.money.BadgeText>, modifier: Modifier = Modifier) {
    FlowRow(modifier, horizontalArrangement = Arrangement.spacedBy(5.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        badges.forEach { Badge(it.text, it.type) }
    }
}

@Composable
private fun Skeleton() {
    val tint = MaterialTheme.colorScheme.surfaceContainerHigh
    Column(Modifier.padding(horizontal = 18.dp, vertical = 14.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        repeat(6) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Box(Modifier.fillMaxWidth(0.6f).height(14.dp).clip(RoundedCornerShape(4.dp)).background(tint))
                    Box(Modifier.fillMaxWidth(0.35f).height(10.dp).clip(RoundedCornerShape(4.dp)).background(tint))
                }
                Box(Modifier.width(64.dp).height(14.dp).clip(RoundedCornerShape(4.dp)).background(tint))
            }
        }
    }
}

// ---------- search and filters ----------

@Composable
private fun TxnTools(
    money: MoneyController,
    filter: TxnFilter,
    month: String,
    taxonomy: List<TaxGroup>,
    accounts: Map<String, AccountRef>,
    panel: FilterPanel?
) {
    val ledger = money.ledger
    val draft by ledger.searchDraft.collectAsState()
    val text = draft ?: filter.q
    var lastSent by remember { mutableStateOf<String?>(null) }
    // Debounced search: the filter follows what is typed after a short pause.
    LaunchedEffect(draft) {
        val d = draft ?: return@LaunchedEffect
        delay(300)
        lastSent = d
        if (d != money.filter.value.q) money.setFilter(money.filter.value.copy(q = d))
    }
    // A search set elsewhere (a saving's "See these charges") replaces what was typed.
    LaunchedEffect(filter.q) { if (draft != null && filter.q != lastSent && filter.q != draft) ledger.setSearchDraft(null) }

    Column(Modifier.fillMaxWidth().padding(top = 10.dp)) {
        InkField(
            value = text,
            onValue = { ledger.setSearchDraft(it) },
            placeholder = if (filter.allTime) "Search every transaction" else "Search merchant, memo, note or amount",
            imeAction = ImeAction.Search,
            modifier = Modifier.padding(horizontal = 18.dp)
        )
        Row(
            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 18.dp, vertical = 10.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Segments(
                listOf("month" to MoneyText.monthLabel(month, short = true), "all" to "All time"),
                if (filter.allTime) "all" else "month"
            ) { money.setFilter(filter.copy(allTime = it == "all")) }
            if (filter.category != "uncategorized") {
                FilterChip("Category", filter.category?.let { MoneyLedger.categoryLabel(taxonomy, it) }, panel == FilterPanel.CATEGORY,
                    onOpen = { ledger.setPanelQuery(""); ledger.openPanel(FilterPanel.CATEGORY) }, onClear = { money.setFilter(filter.copy(category = null)) })
            }
            FilterChip("Account", filter.account?.let { accounts[it]?.name ?: "One account" }, panel == FilterPanel.ACCOUNT,
                onOpen = { ledger.openPanel(FilterPanel.ACCOUNT) }, onClear = { money.setFilter(filter.copy(account = null)) })
            FilterChip("Amount", MoneyLedger.amountChip(filter.min, filter.max), panel == FilterPanel.AMOUNT,
                onOpen = { ledger.openPanel(FilterPanel.AMOUNT) }, onClear = { money.setFilter(filter.copy(min = "", max = "")) })
            val uncat = filter.category == "uncategorized"
            app.ganam.assistant.ui.components.InkChoice("Only uncategorized", selected = uncat) {
                money.setFilter(filter.copy(category = if (uncat) null else "uncategorized"))
            }
            if (!filter.copy(allTime = false).isEmpty) TextLink("Clear filters", Modifier.padding(start = 4.dp)) {
                ledger.setSearchDraft(null)
                money.setFilter(TxnFilter(allTime = filter.allTime))
            }
        }
        when (panel) {
            FilterPanel.CATEGORY -> CategoryPanel(money, taxonomy, filter.category) { key ->
                money.setFilter(filter.copy(category = key)); ledger.openPanel(null)
            }
            FilterPanel.ACCOUNT -> AccountPanel(accounts.values.filter { it.kind != "investment" }.sortedBy { if (it.kind == "bank") 0 else 1 }, filter.account) { id ->
                money.setFilter(filter.copy(account = id)); ledger.openPanel(null)
            }
            FilterPanel.AMOUNT -> AmountPanel(filter.min, filter.max) { min, max ->
                money.setFilter(filter.copy(min = min, max = max)); ledger.openPanel(null)
            }
            null -> {}
        }
        Rule()
    }
}

/** A filter chip: its name with ▾ when off; the chosen value and × when set. */
@Composable
private fun FilterChip(name: String, value: String?, open: Boolean, onOpen: () -> Unit, onClear: () -> Unit) {
    val accent = MaterialTheme.colorScheme.primary
    val on = value != null
    val shape = RoundedCornerShape(10.dp)
    Row(
        Modifier.heightIn(min = 36.dp).clip(shape)
            .border(1.dp, if (on || open) accent else MaterialTheme.colorScheme.outlineVariant, shape),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(
            (value ?: name) + if (on) "" else " ▾",
            style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium, textDirection = TextDirection.Content),
            color = if (on) accent else MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1,
            modifier = Modifier.clickable(role = Role.Button, onClick = onOpen).padding(start = 12.dp, end = if (on) 4.dp else 12.dp, top = 7.dp, bottom = 7.dp)
        )
        if (on) Text(
            "×", style = MaterialTheme.typography.titleMedium, color = accent,
            modifier = Modifier.clickable(role = Role.Button, onClick = onClear).semantics { contentDescription = "Clear $name" }.padding(start = 4.dp, end = 10.dp, top = 4.dp, bottom = 4.dp)
        )
    }
}

/** The category filter: search, groups (a group head picks all of it), categories, "Uncategorized". */
@Composable
private fun CategoryPanel(money: MoneyController, taxonomy: List<TaxGroup>, current: String?, onPick: (String) -> Unit) {
    val q by money.ledger.panelQuery.collectAsState()
    Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainerLow).padding(vertical = 8.dp)) {
        InkField(q, { money.ledger.setPanelQuery(it) }, "Search — English or עברית", Modifier.padding(horizontal = 18.dp))
        Column(Modifier.heightIn(max = 340.dp).verticalScrollable()) {
            if (q.isBlank() || "uncategorized".contains(q.trim().lowercase())) PickRow("Uncategorized", null, current == "uncategorized", head = true) { onPick("uncategorized") }
            CategoryList(taxonomy, q, income = false, current = current, groupsPickable = true, onPick = onPick)
        }
    }
}

/** Groups and their categories (English and Hebrew), the current one marked. */
@Composable
internal fun CategoryList(taxonomy: List<TaxGroup>, q: String, income: Boolean, current: String?, groupsPickable: Boolean, onPick: (String) -> Unit) {
    val groups = MoneyLedger.pickerGroups(taxonomy, q, income)
    if (taxonomy.isEmpty()) Muted("Loading…", Modifier.padding(18.dp))
    else if (groups.isEmpty()) Muted("No category matches “$q”.", Modifier.padding(18.dp))
    groups.forEach { g ->
        if (groupsPickable) PickRow(g.label, g.labelHe, current == g.key, head = true, trailing = "all") { onPick(g.key) }
        else MonoLabel(g.label, Modifier.padding(start = 18.dp, top = 12.dp, bottom = 2.dp))
        g.categories.forEach { c -> PickRow(c.label, c.labelHe, current == c.key, head = false) { onPick(c.key) } }
    }
}

@Composable
internal fun PickRow(label: String, he: String?, on: Boolean, head: Boolean, trailing: String? = null, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(role = Role.Button, onClick = onClick).heightIn(min = 40.dp)
            .padding(start = if (head) 18.dp else 30.dp, end = 18.dp, top = 6.dp, bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(
            label,
            style = MaterialTheme.typography.bodyMedium.copy(fontWeight = if (head) FontWeight.SemiBold else FontWeight.Normal),
            color = if (on) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.weight(1f)
        )
        he?.let { Text(it, style = MaterialTheme.typography.bodySmall.copy(textDirection = TextDirection.Rtl), color = MaterialTheme.colorScheme.onSurfaceVariant) }
        trailing?.let { MonoLabel(it, Modifier.padding(start = 10.dp)) }
        if (on) Text("✓", color = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(start = 10.dp))
    }
}

@Composable
private fun AccountPanel(accounts: List<AccountRef>, current: String?, onPick: (String?) -> Unit) {
    Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainerLow).padding(vertical = 6.dp)) {
        PickRow("Every account", null, current == null, head = true) { onPick(null) }
        accounts.forEach { a -> PickRow(a.name, null, current == a.id, head = true, trailing = MoneyLedger.kindWord(a.kind)) { onPick(a.id) } }
    }
}

@Composable
private fun AmountPanel(min: String, max: String, onShow: (String, String) -> Unit) {
    var lo by remember { mutableStateOf(min) }
    var hi by remember { mutableStateOf(max) }
    Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainerLow).padding(horizontal = 18.dp, vertical = 10.dp)) {
        Muted("Absolute shekels: money out and in alike")
        Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            InkField(lo, { lo = it }, "0", Modifier.weight(1f), label = "At least (₪)", keyboard = KeyboardType.Decimal, imeAction = ImeAction.Next, ltr = true)
            InkField(hi, { hi = it }, "Any", Modifier.weight(1f), label = "At most (₪)", keyboard = KeyboardType.Decimal, onDone = { onShow(lo, hi) }, ltr = true)
        }
        Row(Modifier.padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            InkButton("Clear") { onShow("", "") }
            InkButton("Show", emphasis = InkEmphasis.SOLID) { onShow(lo, hi) }
        }
    }
}

@Composable
private fun Modifier.verticalScrollable(): Modifier = this.then(Modifier.verticalScroll(rememberScrollState()))
