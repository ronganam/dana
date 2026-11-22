package io.github.ronganam.dana.tv

import io.github.ronganam.dana.protocol.PairingCode
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.input.key.utf16CodePoint
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.discovery.DiscoveredServer
import io.github.ronganam.dana.data.pairing.PairingFlow
import io.github.ronganam.dana.ui.components.foundServerLabel
import io.github.ronganam.dana.ui.components.rememberFoundServers

/**
 * Dana TV's first screen until it is paired. The code from Dana's Devices page
 * ("Pair a device" → TV, with its room and role) is entered on an on-screen
 * keypad of the code's 32 characters, so the remote's D-pad is enough (a
 * keyboard's keys work too); the server is picked from the ones found on the
 * home network, or typed.
 */
@Composable
fun TvPairingScreen(flow: PairingFlow) {
    val state by flow.state.collectAsState()
    val found by rememberFoundServers()
    LaunchedEffect(found) { flow.discovered(found) }
    val firstKey = remember { FocusRequester() }
    LaunchedEffect(Unit) { runCatching { firstKey.requestFocus() } }
    var typing by remember { mutableStateOf(false) }
    val focusManager = LocalFocusManager.current

    Row(
        Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)
            .padding(horizontal = 48.dp, vertical = 27.dp)
            .onPreviewKeyEvent { e ->
                if (typing || e.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                val c = e.utf16CodePoint.toChar().uppercaseChar()
                when {
                    c in PairingCode.ALPHABET -> { flow.type(c); true }
                    e.key == Key.Backspace || e.key == Key.Delete -> { flow.backspace(); true }
                    else -> false
                }
            },
        horizontalArrangement = Arrangement.spacedBy(32.dp),
    ) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            TvMono("Dana TV", color = MaterialTheme.colorScheme.primary)
            Text("Pair this TV", style = TvType.title, color = MaterialTheme.colorScheme.onSurface)
            Text("On Dana's web app open Devices → Pair a device → TV, choose its room and role, and enter the code.",
                style = TvType.body, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(6.dp))
            CodeSlots(state.code)
            Spacer(Modifier.height(6.dp))
            Keypad(firstKey, onKey = flow::type, onBackspace = flow::backspace)
        }
        Column(Modifier.width(330.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Spacer(Modifier.height(70.dp))
            TvSection("Server")
            if (state.found.isEmpty()) {
                Text("No Dana server found on this network yet. Type its address.", style = TvType.small,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            state.found.forEach { server -> FoundServerChip(server, selected = server.url == state.server) { flow.pick(server) } }
            OutlinedTextField(
                value = state.server,
                onValueChange = flow::setServer,
                // A single-line field keeps Up / Down to itself; on a remote they must move on.
                modifier = Modifier.fillMaxWidth().onFocusChanged { typing = it.isFocused }.onPreviewKeyEvent { e ->
                    if (e.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                    when (e.key) {
                        Key.DirectionDown -> focusManager.moveFocus(FocusDirection.Down)
                        Key.DirectionUp -> focusManager.moveFocus(FocusDirection.Up)
                        else -> false
                    }
                },
                label = { Text("Server address") },
                singleLine = true,
                textStyle = TvType.body,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, autoCorrectEnabled = false, imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = { flow.submit() }),
            )
            state.error?.let { Text(it, style = TvType.body, color = MaterialTheme.colorScheme.error) }
            ChipRow {
                TvChip(if (state.busy) "Pairing…" else "Pair", selected = state.canSubmit, onClick = flow::submit)
            }
        }
    }
}

/** The 8 code characters as boxes; the next one to fill is outlined. */
@Composable
private fun CodeSlots(code: String) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
        for (i in 0 until PairingCode.LENGTH) {
            if (i == 4) Spacer(Modifier.width(10.dp))
            val next = i == code.length
            Box(
                Modifier.size(width = 42.dp, height = 54.dp)
                    .background(MaterialTheme.colorScheme.surfaceContainer, RoundedCornerShape(8.dp))
                    .border(if (next) 2.dp else 1.dp,
                        if (next) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(8.dp)),
                contentAlignment = Alignment.Center,
            ) {
                Text(code.getOrNull(i)?.toString().orEmpty(), style = TvType.value.copy(fontSize = 28.sp),
                    color = MaterialTheme.colorScheme.onSurface)
            }
        }
    }
}

/** The code's alphabet in rows of 8 plus Delete: every key is a D-pad focus stop. */
@Composable
private fun Keypad(firstKey: FocusRequester, onKey: (Char) -> Unit, onBackspace: () -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        PairingCode.ALPHABET.chunked(8).forEachIndexed { row, letters ->
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                letters.forEachIndexed { i, c ->
                    FocusTile(
                        Modifier.size(width = 40.dp, height = 34.dp),
                        shape = RoundedCornerShape(8.dp),
                        focusRequester = if (row == 0 && i == 0) firstKey else null,
                        onClick = { onKey(c) },
                    ) { _ ->
                        Text(c.toString(), Modifier.align(Alignment.Center), style = TvType.name, color = MaterialTheme.colorScheme.onSurface)
                    }
                }
                if (row == 0) {
                    FocusTile(Modifier.size(width = 40.dp, height = 34.dp), shape = RoundedCornerShape(8.dp), onClick = onBackspace) { _ ->
                        Text("⌫", Modifier.align(Alignment.Center).semantics { contentDescription = "Delete" }, style = TvType.name, color = MaterialTheme.colorScheme.onSurface)
                    }
                }
            }
        }
    }
}

@Composable
private fun FoundServerChip(server: DiscoveredServer, selected: Boolean, onClick: () -> Unit) {
    TvChip(foundServerLabel(server), Modifier.fillMaxWidth(), selected = selected, onClick = onClick)
}
