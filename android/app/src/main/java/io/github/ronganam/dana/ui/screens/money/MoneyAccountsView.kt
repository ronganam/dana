package io.github.ronganam.dana.ui.screens.money

import io.github.ronganam.dana.data.money.MoneyFormat
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.relocation.BringIntoViewRequester
import androidx.compose.foundation.relocation.bringIntoViewRequester
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.money.Attention
import io.github.ronganam.dana.data.money.MoneyCompany
import io.github.ronganam.dana.data.money.MoneyLedger
import io.github.ronganam.dana.ui.components.InkButton
import io.github.ronganam.dana.ui.components.InkChoice
import io.github.ronganam.dana.ui.components.InkEmphasis
import io.github.ronganam.dana.ui.components.MetaLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.theme.Danger
import io.github.ronganam.dana.ui.theme.Info
import io.github.ronganam.dana.ui.theme.Warning
import io.github.ronganam.dana.ui.viewmodels.LoginForm
import io.github.ronganam.dana.ui.viewmodels.MoneyAccount
import io.github.ronganam.dana.ui.viewmodels.MoneyController
import io.github.ronganam.dana.ui.viewmodels.MoneySource
import kotlinx.coroutines.delay
import java.time.Instant
import java.time.LocalDate

/**
 * Accounts (web: the Accounts view): each login's state and reason, its
 * accounts (bank balance, card next charge, investment value, meal card
 * left), last / next sync, Sync now, update login, remove (optionally with
 * its transactions), and adding a bank or card. Two columns of logins from
 * 640dp. Login details are typed into [LoginForm] only: secret fields are
 * password fields, never prefilled, gone after a successful save.
 */
@Composable
internal fun AccountsView(money: MoneyController, modifier: Modifier = Modifier) {
    val ledger = money.ledger
    val sources by money.sources.collectAsState()
    val form by ledger.form.collectAsState()
    val busy by ledger.syncBusy.collectAsState()
    val now by nowTicker(30_000)
    BoxWithConstraints(modifier.fillMaxSize()) {
        val cols = if (maxWidth >= 640.dp) 2 else 1
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 18.dp, vertical = 14.dp)) {
            Muted("Dana signs in to each of these twice a day and when your phone sees a payment. Login details are stored encrypted and never shown again.")
            val f = form
            // Adding opens in place of the "+ Add" button, where the finger already is.
            val adding = f != null && (f.mode == LoginForm.Mode.COMPANIES || (f.mode == LoginForm.Mode.LOGIN && f.sourceId == null))
            val list = sources
            when {
                list == null -> Muted("Loading…", Modifier.padding(top = 18.dp))
                list.isEmpty() && f == null -> Column(Modifier.padding(top = 24.dp)) {
                    Text("No bank or card yet.", style = MaterialTheme.typography.headlineMedium)
                    Muted("Add the login you use on your bank's or card's site, and Dana keeps your transactions here.", Modifier.padding(top = 6.dp, bottom = 12.dp))
                    InkButton("Add a bank or card", emphasis = InkEmphasis.SOLID) { ledger.openAdd() }
                }
                else -> {
                    Spacer(Modifier.height(14.dp))
                    list.chunked(cols).forEach { row ->
                        Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min), horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                            row.forEach { s ->
                                Box(Modifier.weight(1f).fillMaxHeight().loginBox()) {
                                    LoginBlock(money, s, f?.takeIf { it.sourceId == s.id }, s.id in busy, now)
                                }
                            }
                            repeat(cols - row.size) { Spacer(Modifier.weight(1f)) }
                        }
                        Spacer(Modifier.height(14.dp))
                    }
                    if (adding) {
                        IntoView(f.mode to f.company, Modifier.loginBox()) { AddForm(money, f) }
                    } else if (list.isNotEmpty()) {
                        InkButton("+ Add a bank or card", emphasis = InkEmphasis.PRIMARY) { ledger.openAdd() }
                    }
                }
            }
        }
    }
}

@Composable
private fun Modifier.loginBox(): Modifier =
    this.border(1.dp, MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(14.dp)).padding(16.dp)

/** The clock, ticking every [ms] (relative times, countdowns). */
/** A form that scrolls itself into view when it opens or changes step ([key]). */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun IntoView(key: Any?, modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    val into = remember { BringIntoViewRequester() }
    LaunchedEffect(key) { into.bringIntoView() }
    Column(modifier.bringIntoViewRequester(into), content = content)
}

@Composable
internal fun nowTicker(ms: Long): State<Instant> {
    val s = remember { mutableStateOf(Instant.now()) }
    LaunchedEffect(ms) { while (true) { delay(ms); s.value = Instant.now() } }
    return s
}

@Composable
private fun LoginBlock(money: MoneyController, s: MoneySource, form: LoginForm?, syncing: Boolean, now: Instant) {
    val ledger = money.ledger
    val st = MoneyLedger.sourceStatus(s.state, s.stateReason, s.lastOkAt, s.canSync, now)
    val busy = s.state == "syncing" || s.state == "new" || syncing
    val noSync = busy || s.state == "needs_code" || (s.state == "needs_attention" && !s.canSync)
    Column(Modifier.fillMaxHeight()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(s.companyName, style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.SemiBold))
            s.label?.let { Text(" · $it", style = MaterialTheme.typography.titleMedium.copy(textDirection = TextDirection.Content), color = MaterialTheme.colorScheme.onSurfaceVariant) }
        }
        Row(Modifier.padding(top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            if (busy) {
                CircularProgressIndicator(Modifier.size(12.dp), strokeWidth = 1.5.dp, color = Info)
                Spacer(Modifier.width(7.dp))
                Text(if (st.tone != "busy") "Syncing…" else st.label, style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium), color = Info)
            } else StateLine(st.label, st.tone)
        }
        if (st.detail.isNotEmpty()) Muted(st.detail, Modifier.padding(top = 4.dp))
        val times = listOfNotNull(
            if (s.lastOkAt != null) "Last good sync ${MoneyLedger.whenText(s.lastOkAt, now)}" else "Never synced yet",
            s.nextSyncAt?.takeIf { s.state != "syncing" && s.state != "needs_attention" }?.let { "next ${MoneyLedger.whenText(it, now)} (${MoneyLedger.until(it, now)})" }
        ).joinToString(" · ")
        // Wraps (MetaLabel is one line) so "next … (in 7 h)" is never cut in a narrow column.
        Text(times.uppercase(), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 6.dp))
        Rule(Modifier.padding(vertical = 10.dp))
        if (s.accounts.isEmpty()) Muted("No accounts yet — they appear after the first sync.")
        s.accounts.forEach { a -> AccountLine(s, a, now) }
        when (form?.mode) {
            LoginForm.Mode.LOGIN -> IntoView(form.mode) { Rule(Modifier.padding(vertical = 10.dp)); LoginFields(money, form) }
            LoginForm.Mode.REMOVE -> IntoView(form.mode) { Rule(Modifier.padding(vertical = 10.dp)); RemoveForm(money, s, form) }
            else -> {
                Spacer(Modifier.weight(1f))
                @OptIn(ExperimentalLayoutApi::class)
                FlowRow(Modifier.padding(top = 12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    InkButton(if (syncing) "Starting…" else "Sync now", enabled = !noSync) { ledger.syncNow(s.id) }
                    InkButton("Update login") { ledger.openEdit(s.id) }
                    InkButton("Remove", emphasis = InkEmphasis.BARE) { ledger.openRemove(s.id) }
                }
                if (noSync && !busy) Muted(
                    if (s.state == "needs_code") "Signing in: waiting for the code." else "Update the login first.",
                    Modifier.padding(top = 6.dp)
                )
            }
        }
    }
}

@Composable
private fun AccountLine(s: MoneySource, a: MoneyAccount, now: Instant) {
    val name = MoneyLedger.accountName(a.kind, a.number, a.label, s.companyName)
    val (value, sub) = when (a.kind) {
        "meal" -> (a.balance?.let { MoneyFormat.amount(it, cents = false) } ?: "") to (a.balance?.let { "left, as of ${MoneyLedger.whenText(a.balanceAt, now)}" } ?: "")
        "bank" -> (a.balance?.let { MoneyFormat.amount(it) } ?: "") to (a.balance?.let { "as of ${MoneyLedger.whenText(a.balanceAt, now)}" } ?: "")
        "investment" -> (a.balance?.let { MoneyFormat.amount(it, cents = false) } ?: "") to (a.balance?.let { "worth, as of ${MoneyLedger.whenText(a.balanceAt, now)}" } ?: "")
        else -> if (a.nextCharge != null && a.nextCharge != 0L) MoneyFormat.amount(a.nextCharge, sign = MoneyFormat.Sign.NONE) to "bill on ${MoneyLedger.shortDate(a.nextChargeDate, LocalDate.now())}"
        else "" to "no bill coming"
    }
    Row(Modifier.fillMaxWidth().padding(vertical = 5.dp), verticalAlignment = Alignment.Top) {
        Column(Modifier.weight(1f)) {
            Text(name, style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium, textDirection = TextDirection.Content))
            MetaLabel(MoneyLedger.kindWord(a.kind))
        }
        Column(horizontalAlignment = Alignment.End) {
            if (value.isNotEmpty()) Text(value, style = MaterialTheme.typography.bodyMedium.merge(MoneyNumbers).copy(fontWeight = FontWeight.Medium))
            if (sub.isNotEmpty()) Muted(sub)
        }
    }
}

/** Add a bank or card: the companies, then the chosen one's login. */
@Composable
private fun AddForm(money: MoneyController, f: LoginForm) {
    val ledger = money.ledger
    val companies by ledger.companies.collectAsState()
    Column {
        if (f.mode == LoginForm.Mode.COMPANIES) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Add a bank or card", style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.SemiBold), modifier = Modifier.weight(1f))
                InkButton("Cancel", emphasis = InkEmphasis.BARE) { ledger.closeForm() }
            }
            val list = companies
            if (list == null && f.error == null) Muted("Loading…", Modifier.padding(top = 8.dp))
            list?.forEach { c ->
                Row(
                    Modifier.fillMaxWidth().clickable(role = Role.Button) { ledger.pickCompany(c.id) }.heightIn(min = 46.dp).padding(vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(c.name, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
                    MetaLabel(MoneyLedger.kindWord(c.kind))
                    Text("  ›", color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Rule()
            }
            f.error?.let { Text(it, color = Danger, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 8.dp)) }
        } else LoginFields(money, f)
    }
}

/** The login's fields (secret ones as passwords), the optional name, errors at the field, Save. */
@Composable
private fun LoginFields(money: MoneyController, f: LoginForm) {
    val ledger = money.ledger
    val companies by ledger.companies.collectAsState()
    val company: MoneyCompany? = companies?.firstOrNull { it.id == f.company }
    if (company == null) Muted("Loading…") else Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(
            if (f.sourceId != null) "Update the ${company.name} login" else "Add ${company.name}",
            style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.SemiBold)
        )
        Muted(if (f.sourceId != null) "Type the login again: saved details are never shown. Leave it empty to change only the name." else "The same details you use on ${company.name}'s site.")
        company.fields.forEach { field ->
            InkField(
                value = f.values[field.name] ?: "",
                onValue = { v -> ledger.editForm { it.copy(values = it.values + (field.name to v), fieldError = null, error = null) } },
                placeholder = field.label,
                label = field.label,
                secret = field.secret,
                keyboard = if (field.name == "phone") KeyboardType.Phone else KeyboardType.Text,
                imeAction = ImeAction.Next,
                ltr = true,
                error = f.fieldError?.takeIf { it.first == field.name }?.second
            )
        }
        InkField(
            value = f.label,
            onValue = { v -> ledger.editForm { it.copy(label = v) } },
            placeholder = "e.g. Joint account",
            label = "Name (optional)",
            onDone = { ledger.submitLogin() }
        )
        if (company.signInCode && f.sourceId == null) {
            Muted("${company.name} sends an SMS code the first time Dana signs in. Your phone hands it over by itself; if not, a box for it shows up here.")
        }
        f.error?.let { Text(it, color = Danger, style = MaterialTheme.typography.bodySmall) }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (f.sourceId == null) InkButton("Back") { ledger.openAdd() } else InkButton("Cancel") { ledger.closeForm() }
            InkButton(if (f.busy) "Saving…" else if (f.sourceId != null) "Save and sync" else "Add and sync", emphasis = InkEmphasis.SOLID, enabled = !f.busy) { ledger.submitLogin() }
        }
        Muted("Stored encrypted on your Dana server and used only to sign in for you.")
    }
}

@Composable
private fun RemoveForm(money: MoneyController, s: MoneySource, f: LoginForm) {
    val ledger = money.ledger
    val n = s.accounts.size
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Remove ${s.companyName}?", style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.SemiBold))
        Text("Dana stops signing in to ${s.companyName} and forgets the login.", style = MaterialTheme.typography.bodyMedium)
        InkChoice(
            (if (f.alsoTransactions) "✓ " else "") + "Also delete " + if (n > 0) "its $n account${if (n == 1) "" else "s"} and their transactions" else "its transactions",
            selected = f.alsoTransactions
        ) { ledger.editForm { it.copy(alsoTransactions = !it.alsoTransactions) } }
        Muted(if (f.alsoTransactions) "They are deleted for good." else "Otherwise they stay here, without new ones.")
        f.error?.let { Text(it, color = Danger, style = MaterialTheme.typography.bodySmall) }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            InkButton("Cancel") { ledger.closeForm() }
            InkButton(if (f.busy) "Removing…" else "Remove", emphasis = InkEmphasis.PRIMARY, enabled = !f.busy) { ledger.submitRemove() }
        }
    }
}

// ---------- banners ----------

/**
 * Above the views: a pending SMS code (input + countdown) per login, and the
 * logins that need attention with a way to fix them. Nothing when all is well.
 */
@Composable
internal fun Banners(money: MoneyController, modifier: Modifier = Modifier) {
    val sources by money.sources.collectAsState()
    val overview by money.overview.collectAsState()
    val list = sources.orEmpty()
    val coded = list.filter { it.codeRequest != null }
    val attention = (MoneyLedger.attention(overview) ?: list.filter { it.state in setOf("needs_attention", "failing", "needs_code") }
        .map { Attention(it.id, it.companyName, it.state, it.stateReason) })
        .filter { a -> coded.none { it.id == a.sourceId } }
        .filter { a -> (list.firstOrNull { it.id == a.sourceId }?.state ?: a.state) !in setOf("syncing", "ok", "new") }
    if (coded.isNotEmpty() || attention.isNotEmpty()) Column(modifier.fillMaxWidth().padding(vertical = 6.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        coded.forEach { s -> s.codeRequest?.let { (id, expires) -> CodeBanner(money, s, id, expires) } }
        attention.forEach { a -> AttentionBanner(money, a, list.firstOrNull { it.id == a.sourceId }) }
    }
}

@Composable
private fun BannerBox(color: androidx.compose.ui.graphics.Color, content: @Composable ColumnScope.() -> Unit) {
    Column(
        Modifier.fillMaxWidth()
            .border(1.dp, color.copy(alpha = 0.5f), RoundedCornerShape(10.dp))
            .drawBehind { drawLine(color, Offset(1.5.dp.toPx(), 8.dp.toPx()), Offset(1.5.dp.toPx(), size.height - 8.dp.toPx()), 3.dp.toPx()) }
            .padding(start = 14.dp, end = 12.dp, top = 10.dp, bottom = 10.dp),
        content = content
    )
}

@Composable
private fun CodeBanner(money: MoneyController, s: MoneySource, id: String, expires: String) {
    val ledger = money.ledger
    val drafts by ledger.codeDrafts.collectAsState()
    val busySet by ledger.codeBusy.collectAsState()
    val errors by ledger.codeErrors.collectAsState()
    val sent by ledger.codeSent.collectAsState()
    val now by nowTicker(1_000)
    val done = id in sent
    BannerBox(MaterialTheme.colorScheme.primary) {
        Text("${s.companyName} sent you a sign-in code", style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold))
        if (done) Muted("Thanks — signing in…", Modifier.padding(top = 2.dp))
        else {
            Row(Modifier.padding(top = 2.dp), verticalAlignment = Alignment.CenterVertically) {
                Muted("The phone usually hands it over by itself. If it doesn't, type it here.", Modifier.weight(1f))
                Text(
                    MoneyLedger.countdown(expires, now),
                    style = MaterialTheme.typography.labelMedium.merge(MoneyNumbers),
                    color = if (MoneyLedger.secondsLeft(expires, now) < 60) Warning else MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(start = 8.dp)
                )
            }
            Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                InkField(
                    value = drafts[id] ?: "",
                    onValue = { ledger.setCodeDraft(id, it) },
                    placeholder = "Code",
                    keyboard = KeyboardType.NumberPassword,
                    imeAction = ImeAction.Send,
                    onDone = { ledger.sendCode(id) },
                    ltr = true,
                    modifier = Modifier.weight(1f)
                )
                Spacer(Modifier.width(8.dp))
                InkButton(if (id in busySet) "Sending…" else "Send", emphasis = InkEmphasis.SOLID, enabled = id !in busySet) { ledger.sendCode(id) }
            }
            errors[id]?.let { Text(it, color = Danger, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 4.dp)) }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun AttentionBanner(money: MoneyController, a: Attention, src: MoneySource?) {
    val ledger = money.ledger
    val busy by ledger.syncBusy.collectAsState()
    val st = src?.state ?: a.state
    val bad = a.state == "needs_attention"
    val byHand = bad && src?.canSync == true
    val title = when {
        byHand -> "Dana stopped signing in"
        bad -> "the login needs fixing"
        st == "needs_code" -> "waiting for a sign-in code"
        else -> "the last sync failed"
    }
    BannerBox(if (bad) Danger else Warning) {
        Text("${a.name}: $title", style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold))
        a.reason?.let { Muted(it, Modifier.padding(top = 2.dp)) }
        FlowRow(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            val sync: @Composable () -> Unit = { InkButton("Sync now", enabled = a.sourceId !in busy) { ledger.syncNow(a.sourceId) } }
            val edit: @Composable () -> Unit = { InkButton("Update login") { ledger.openEdit(a.sourceId) } }
            when {
                byHand -> { sync(); edit() }
                bad -> edit()
                else -> sync()
            }
        }
    }
}
