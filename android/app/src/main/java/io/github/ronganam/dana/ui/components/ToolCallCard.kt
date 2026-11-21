package io.github.ronganam.dana.ui.components

import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.models.ToolCallEntry
import io.github.ronganam.dana.ui.theme.DmMono
import java.text.SimpleDateFormat
import java.util.*

@Composable
fun ToolCallCard(
    messageId: String,
    entry: ToolCallEntry,
    onToggleCollapse: (String) -> Unit,
    onApprove: (Boolean) -> Unit,
    modifier: Modifier = Modifier
) {
    val chevronAngle by animateFloatAsState(
        targetValue = if (entry.isCollapsed) -90f else 0f,
        animationSpec = tween(300, easing = FastOutSlowInEasing),
        label = "chevron"
    )

    val timeLabel = SimpleDateFormat("HH:mm", Locale.getDefault())
        .format(Date(entry.timestamp))

    val accent = MaterialTheme.colorScheme.primary
    val muted = MaterialTheme.colorScheme.onSurfaceVariant

    Box(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(8.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .border(
                width = 1.dp,
                brush = SolidColor(MaterialTheme.colorScheme.outlineVariant),
                shape = RoundedCornerShape(8.dp)
            )
            .animateContentSize(tween(300, easing = FastOutSlowInEasing))
    ) {
        Column(modifier = Modifier.padding(12.dp)) {

            // ── Header (always visible, tappable) ──────────────────────────
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { onToggleCollapse(messageId) },
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Surface(
                    shape = RoundedCornerShape(4.dp),
                    color = accent.copy(alpha = 0.12f),
                    modifier = Modifier.padding(end = 2.dp)
                ) {
                    Text(
                        text = "⚙",
                        fontSize = 14.sp,
                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                        color = accent
                    )
                }

                Text(
                    // "send_whatsapp_message" → "Send whatsapp message": the card reads as words.
                    text = entry.toolName.replace('_', ' ').replaceFirstChar { it.uppercase() },
                    fontWeight = FontWeight.Medium,
                    fontSize = 13.sp,
                    color = accent,
                    modifier = Modifier.weight(1f)
                )

                // Live status: running spinner, or the outcome once known
                if (entry.isRunning || entry.isPreparing) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(12.dp),
                        strokeWidth = 1.5.dp,
                        color = accent
                    )
                    Text(
                        text = if (entry.isPreparing) "Preparing" else "Running",
                        fontSize = 10.sp,
                        color = accent.copy(alpha = 0.8f)
                    )
                } else if (entry.succeeded != null) {
                    Text(
                        text = if (entry.succeeded) "Done" else "Failed",
                        fontSize = 10.sp,
                        fontWeight = FontWeight.Medium,
                        color = if (entry.succeeded) accent.copy(alpha = 0.8f)
                        else MaterialTheme.colorScheme.error
                    )
                }

                entry.durationMs?.let { ms ->
                    Text(
                        text = "${ms}ms",
                        fontFamily = DmMono,
                        fontSize = 10.sp,
                        color = muted.copy(alpha = 0.7f)
                    )
                }

                // Approval badge
                if (entry.requiresApproval) {
                    Surface(
                        shape = RoundedCornerShape(4.dp),
                        color = MaterialTheme.colorScheme.error.copy(alpha = 0.14f)
                    ) {
                        Text(
                            text = "Asks first",
                            fontSize = 10.sp,
                            fontWeight = FontWeight.Medium,
                            color = MaterialTheme.colorScheme.error,
                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp)
                        )
                    }
                }

                Text(
                    text = timeLabel,
                    fontSize = 11.sp,
                    color = muted.copy(alpha = 0.7f)
                )

                Icon(
                    imageVector = Icons.Default.KeyboardArrowDown,
                    contentDescription = if (entry.isCollapsed) "Expand" else "Collapse",
                    tint = accent.copy(alpha = 0.7f),
                    modifier = Modifier
                        .size(20.dp)
                        .rotate(chevronAngle)
                )
            }

            // Tool summary subtitle
            if (!entry.isCollapsed && entry.toolSummary.isNotBlank() && entry.toolSummary != entry.toolName) {
                Text(
                    text = entry.toolSummary,
                    fontSize = 11.sp,
                    color = muted.copy(alpha = 0.75f),
                    modifier = Modifier.padding(top = 2.dp, start = 28.dp)
                )
            }

            // ── Collapsible body ───────────────────────────────────────────
            if (!entry.isCollapsed) {
                Spacer(modifier = Modifier.height(10.dp))
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                Spacer(modifier = Modifier.height(10.dp))

                JsonSection(label = "Parameters", json = entry.parameters)

                if (entry.response.isNotBlank()) {
                    Spacer(modifier = Modifier.height(10.dp))
                    JsonSection(label = "Response", json = entry.response)
                } else if (entry.isRunning) {
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(
                        text = "Running…",
                        fontSize = 12.sp,
                        color = muted
                    )
                }

                // ── Approval section ───────────────────────────────────────
                if (entry.requiresApproval) {
                    Spacer(modifier = Modifier.height(14.dp))
                    HorizontalDivider(color = MaterialTheme.colorScheme.error.copy(alpha = 0.3f))
                    Spacer(modifier = Modifier.height(10.dp))

                    Text(
                        text = "Dana asks first",
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 13.sp,
                        color = MaterialTheme.colorScheme.error
                    )
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(
                        // Only tools that send to someone or delete still ask (everything else just happens,
                        // with Undo) — and device commands in a turn that carries text others wrote: the shared
                        // phone screen, an email Dana read, or a chat opened from a transaction.
                        text = when (entry.approvalReason) {
                            "device_screen" -> "Asking because the screen was shared."
                            "untrusted_result", "untrusted_context" -> "Asking because this chat holds text others wrote."
                            else -> "This sends a message or deletes something, so it waits for you."
                        },
                        fontSize = 12.sp,
                        color = muted
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        OutlinedButton(
                            onClick = { onApprove(false) },
                            colors = ButtonDefaults.outlinedButtonColors(
                                contentColor = MaterialTheme.colorScheme.error
                            ),
                            border = androidx.compose.foundation.BorderStroke(
                                1.dp,
                                MaterialTheme.colorScheme.error.copy(alpha = 0.5f)
                            ),
                            modifier = Modifier.weight(1f)
                        ) {
                            Text("Cancel", fontWeight = FontWeight.Medium)
                        }
                        Button(
                            onClick = { onApprove(true) },
                            colors = ButtonDefaults.buttonColors(
                                containerColor = accent,
                                contentColor = MaterialTheme.colorScheme.onPrimary
                            ),
                            modifier = Modifier.weight(1f)
                        ) {
                            Text("Go ahead", fontWeight = FontWeight.Medium)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun JsonSection(label: String, json: String) {
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    Column {
        Text(
            text = label,
            fontSize = 10.sp,
            fontWeight = FontWeight.Medium,
            letterSpacing = 1.sp,
            color = muted.copy(alpha = 0.7f),
            modifier = Modifier.padding(bottom = 4.dp)
        )
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(4.dp))
                .background(MaterialTheme.colorScheme.surface.copy(alpha = 0.4f))
                .padding(horizontal = 10.dp, vertical = 8.dp)
                .heightIn(max = 200.dp)
        ) {
            Text(
                text = json.ifBlank { "—" },
                fontFamily = DmMono,
                fontSize = 11.sp,
                lineHeight = 16.sp,
                color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.75f),
                modifier = Modifier.verticalScroll(rememberScrollState())
            )
        }
    }
}