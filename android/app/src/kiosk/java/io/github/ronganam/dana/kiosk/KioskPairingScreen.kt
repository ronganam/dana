package io.github.ronganam.dana.kiosk

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.pairing.PairingFlow
import io.github.ronganam.dana.ui.components.FoundServers
import io.github.ronganam.dana.ui.components.MetaLabel
import io.github.ronganam.dana.ui.components.rememberFoundServers
import io.github.ronganam.dana.ui.theme.DanaSerif
import io.github.ronganam.dana.ui.theme.DmMono

/**
 * The tablet's first screen until it is paired: the code from Dana's Devices
 * page ("Pair a device" → Tablet, with its room and role) in big letters, and
 * the server address, filled from the home network when one Dana server is found.
 */
@Composable
fun KioskPairingScreen(flow: PairingFlow, onSettings: () -> Unit) {
    val state by flow.state.collectAsState()
    val found by rememberFoundServers()
    LaunchedEffect(found) { flow.discovered(found) }

    Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).imePadding(), contentAlignment = Alignment.Center) {
        Column(
            Modifier.widthIn(max = 640.dp).fillMaxWidth().verticalScroll(rememberScrollState()).padding(32.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            MetaLabel("Dana kiosk", color = MaterialTheme.colorScheme.primary)
            Text("Pair this tablet", style = TextStyle(fontFamily = DanaSerif, fontSize = 44.sp, lineHeight = 48.sp),
                color = MaterialTheme.colorScheme.onBackground)
            Text(
                "On Dana's web app open Devices → Pair a device → Tablet, choose its room and role, and type the code it shows.",
                style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            OutlinedTextField(
                value = state.code,
                onValueChange = flow::setCode,
                modifier = Modifier.fillMaxWidth().heightIn(min = 96.dp),
                label = { Text("Pairing code") },
                singleLine = true,
                textStyle = TextStyle(fontFamily = DmMono, fontSize = 44.sp, letterSpacing = 6.sp, textAlign = TextAlign.Center),
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Characters,
                    keyboardType = KeyboardType.Ascii, autoCorrectEnabled = false, imeAction = ImeAction.Next),
            )
            OutlinedTextField(
                value = state.server,
                onValueChange = flow::setServer,
                modifier = Modifier.fillMaxWidth(),
                label = { Text("Server address") },
                placeholder = { Text("http://192.168.1.10:8080") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, autoCorrectEnabled = false, imeAction = ImeAction.Go),
                keyboardActions = KeyboardActions(onGo = { flow.submit() }),
            )
            FoundServers(state.found, state.server, flow::pick)
            state.error?.let { Text(it, style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.error) }
            Button(onClick = flow::submit, enabled = state.canSubmit, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp)) {
                Text(if (state.busy) "Pairing…" else "Pair")
            }
            TextButton(onClick = onSettings, modifier = Modifier.align(Alignment.End)) { Text("Wake word and display settings") }
        }
    }
}
