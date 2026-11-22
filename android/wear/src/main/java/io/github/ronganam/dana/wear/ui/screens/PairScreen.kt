package io.github.ronganam.dana.wear.ui.screens

import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.wear.compose.foundation.lazy.ScalingLazyColumn
import androidx.wear.compose.foundation.lazy.rememberScalingLazyListState
import androidx.wear.compose.material.Chip
import androidx.wear.compose.material.ChipDefaults
import androidx.wear.compose.material.CircularProgressIndicator
import androidx.wear.compose.material.MaterialTheme
import androidx.wear.compose.material.PositionIndicator
import androidx.wear.compose.material.Scaffold
import androidx.wear.compose.material.Text
import androidx.wear.compose.material.TimeText
import io.github.ronganam.dana.wear.R
import io.github.ronganam.dana.wear.data.NeedsCodeReason
import io.github.ronganam.dana.wear.data.WatchPairState
import io.github.ronganam.dana.wear.data.WatchPairing
import io.github.ronganam.dana.wear.data.WearPrefs
import io.github.ronganam.dana.wear.ui.components.rotaryScroll

/**
 * The watch's own pairing: where it stands, "Ask the phone", and the code
 * entry (server address + the code from the web Devices page) for a watch
 * without its phone. docs/wear.md → Pairing.
 */
@Composable
fun PairScreen() {
    val context = LocalContext.current
    val state by WatchPairing.state.collectAsState()
    LaunchedEffect(Unit) { WatchPairing.load(context) }
    val listState = rememberScalingLazyListState()
    val focusRequester = remember { FocusRequester() }
    var server by rememberSaveable {
        mutableStateOf(WearPrefs(context).let { it.serverUrl.ifEmpty { it.phoneServerUrl } })
    }
    var code by rememberSaveable { mutableStateOf("") }

    Scaffold(timeText = { TimeText() }, positionIndicator = { PositionIndicator(scalingLazyListState = listState) }) {
        ScalingLazyColumn(
            state = listState,
            modifier = Modifier.fillMaxSize().rotaryScroll(listState, focusRequester),
            horizontalAlignment = Alignment.CenterHorizontally,
            contentPadding = PaddingValues(top = 28.dp, bottom = 36.dp, start = 12.dp, end = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            item {
                Text(
                    stringResource(R.string.pairing_title),
                    style = MaterialTheme.typography.caption1,
                    color = MaterialTheme.colors.secondary
                )
            }
            item { StatusText(state) }
            if (state == WatchPairState.Working) {
                item { CircularProgressIndicator(modifier = Modifier.padding(8.dp)) }
            } else {
                item {
                    Chip(
                        onClick = { WatchPairing.pairThroughPhone(context) },
                        label = { Text(stringResource(R.string.pairing_ask_phone)) },
                        colors = ChipDefaults.secondaryChipColors(),
                        modifier = Modifier.fillMaxWidth()
                    )
                }
                item { Field(stringResource(R.string.pairing_server), server, KeyboardType.Uri) { server = it } }
                item {
                    Field(stringResource(R.string.pairing_code), code, KeyboardType.Ascii, characters = true) {
                        code = it.uppercase().take(12)
                    }
                }
                item {
                    Chip(
                        onClick = { WatchPairing.pairWithCode(context, server, code) },
                        label = { Text(stringResource(R.string.pairing_pair)) },
                        modifier = Modifier.fillMaxWidth()
                    )
                }
            }
        }
    }
}

@Composable
private fun StatusText(state: WatchPairState) {
    val text = when (state) {
        is WatchPairState.Paired -> stringResource(R.string.pairing_paired_as, state.deviceName.ifBlank { "Watch" })
        WatchPairState.Working -> stringResource(R.string.pairing_working)
        is WatchPairState.NeedsCode -> state.message ?: when (state.reason) {
            NeedsCodeReason.NO_PHONE -> stringResource(R.string.pairing_no_phone)
            NeedsCodeReason.PHONE_SILENT -> stringResource(R.string.pairing_phone_silent)
            NeedsCodeReason.UNREACHABLE -> stringResource(R.string.pairing_unreachable)
            NeedsCodeReason.BAD_CODE -> stringResource(R.string.pairing_bad_code)
            NeedsCodeReason.PHONE_REFUSED, NeedsCodeReason.SERVER_REFUSED, null -> stringResource(R.string.pairing_not_paired)
        }
    }
    Text(text, style = MaterialTheme.typography.body2, textAlign = TextAlign.Center)
}

@Composable
private fun Field(label: String, value: String, type: KeyboardType, characters: Boolean = false, onChange: (String) -> Unit) {
    Box(
        Modifier
            .fillMaxWidth()
            .border(1.dp, MaterialTheme.colors.secondary, RoundedCornerShape(12.dp))
            .padding(horizontal = 12.dp, vertical = 8.dp)
    ) {
        if (value.isEmpty()) Text(label, style = MaterialTheme.typography.body2, color = MaterialTheme.colors.secondary)
        BasicTextField(
            value = value,
            onValueChange = onChange,
            singleLine = true,
            textStyle = MaterialTheme.typography.body2.copy(color = MaterialTheme.colors.onBackground),
            cursorBrush = SolidColor(MaterialTheme.colors.primary),
            keyboardOptions = KeyboardOptions(
                keyboardType = type,
                capitalization = if (characters) KeyboardCapitalization.Characters else KeyboardCapitalization.None,
                autoCorrectEnabled = false,
                imeAction = ImeAction.Done
            ),
            modifier = Modifier.fillMaxWidth()
        )
    }
}
