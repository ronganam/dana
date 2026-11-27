package io.github.ronganam.dana.ui.components.circuit

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.Orientation
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Chat
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.Email
import androidx.compose.material.icons.outlined.LocalLaundryService
import androidx.compose.material.icons.outlined.Place
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.ui.theme.DanaTheme
import io.github.ronganam.dana.ui.theme.circuitColors

@Composable
private fun PreviewFrame(dark: Boolean = true, content: @Composable () -> Unit) {
    DanaTheme(darkTheme = dark) {
        Column(
            Modifier.background(MaterialTheme.colorScheme.background).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) { content() }
    }
}

@Preview(name = "Nodes: shapes × states", widthDp = 360)
@Composable
private fun NodesPreview() = PreviewFrame {
    NodeState.entries.forEach { state ->
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            NodeShape.entries.forEach { shape ->
                CircuitNode(shape, state, size = NodeSize.Leading, icon = if (shape == NodeShape.Circle) Icons.Outlined.Email else null)
            }
            Text(state.name, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Preview(name = "Watching rows", widthDp = 390)
@Composable
private fun CircuitRowsPreview() = PreviewFrame {
    CircuitColumnHeader(startInset = 16.dp, endInset = 16.dp)
    GroupedSection {
        item(color = null) {
            CircuitRow(
                "Courier wants a time",
                source = LineNode(NodeShape.Circle, NodeState.Passed, icon = Icons.AutoMirrored.Outlined.Chat),
                rule = LineNode(NodeShape.Slot, NodeState.Passed),
                ai = LineNode(NodeShape.Diamond, NodeState.Passed),
                action = LineNode(NodeShape.RoundedSquare, NodeState.Waiting),
                time = "08:31", state = "Needs you"
            )
        }
        item {
            CircuitRow(
                "Leaving home, things left on",
                source = LineNode(NodeShape.Circle, NodeState.Passed, icon = Icons.Outlined.Place),
                rule = LineNode(NodeShape.Slot, NodeState.Passed),
                action = LineNode(NodeShape.RoundedSquare, NodeState.Firing),
                time = "08:42", state = "Running"
            )
        }
        item {
            CircuitRow(
                "Laundry done",
                source = LineNode(NodeShape.Circle, NodeState.Idle, icon = Icons.Outlined.LocalLaundryService),
                rule = LineNode(NodeShape.Slot),
                action = LineNode(NodeShape.RoundedSquare, NodeState.Done),
                time = "07:12", state = "On"
            )
        }
        item {
            CircuitRow(
                "School dates to calendar",
                source = LineNode(NodeShape.Circle, NodeState.Paused, icon = Icons.Outlined.Email),
                rule = LineNode(NodeShape.Slot, NodeState.Paused),
                ai = LineNode(NodeShape.Diamond, NodeState.Paused),
                action = LineNode(NodeShape.RoundedSquare, NodeState.Paused),
                time = "Nov 9", state = "Paused", dimmed = true
            )
        }
    }
}

@Preview(name = "Steps and lines", widthDp = 360)
@Composable
private fun StepsPreview() = PreviewFrame {
    StepLine(
        listOf(
            Step("Found 2 on", StepState.Done), Step("AC off", StepState.Done),
            Step("Lights off", StepState.Current), Step("Tell you", StepState.Next)
        ),
        Modifier.fillMaxWidth()
    )
    StepLineMini(done = 2, total = 4)
    NodeLine(
        listOf(
            LineNode(NodeShape.Circle, NodeState.Passed, "On waking"),
            LineNode(NodeShape.Circle, NodeState.Firing, "Leave home"),
            LineNode(NodeShape.Circle, NodeState.Idle, "At the office"),
            LineNode(NodeShape.RoundedSquare, NodeState.Idle, "Done", icon = Icons.Outlined.Check)
        ),
        Modifier.fillMaxWidth()
    )
    NodeLine(
        listOf(
            LineNode(NodeShape.Circle, NodeState.Done, "Asked Yoav"),
            LineNode(NodeShape.Diamond, NodeState.Dropped, "Not a delivery"),
            LineNode(NodeShape.RoundedSquare, NodeState.Idle, "Reply")
        ),
        orientation = Orientation.Vertical
    )
}

@Preview(name = "Timeline and groups", widthDp = 390)
@Composable
private fun TimelinePreview() = PreviewFrame {
    Column {
        TimelineRow("09:30", LineNode(NodeShape.Circle, NodeState.Idle), "Standup", subtitle = "At the office", first = true)
        TimelineRow("Office", LineNode(NodeShape.Circle, NodeState.Passed, icon = Icons.Outlined.Place), "Print the lease for Dad", subtitle = "When you get to the office", timeIsPlace = true)
        TimelineRow("13:00", LineNode(NodeShape.Circle, NodeState.Idle), "Lunch with Dad", subtitle = "Café Levinsky", last = true)
    }
    val c = circuitColors
    GroupedSection(title = "Needs you", meta = "2", metaColor = c.warm) {
        item(color = c.warmSurface) {
            GroupRow("Reply to Yoav Deliveries?", subtitle = "You are free tomorrow from 17:00", leading = { CircuitNode(NodeShape.RoundedSquare, NodeState.Waiting, size = NodeSize.Leading) })
        }
        item { GroupRow("Renew the car insurance", subtitle = "Two days late", leading = { CircuitNode(NodeShape.Circle, NodeState.Idle, size = NodeSize.Leading) }) }
    }
    AskDanaButton(onClick = {})
}

@Preview(name = "Light theme", widthDp = 390)
@Composable
private fun LightPreview() = PreviewFrame(dark = false) {
    CircuitRow(
        "Courier wants a time",
        source = LineNode(NodeShape.Circle, NodeState.Passed, icon = Icons.AutoMirrored.Outlined.Chat),
        rule = LineNode(NodeShape.Slot, NodeState.Passed),
        ai = LineNode(NodeShape.Diamond, NodeState.Passed),
        action = LineNode(NodeShape.RoundedSquare, NodeState.Waiting),
        time = "08:31", state = "Needs you"
    )
    StepLine(listOf(Step("Found", StepState.Done), Step("Turning off", StepState.Current), Step("Tell you", StepState.Next)), Modifier.fillMaxWidth())
}
