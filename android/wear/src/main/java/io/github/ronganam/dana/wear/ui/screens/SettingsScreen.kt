package io.github.ronganam.dana.wear.ui.screens

import io.github.ronganam.dana.wear.ui.components.rotaryScroll

import android.content.Context
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Sync
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.focusable
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.input.rotary.onRotaryScrollEvent
import androidx.wear.compose.foundation.ExperimentalWearFoundationApi
import androidx.wear.compose.foundation.lazy.ScalingLazyColumn
import androidx.wear.compose.foundation.lazy.rememberScalingLazyListState
import androidx.wear.compose.material.*
import io.github.ronganam.dana.wear.ui.WearViewModel
import io.github.ronganam.dana.wear.data.WatchPairState
import io.github.ronganam.dana.wear.data.WatchPairing
import io.github.ronganam.dana.wear.R
import androidx.compose.ui.res.stringResource

@OptIn(ExperimentalWearFoundationApi::class)
@Composable
fun SettingsScreen(
    viewModel: WearViewModel,
    onOpenPairing: () -> Unit,
) {
    val listState = rememberScalingLazyListState()
    val focusRequester = remember { FocusRequester() }

    val context = LocalContext.current
    val ownVersion = remember {
        try {
            context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: "?"
        } catch (e: Exception) { "?" }
    }
    val phoneVersionCode = remember {
        io.github.ronganam.dana.wear.data.WearPrefs(context).phoneVersionCode
    }

    Scaffold(
        timeText = { TimeText() },
        positionIndicator = {
            PositionIndicator(scalingLazyListState = listState)
        }
    ) {
        ScalingLazyColumn(
            state = listState,
            modifier = Modifier
                .fillMaxSize()
                .rotaryScroll(listState, focusRequester),
            horizontalAlignment = Alignment.CenterHorizontally,
            contentPadding = PaddingValues(top = 28.dp, bottom = 36.dp, start = 12.dp, end = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            item {
                Text(
                    text = stringResource(R.string.watch_settings),
                    style = MaterialTheme.typography.caption1,
                    color = MaterialTheme.colors.secondary
                )
            }

            item {
                Card(
                    onClick = {},
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column {
                        Text(
                            text = stringResource(R.string.auto_synced),
                            style = MaterialTheme.typography.caption2,
                            color = MaterialTheme.colors.primary
                        )
                        Spacer(modifier = Modifier.height(2.dp))
                        Text(
                            text = stringResource(R.string.conversation_shared_thread),
                            style = MaterialTheme.typography.body2,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                    }
                }
            }

            item {
                val pairState by WatchPairing.state.collectAsState()
                LaunchedEffect(Unit) { WatchPairing.load(context) }
                Chip(
                    onClick = onOpenPairing,
                    label = { Text(stringResource(R.string.pairing_title), maxLines = 1) },
                    secondaryLabel = {
                        Text(
                            when (val s = pairState) {
                                is WatchPairState.Paired -> stringResource(R.string.pairing_paired_as, s.deviceName.ifBlank { "Watch" })
                                WatchPairState.Working -> stringResource(R.string.pairing_working)
                                is WatchPairState.NeedsCode -> stringResource(R.string.pairing_not_paired)
                            },
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                    },
                    colors = ChipDefaults.secondaryChipColors(),
                    modifier = Modifier.fillMaxWidth()
                )
            }

            item {
                Chip(
                    onClick = {
                        viewModel.reloadConfiguration()
                    },
                    label = {
                        Text(stringResource(R.string.reload_config), maxLines = 1, overflow = TextOverflow.Ellipsis)
                    },
                    icon = {
                        Icon(Icons.Default.Sync, contentDescription = stringResource(R.string.reload))
                    },
                    colors = ChipDefaults.secondaryChipColors(),
                    modifier = Modifier.fillMaxWidth()
                )
            }

            item {
                Text(
                    text = stringResource(R.string.watch_app_version, ownVersion),
                    style = MaterialTheme.typography.caption2,
                    color = MaterialTheme.colors.secondary
                )
            }

            if (phoneVersionCode > 0) {
                item {
                    Text(
                        text = stringResource(R.string.phone_app_version, phoneVersionCode.toString()),
                        style = MaterialTheme.typography.caption2,
                        color = MaterialTheme.colors.secondary
                    )
                }
            }
        }
    }
}
