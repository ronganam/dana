package io.github.ronganam.dana.ui.screens.chat

import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import io.github.ronganam.dana.data.chat.ThreadModel
import io.github.ronganam.dana.ui.components.MonoLabel
import kotlinx.coroutines.delay

/** How faint a message Dana no longer remembers is drawn. */
const val FORGOTTEN_ALPHA = 0.45f

/**
 * "Dana remembers from here" between two accent hairlines: the thread's
 * messages above it are no longer in Dana's mind (see [ThreadModel.Memory]).
 */
@Composable
fun MemoryLine(label: String, modifier: Modifier = Modifier) {
    val accent = MaterialTheme.colorScheme.primary
    Row(modifier.fillMaxWidth().padding(top = 10.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        HorizontalDivider(Modifier.weight(1f), thickness = 1.dp, color = accent.copy(alpha = 0.35f))
        MonoLabel(label, color = accent, modifier = Modifier.padding(horizontal = 10.dp))
        HorizontalDivider(Modifier.weight(1f), thickness = 1.dp, color = accent.copy(alpha = 0.35f))
    }
}

/**
 * The time (epoch ms) to judge [memory] by: now, refreshed when the quiet gap
 * runs out and whenever the screen resumes (a timer doesn't run while the
 * phone sleeps).
 */
@Composable
fun rememberMemoryNow(memory: ThreadModel.Memory): Long {
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) now = System.currentTimeMillis()
        }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer) }
    }
    val expires = (memory as? ThreadModel.Memory.Known)?.expiresAtMs
    LaunchedEffect(memory) {
        now = System.currentTimeMillis()
        if (expires != null && expires > now) {
            delay(expires - now + 500)
            now = System.currentTimeMillis()
        }
    }
    return now
}
