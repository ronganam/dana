package io.github.ronganam.dana.ui.shell

import android.text.format.DateUtils
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.network.SessionItem
import io.github.ronganam.dana.ui.components.MetaLabel

/**
 * "Background runs": the conversations Dana had on its own (a watch's AI
 * agent, `BackgroundRuns`), newest first, each with when it last changed.
 * Tapping one opens it in the thread pane as a visit ("Back to the thread"),
 * where its messages show and a waiting approval can be answered.
 */
@Composable
fun BackgroundRunsDialog(runs: List<SessionItem>, onOpen: (String) -> Unit, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Background runs") },
        text = {
            if (runs.isEmpty()) {
                Text(
                    "None yet. When a watch hands something to Dana's AI agent, its run shows here.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            } else LazyColumn(Modifier.heightIn(max = 420.dp)) {
                items(runs, key = { it.id }) { run ->
                    Column(
                        Modifier.fillMaxWidth().heightIn(min = 48.dp).clickable { onOpen(run.id) }.padding(vertical = 8.dp)
                    ) {
                        Text(
                            run.title.ifBlank { run.id },
                            style = MaterialTheme.typography.bodyLarge.copy(textDirection = TextDirection.Content),
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                        MetaLabel(
                            listOfNotNull(
                                run.status.takeIf { it != "idle" && it.isNotBlank() },
                                run.timestamp.takeIf { it > 0 }?.let {
                                    DateUtils.getRelativeTimeSpanString(it, System.currentTimeMillis(), DateUtils.MINUTE_IN_MILLIS).toString()
                                }
                            ).joinToString(" · ")
                        )
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } }
    )
}
