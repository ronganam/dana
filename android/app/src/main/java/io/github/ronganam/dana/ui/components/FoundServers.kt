package io.github.ronganam.dana.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.State
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.discovery.AndroidNsdBrowser
import io.github.ronganam.dana.data.discovery.DiscoveredServer
import io.github.ronganam.dana.data.discovery.ServerDiscovery

/** Dana servers on the home network while this composable is shown (`_dana._tcp` discovery). */
@Composable
fun rememberFoundServers(): State<List<DiscoveredServer>> {
    val context = LocalContext.current
    val discovery = remember { ServerDiscovery(AndroidNsdBrowser(context)) }
    DisposableEffect(discovery) {
        discovery.start()
        onDispose { discovery.stop() }
    }
    return discovery.servers.collectAsState()
}

/** The servers found on this network as chips; picking one fills the address field. Nothing when none were found. */
@Composable
fun FoundServers(servers: List<DiscoveredServer>, selectedUrl: String, onPick: (DiscoveredServer) -> Unit, modifier: Modifier = Modifier) {
    if (servers.isNotEmpty()) FoundServerChips(servers, selectedUrl, onPick, modifier)
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun FoundServerChips(servers: List<DiscoveredServer>, selectedUrl: String, onPick: (DiscoveredServer) -> Unit, modifier: Modifier) {
    Column(modifier, verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(
            if (servers.size == 1) "Found on this network" else "Found on this network — pick one",
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            servers.forEach { server ->
                FilterChip(
                    selected = server.url == selectedUrl,
                    onClick = { onPick(server) },
                    label = { Text(foundServerLabel(server)) },
                )
            }
        }
    }
}

/** "Dana · 192.168.1.5:8080 · 10.1.0". */
fun foundServerLabel(server: DiscoveredServer): String =
    listOfNotNull(server.name, server.url.substringAfter("://"), server.version).distinct().joinToString(" · ")
