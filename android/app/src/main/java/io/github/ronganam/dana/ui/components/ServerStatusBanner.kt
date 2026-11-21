package io.github.ronganam.dana.ui.components

import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.windowInsetsPadding
import io.github.ronganam.dana.ui.theme.Success

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CloudDone
import androidx.compose.material.icons.filled.CloudOff
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.compositeOver
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.network.Reachability
import io.github.ronganam.dana.data.network.ServerStatus
import kotlinx.coroutines.delay

private enum class BannerKind { HIDDEN, OFFLINE, NOT_CONFIGURED, RECOVERED }

/**
 * A compact pill that says the assistant server can't be reached (usually
 * the VPN is off), with a Retry action. It briefly shows "Back online" when
 * the connection returns, and is hidden the rest of the time.
 */
@Composable
fun ServerStatusBanner(
    status: ServerStatus,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
    onOpenSettings: (() -> Unit)? = null,
    onVisibleChange: (Boolean) -> Unit = {}
) {
    var wasOffline by remember { mutableStateOf(false) }
    var showRecovered by remember { mutableStateOf(false) }

    LaunchedEffect(status.reachability) {
        when (status.reachability) {
            Reachability.OFFLINE -> {
                wasOffline = true
                showRecovered = false
            }
            Reachability.ONLINE -> if (wasOffline) {
                wasOffline = false
                showRecovered = true
                delay(2_500)
                showRecovered = false
            }
            else -> Unit
        }
    }

    val kind = when {
        status.reachability == Reachability.OFFLINE -> BannerKind.OFFLINE
        status.reachability == Reachability.NOT_CONFIGURED && onOpenSettings != null -> BannerKind.NOT_CONFIGURED
        showRecovered -> BannerKind.RECOVERED
        else -> BannerKind.HIDDEN
    }

    LaunchedEffect(kind != BannerKind.HIDDEN) { onVisibleChange(kind != BannerKind.HIDDEN) }

    AnimatedVisibility(
        visible = kind != BannerKind.HIDDEN,
        enter = expandVertically() + fadeIn(),
        exit = shrinkVertically() + fadeOut(),
        modifier = modifier
    ) {
        val (container, content) = when (kind) {
            BannerKind.RECOVERED -> Success.copy(alpha = 0.22f).compositeOver(MaterialTheme.colorScheme.surface) to MaterialTheme.colorScheme.onSurface
            BannerKind.NOT_CONFIGURED -> MaterialTheme.colorScheme.secondaryContainer to
                MaterialTheme.colorScheme.onSecondaryContainer
            else -> MaterialTheme.colorScheme.errorContainer to MaterialTheme.colorScheme.onErrorContainer
        }
        androidx.compose.foundation.layout.Box(
            modifier = androidx.compose.ui.Modifier.fillMaxWidth().windowInsetsPadding(androidx.compose.foundation.layout.WindowInsets.Companion.statusBars).padding(horizontal = 16.dp, vertical = 8.dp),
            contentAlignment = androidx.compose.ui.Alignment.Center
        ) {
        Surface(
            color = container,
            contentColor = content,
            shape = RoundedCornerShape(50),
            tonalElevation = 2.dp,
            shadowElevation = 4.dp,
            modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite }
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.Start,
                modifier = Modifier.padding(start = 14.dp, end = 4.dp)
            ) {
                Icon(
                    imageVector = when (kind) {
                        BannerKind.RECOVERED -> Icons.Filled.CloudDone
                        BannerKind.NOT_CONFIGURED -> Icons.Filled.Settings
                        else -> Icons.Filled.CloudOff
                    },
                    contentDescription = null,
                    modifier = Modifier.size(18.dp)
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    text = when (kind) {
                        BannerKind.RECOVERED -> "Back online"
                        BannerKind.NOT_CONFIGURED -> "Server not configured"
                        else -> status.detail ?: "Server unreachable"
                    },
                    style = MaterialTheme.typography.labelLarge,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f, fill = false)
                        .padding(vertical = if (kind == BannerKind.RECOVERED) 10.dp else 0.dp)
                )
                when (kind) {
                    BannerKind.OFFLINE -> {
                        if (status.checking) {
                            CircularProgressIndicator(
                                strokeWidth = 2.dp,
                                color = content,
                                // Same 40dp height as the Retry button, so the pill doesn't jump.
                                modifier = Modifier.padding(horizontal = 18.dp, vertical = 12.dp).size(16.dp)
                            )
                        } else {
                            TextButton(onClick = onRetry) { Text("Retry", color = content) }
                        }
                    }
                    BannerKind.NOT_CONFIGURED -> TextButton(onClick = { onOpenSettings?.invoke() }) {
                        Text("Settings", color = content)
                    }
                    else -> Spacer(Modifier.width(10.dp))
                }
            }
        }
        }
    }
}
