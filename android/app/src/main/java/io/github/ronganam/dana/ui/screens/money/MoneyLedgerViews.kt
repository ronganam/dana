package io.github.ronganam.dana.ui.screens.money

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import io.github.ronganam.dana.ui.viewmodels.MoneyController
import io.github.ronganam.dana.ui.viewmodels.MoneyDetail

// Transactions, Accounts, Save, the banners and the detail: the entry points
// MoneyDestination calls. Each view fills the space it gets, scrolls itself
// and lays out by the width it measures (BoxWithConstraints), so [wide] is
// only a hint. The views live in MoneyTransactionsView, MoneyTxnDetail,
// MoneyAccountsView and MoneySaveView; their state in MoneyLedgerState.

@Composable
fun MoneyTransactions(money: MoneyController, wide: Boolean, modifier: Modifier = Modifier) {
    TransactionsView(money, modifier)
}

@Composable
fun MoneyAccounts(money: MoneyController, wide: Boolean, modifier: Modifier = Modifier) {
    AccountsView(money, modifier)
}

@Composable
fun MoneySave(money: MoneyController, wide: Boolean, modifier: Modifier = Modifier) {
    SaveView(money, modifier)
}

/** The SMS code and needs-attention banners (above the views; nothing when all is well). */
@Composable
fun MoneyBanners(money: MoneyController, modifier: Modifier = Modifier) {
    Banners(money, modifier)
}

/** A transaction or a saving item: a pane beside the view unfolded, full screen on the cover ([onClose] = back). */
@Composable
fun MoneyDetailPane(money: MoneyController, detail: MoneyDetail, onClose: () -> Unit, modifier: Modifier = Modifier) {
    when (detail) {
        is MoneyDetail.Transaction -> TxnDetailPane(money, detail.id, onClose, modifier)
        is MoneyDetail.Saving -> SavingDetailPane(money, detail.key, onClose, modifier)
    }
}
