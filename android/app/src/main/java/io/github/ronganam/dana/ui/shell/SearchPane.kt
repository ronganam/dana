package io.github.ronganam.dana.ui.shell

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.models.SearchResult
import io.github.ronganam.dana.ui.components.MetaLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.viewmodels.SearchController

/**
 * What the ask bar's text finds (`GET /api/search`), grouped by kind, and a
 * last row that asks Dana instead. Unfolded it fills the stage; on the cover
 * screen it covers the content while the text is there.
 */
@Composable
fun SearchPane(
    query: String,
    state: SearchController.State,
    wide: Boolean,
    onResult: (SearchResult) -> Unit,
    onAsk: () -> Unit
) {
    val gutter = if (wide) 26.dp else 18.dp
    val groups = SearchController.grouped(state.results)
    val count = state.results.size
    val current = state.query == query.trim()
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(top = 18.dp, bottom = 24.dp)) {
        item(key = "head") {
            Column(Modifier.padding(horizontal = gutter).widthIn(max = 720.dp)) {
                MetaLabel(
                    when {
                        state.failed && current -> "Search didn't answer"
                        state.searching || !current -> "Looking…"
                        else -> "In your things · $count"
                    }
                )
                if (!state.searching && current && !state.failed && count == 0) Text(
                    "Nothing of yours matches.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 8.dp)
                )
            }
        }
        groups.forEach { (kind, results) ->
            item(key = "k_$kind") {
                Column(Modifier.padding(horizontal = gutter).padding(top = 16.dp)) {
                    MetaLabel(SearchController.kindLabel(kind), modifier = Modifier.padding(bottom = 6.dp))
                    Rule()
                }
            }
            items(results, key = { "r_${it.kind}_${it.id}" }) { r -> ResultRow(r, gutter, onResult) }
        }
        item(key = "ask") {
            Column(Modifier.padding(horizontal = gutter).padding(top = 22.dp)) {
                MetaLabel("Or ask Dana", modifier = Modifier.padding(bottom = 6.dp))
                Rule()
                Row(
                    Modifier.fillMaxWidth().clickable(onClick = onAsk).padding(vertical = 13.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        "Ask Dana: “${query.trim()}”",
                        style = MaterialTheme.typography.bodyLarge.copy(fontWeight = FontWeight.Medium, textDirection = TextDirection.Content),
                        color = MaterialTheme.colorScheme.primary,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f)
                    )
                    MetaLabel("↵")
                }
                Rule()
            }
        }
    }
}

@Composable
private fun ResultRow(r: SearchResult, gutter: androidx.compose.ui.unit.Dp, onResult: (SearchResult) -> Unit) {
    Column(Modifier.fillMaxWidth().clickable { onResult(r) }.padding(horizontal = gutter)) {
        Column(Modifier.padding(vertical = 11.dp)) {
            Text(
                r.title,
                style = MaterialTheme.typography.bodyLarge.copy(textDirection = TextDirection.Content),
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.fillMaxWidth()
            )
            if (r.subtitle.isNotBlank()) Text(
                io.github.ronganam.dana.ui.screens.common.isolatedParts(r.subtitle),
                style = MaterialTheme.typography.bodySmall.copy(textDirection = TextDirection.Content),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.fillMaxWidth().padding(top = 2.dp)
            )
        }
        Rule()
    }
}
