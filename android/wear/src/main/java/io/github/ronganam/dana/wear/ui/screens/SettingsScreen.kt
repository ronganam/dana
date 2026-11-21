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

@OptIn(ExperimentalWearFoundationApi::class)
@Composable
fun SettingsScreen(
    viewModel: WearViewModel
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
                    text = "Watch Settings",
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
                            text = "Auto-Synced via Phone",
                            style = MaterialTheme.typography.caption2,
                            color = MaterialTheme.colors.primary
                        )
                        Spacer(modifier = Modifier.height(2.dp))
                        Text(
                            text = "Conversation: the shared thread",
                            style = MaterialTheme.typography.body2,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                    }
                }
            }

            item {
                Chip(
                    onClick = {
                        viewModel.reloadConfiguration()
                    },
                    label = {
                        Text("Reload Config", maxLines = 1, overflow = TextOverflow.Ellipsis)
                    },
                    icon = {
                        Icon(Icons.Default.Sync, contentDescription = "Reload")
                    },
                    colors = ChipDefaults.secondaryChipColors(),
                    modifier = Modifier.fillMaxWidth()
                )
            }

            item {
                Text(
                    text = "Watch app v$ownVersion",
                    style = MaterialTheme.typography.caption2,
                    color = MaterialTheme.colors.secondary
                )
            }

            if (phoneVersionCode > 0) {
                item {
                    Text(
                        text = "Phone app v$phoneVersionCode",
                        style = MaterialTheme.typography.caption2,
                        color = MaterialTheme.colors.secondary
                    )
                }
            }
        }
    }
}
