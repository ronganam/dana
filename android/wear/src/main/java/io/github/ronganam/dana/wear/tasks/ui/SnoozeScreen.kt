package io.github.ronganam.dana.wear.tasks.ui

import androidx.compose.ui.focus.FocusRequester
import androidx.compose.runtime.remember
import io.github.ronganam.dana.wear.ui.components.rotaryScroll

import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.DateRange
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.input.rotary.onRotaryScrollEvent
import androidx.wear.compose.foundation.ExperimentalWearFoundationApi
import androidx.wear.compose.foundation.lazy.ScalingLazyColumn
import androidx.wear.compose.foundation.lazy.rememberScalingLazyListState
import androidx.compose.ui.focus.focusRequester
import androidx.wear.compose.material.*

@OptIn(ExperimentalWearFoundationApi::class)
@Composable
fun SnoozeScreen(
    taskTitle: String,
    onSnoozeSelected: (Int) -> Unit,
    onDismiss: () -> Unit
) {
    val listState = rememberScalingLazyListState()
    val focusRequester = remember { FocusRequester() }

    Scaffold(
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
            contentPadding = PaddingValues(top = 28.dp, bottom = 36.dp, start = 12.dp, end = 12.dp)
        ) {
            item {
                Text(
                    text = "Snooze",
                    style = MaterialTheme.typography.caption1,
                    color = MaterialTheme.colors.secondary
                )
            }

            item {
                Text(
                    text = taskTitle,
                    style = MaterialTheme.typography.body2,
                    color = MaterialTheme.colors.onBackground,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp)
                )
            }

            val options = listOf(
                Pair("15 minutes", 15),
                Pair("1 hour", 60),
                Pair("3 hours", 180),
                Pair("1 day", 1440)
            )

            items(options.size) { index ->
                val (label, minutes) = options[index]
                CompactChip(
                    onClick = { onSnoozeSelected(minutes) },
                    label = { Text(label) },
                    icon = { Icon(Icons.Default.DateRange, contentDescription = null, modifier = Modifier.size(18.dp)) },
                    colors = ChipDefaults.secondaryChipColors(),
                    modifier = Modifier.fillMaxWidth()
                )
            }

            item {
                Spacer(modifier = Modifier.height(4.dp))
            }

            item {
                CompactChip(
                    onClick = onDismiss,
                    label = { Text("Cancel") },
                    colors = ChipDefaults.secondaryChipColors(),
                    modifier = Modifier.fillMaxWidth()
                )
            }
        }
    }
}
