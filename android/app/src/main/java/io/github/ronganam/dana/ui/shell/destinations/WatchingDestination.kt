package io.github.ronganam.dana.ui.shell.destinations

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.ui.components.circuit.GroupedSection
import io.github.ronganam.dana.ui.components.circuit.LineNode
import io.github.ronganam.dana.ui.components.circuit.NodeLine
import io.github.ronganam.dana.ui.components.circuit.NodeShape
import io.github.ronganam.dana.ui.components.circuit.askSlotInset
import io.github.ronganam.dana.ui.shell.Destination
import io.github.ronganam.dana.ui.theme.Space
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import io.github.ronganam.dana.ui.viewmodels.Place

/**
 * Watching: what Dana watches for the owner, each watch drawn as its circuit
 * (source → rule → AI check → action). A stub for now — its header and an
 * empty state that explains the shapes; the list of watches
 * (`/api/watches`), one watch and a new watch come in phase 2
 * (`docs/redesign-circuit.md`).
 */
@Composable
fun rememberWatchingDestination(@Suppress("UNUSED_PARAMETER") viewModel: MainViewModel): Destination = Destination(
    place = Place.WATCHING,
    picked = false,
    onBack = {},
    list = { WatchingList() },
    stage = null
)

@Composable
private fun WatchingList() {
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Space.screen, vertical = 12.dp)
    ) {
        Text(
            "Watching",
            style = MaterialTheme.typography.displayMedium,
            modifier = Modifier.askSlotInset().semantics { heading() }
        )
        Text(
            "What Dana keeps an eye on for you shows here.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 4.dp).askSlotInset()
        )
        GroupedSection(Modifier.padding(top = 24.dp)) {
            item {
                Column(Modifier.padding(16.dp)) {
                    Text("Each watch is a line", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "Something happens, a rule and Dana check it, then Dana acts or asks you.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 2.dp, bottom = 16.dp)
                    )
                    NodeLine(
                        listOf(
                            LineNode(NodeShape.Circle, label = "When"),
                            LineNode(NodeShape.Slot, label = "Rule"),
                            LineNode(NodeShape.Diamond, label = "AI check"),
                            LineNode(NodeShape.RoundedSquare, label = "Then")
                        ),
                        Modifier.fillMaxWidth()
                    )
                }
            }
        }
        Text(
            "Ask Dana to watch something, like “tell me when the laundry is done”.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 16.dp)
        )
    }
}
