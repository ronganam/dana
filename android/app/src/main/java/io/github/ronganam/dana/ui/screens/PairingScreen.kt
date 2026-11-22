package io.github.ronganam.dana.ui.screens

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.ui.components.FoundServers
import io.github.ronganam.dana.ui.components.GlassCard
import io.github.ronganam.dana.ui.components.rememberFoundServers
import io.github.ronganam.dana.ui.components.ScreenTopBar
import io.github.ronganam.dana.ui.components.SectionLabel
import io.github.ronganam.dana.ui.theme.hues
import io.github.ronganam.dana.ui.viewmodels.MainViewModel

/**
 * Pairing this phone with a Dana server: the first screen of an unpaired app,
 * and Settings → "Pair again". Scanning the QR code of the web Devices page
 * with the camera opens its `dana://pair` link here; the form takes the same
 * server address and code typed.
 */
@Composable
fun PairingScreen(viewModel: MainViewModel) {
    val pairing = viewModel.pairingController
    val form = pairing.flow
    val state by form.state.collectAsState()
    val found by rememberFoundServers()
    LaunchedEffect(found) { form.discovered(found) }
    val paired by viewModel.paired.collectAsState()
    val snackbar = remember { SnackbarHostState() }
    LaunchedEffect(Unit) { viewModel.messages.collect { snackbar.showSnackbar(it) } }
    BackHandler(enabled = paired) { pairing.close() }

    Scaffold(snackbarHost = { SnackbarHost(snackbar) }, containerColor = MaterialTheme.colorScheme.background) { padding ->
        Column(
            Modifier.fillMaxSize().padding(padding),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            ScreenTopBar(
                title = if (paired) "Pair again" else "Welcome to Dana",
                showSettings = false,
                navigationIcon = if (paired) {
                    { IconButton(onClick = { pairing.close() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back") } }
                } else null
            )
            Column(
                Modifier
                    .widthIn(max = 520.dp)
                    .fillMaxWidth()
                    .weight(1f)
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp)
                    .navigationBarsPadding()
                    .imePadding()
                    .padding(bottom = 24.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Text(
                    if (paired) "Pairing with a new code replaces this phone's pairing with ${viewModel.serverConfig.url}."
                    else "Pair this phone with your Dana server to start.",
                    style = MaterialTheme.typography.bodyLarge
                )
                SectionLabel("Scan", hue = hues.sky)
                GlassCard(Modifier.fillMaxWidth()) {
                    Text(
                        "On Dana's web page, open Devices → Pair a device → Phone, then scan the QR code " +
                            "with this phone's camera and open the link in Dana.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                SectionLabel("Or type it", hue = hues.sky)
                GlassCard(Modifier.fillMaxWidth()) {
                    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        OutlinedTextField(
                            value = state.server,
                            onValueChange = form::setServer,
                            label = { Text("Server address") },
                            placeholder = { Text("dana.example.com") },
                            singleLine = true,
                            enabled = !state.busy,
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Next),
                            modifier = Modifier.fillMaxWidth()
                        )
                        FoundServers(state.found, state.server, form::pick)
                        OutlinedTextField(
                            value = state.code,
                            onValueChange = form::setCode,
                            label = { Text("Pairing code") },
                            placeholder = { Text("K7QM 4XPZ") },
                            singleLine = true,
                            enabled = !state.busy,
                            textStyle = MaterialTheme.typography.titleMedium.copy(fontFamily = FontFamily.Monospace),
                            keyboardOptions = KeyboardOptions(
                                capitalization = KeyboardCapitalization.Characters,
                                keyboardType = KeyboardType.Ascii,
                                autoCorrectEnabled = false,
                                imeAction = ImeAction.Done
                            ),
                            keyboardActions = KeyboardActions(onDone = { form.submit() }),
                            modifier = Modifier.fillMaxWidth()
                        )
                        state.error?.let {
                            Text(it, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error)
                        }
                        Button(
                            onClick = { form.submit() },
                            enabled = !state.busy,
                            modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
                        ) {
                            if (state.busy) {
                                Box(contentAlignment = Alignment.Center) {
                                    CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                                }
                            } else {
                                Text("Pair")
                            }
                        }
                    }
                }
                Spacer(Modifier.height(4.dp))
                Text(
                    "A code works once and lasts five minutes. Your server's admin can also make one for you.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
    }
}
