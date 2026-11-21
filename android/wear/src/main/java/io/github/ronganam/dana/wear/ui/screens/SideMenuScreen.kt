package io.github.ronganam.dana.wear.ui.screens

import io.github.ronganam.dana.wear.ui.components.rotaryScroll
import android.content.Intent
import androidx.compose.ui.platform.LocalContext
import io.github.ronganam.dana.wear.shopping.ShoppingActivity

import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.ShoppingCart
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withStyle
import io.github.ronganam.dana.wear.ui.theme.DanaWear
import androidx.compose.foundation.focusable
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.input.rotary.onRotaryScrollEvent
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.wear.compose.foundation.ExperimentalWearFoundationApi
import androidx.wear.compose.foundation.rememberActiveFocusRequester
import androidx.wear.compose.foundation.lazy.ScalingLazyColumn
import androidx.wear.compose.foundation.lazy.rememberScalingLazyListState
import androidx.wear.compose.material.*
import io.github.ronganam.dana.wear.ui.WearViewModel
import io.github.ronganam.dana.wear.R
import androidx.compose.ui.res.stringResource

@OptIn(ExperimentalWearFoundationApi::class)
@Composable
fun SideMenuScreen(
    viewModel: WearViewModel,
    onSessionSelected: () -> Unit,
    onNavigateToSettings: () -> Unit,
    active: Boolean = true
) {
    val listState = rememberScalingLazyListState()
    val focusRequester = remember { androidx.compose.ui.focus.FocusRequester() }

    Scaffold(
        positionIndicator = {
            PositionIndicator(scalingLazyListState = listState)
        }
    ) {
        ScalingLazyColumn(
            state = listState,
            modifier = Modifier
                .fillMaxSize()
                .rotaryScroll(listState, focusRequester, active = active),
            horizontalAlignment = Alignment.CenterHorizontally,
            contentPadding = PaddingValues(top = 28.dp, bottom = 36.dp, start = 12.dp, end = 12.dp)
        ) {
        item {
            // The name as on the icon: serif, with the ember full stop.
            Text(
                text = buildAnnotatedString {
                    append(stringResource(R.string.app_name))
                    withStyle(SpanStyle(color = DanaWear.Ember)) { append(".") }
                },
                fontFamily = DanaWear.Serif,
                fontSize = 24.sp,
                color = DanaWear.Ink,
                modifier = Modifier.padding(bottom = 4.dp)
            )
        }

        // One conversation, shared with the phone and the Mac: show its latest turns, or start clean.
        item {
            Chip(
                onClick = {
                    viewModel.showThread()
                    onSessionSelected()
                },
                label = { Text(stringResource(R.string.latest_in_thread), maxLines = 1, overflow = TextOverflow.Ellipsis) },
                icon = { Icon(Icons.AutoMirrored.Filled.Chat, contentDescription = null) },
                colors = ChipDefaults.primaryChipColors(),
                modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)
            )
        }

        item {
            Chip(
                onClick = {
                    viewModel.startNewChat()
                    onSessionSelected()
                },
                label = { Text(stringResource(R.string.clear_the_screen), maxLines = 1, overflow = TextOverflow.Ellipsis) },
                icon = { Icon(Icons.Default.Add, contentDescription = null) },
                colors = ChipDefaults.secondaryChipColors(iconColor = DanaWear.Mute),
                modifier = Modifier.fillMaxWidth()
            )
        }

        item {
            val context = LocalContext.current
            Chip(
                onClick = {
                    context.startActivity(Intent(context, ShoppingActivity::class.java))
                },
                label = { Text(stringResource(R.string.label_shopping), maxLines = 1) },
                icon = { Icon(Icons.Default.ShoppingCart, contentDescription = null) },
                colors = ChipDefaults.chipColors(
                    backgroundColor = DanaWear.tint(DanaWear.Sage),
                    contentColor = DanaWear.Ink,
                    iconColor = DanaWear.Sage
                ),
                modifier = Modifier.fillMaxWidth().padding(top = 4.dp)
            )
        }

        item {
            Spacer(modifier = Modifier.height(6.dp))
            Chip(
                onClick = onNavigateToSettings,
                label = { Text(stringResource(R.string.settings_and_sync), maxLines = 1) },
                icon = { Icon(Icons.Default.Settings, contentDescription = stringResource(R.string.settings)) },
                colors = ChipDefaults.secondaryChipColors(iconColor = DanaWear.Mute),
                modifier = Modifier.fillMaxWidth().padding(top = 4.dp)
            )
        }
    }
    }
}
