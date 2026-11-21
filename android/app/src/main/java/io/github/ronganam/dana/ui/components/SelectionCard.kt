package io.github.ronganam.dana.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.models.SelectionRequest

@Composable
fun SelectionCard(
    request: SelectionRequest,
    onSelect: (List<Int>) -> Unit,
    onCancel: () -> Unit,
    modifier: Modifier = Modifier
) {
    var selected by remember(request) { mutableStateOf<Set<Int>>(emptySet()) }

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
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(
                text = if (request.multiple) "Select one or more" else "Choose an option",
                fontSize = 10.sp,
                fontWeight = FontWeight.Medium,
                letterSpacing = 1.sp,
                color = accent
            )
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                text = request.prompt.ifBlank { "Please choose an option" },
                fontSize = 14.sp,
                fontWeight = FontWeight.Medium,
                color = MaterialTheme.colorScheme.onSurface
            )
            Spacer(modifier = Modifier.height(10.dp))

            request.options.forEachIndexed { index, option ->
                val isSelected = index in selected
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(6.dp))
                        .background(if (isSelected) accent.copy(alpha = 0.12f) else Color.Transparent)
                        .clickable {
                            if (request.multiple) {
                                selected = if (isSelected) selected - index else selected + index
                            } else {
                                onSelect(listOf(index))
                            }
                        }
                        .padding(horizontal = 12.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(8.dp)
                ) {
                    if (request.multiple) {
                        Box(
                            modifier = Modifier
                                .size(20.dp)
                                .clip(RoundedCornerShape(3.dp))
                                .background(if (isSelected) accent else Color.Transparent)
                                .border(1.dp, accent.copy(alpha = 0.6f), RoundedCornerShape(3.dp)),
                            contentAlignment = Alignment.Center
                        ) {
                            if (isSelected) {
                                Icon(
                                    imageVector = Icons.Default.Check,
                                    contentDescription = null,
                                    tint = MaterialTheme.colorScheme.onPrimary,
                                    modifier = Modifier.size(14.dp)
                                )
                            }
                        }
                    }
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            text = option.label,
                            fontSize = 14.sp,
                            fontWeight = FontWeight.Medium,
                            color = MaterialTheme.colorScheme.onSurface
                        )
                        if (option.value.isNotEmpty() && option.value != option.label) {
                            Text(
                                text = option.value,
                                fontSize = 11.sp,
                                color = muted.copy(alpha = 0.7f)
                            )
                        }
                    }
                    if (!request.multiple) {
                        Text(
                            text = "→",
                            fontSize = 14.sp,
                            color = accent
                        )
                    }
                }
                Spacer(modifier = Modifier.height(6.dp))
            }

            if (request.multiple) {
                Spacer(modifier = Modifier.height(4.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(8.dp)
                ) {
                    OutlinedButton(
                        onClick = onCancel,
                        colors = ButtonDefaults.outlinedButtonColors(contentColor = muted),
                        border = androidx.compose.foundation.BorderStroke(1.dp, muted.copy(alpha = 0.4f)),
                        modifier = Modifier.weight(1f)
                    ) {
                        Text("Cancel", fontWeight = FontWeight.Medium)
                    }
                    Button(
                        onClick = { onSelect(selected.toList().sorted()) },
                        enabled = selected.isNotEmpty(),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = accent,
                            contentColor = MaterialTheme.colorScheme.onPrimary,
                            disabledContainerColor = accent.copy(alpha = 0.3f),
                            disabledContentColor = muted.copy(alpha = 0.5f)
                        ),
                        modifier = Modifier.weight(1f)
                    ) {
                        Text("Confirm (${selected.size})", fontWeight = FontWeight.Medium)
                    }
                }
            } else {
                OutlinedButton(
                    onClick = onCancel,
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = muted),
                    border = androidx.compose.foundation.BorderStroke(1.dp, muted.copy(alpha = 0.4f)),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text("Cancel", fontWeight = FontWeight.Medium)
                }
            }
        }
    }
}