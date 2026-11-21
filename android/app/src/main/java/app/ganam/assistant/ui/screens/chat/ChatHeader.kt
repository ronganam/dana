package app.ganam.assistant.ui.screens.chat

import android.Manifest
import android.content.pm.PackageManager
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.CallEnd
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Phone
import androidx.compose.material.icons.outlined.ViewSidebar
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import app.ganam.assistant.ui.components.MonoLabel
import app.ganam.assistant.ui.components.ScreenTopBarSlots
import app.ganam.assistant.ui.viewmodels.MainViewModel
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * The single global header for multi-pane Chat (same slot as every other screen's ScreenTopBar):
 * date kicker + serif title, then "New chat", model, call and context actions.
 */
@Composable
fun ChatHeader(
    viewModel: MainViewModel,
    onToggleContext: (() -> Unit)?,
    contextOpen: Boolean,
    modifier: Modifier = Modifier
) {
    val context = LocalContext.current
    val selectedModel by viewModel.selectedRealtimeModel.collectAsState()
    val isCallActive by viewModel.isCallActive.collectAsState()
    var menuOpen by remember { mutableStateOf(false) }

    val micLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) viewModel.startRealtimeCall()
        else Toast.makeText(context, "Microphone permission required for call mode", Toast.LENGTH_SHORT).show()
    }

    ScreenTopBarSlots(
        modifier = modifier,
        title = {
            Column {
                MonoLabel(
                    SimpleDateFormat("EEE d MMM", Locale.getDefault()).format(Date()),
                    color = MaterialTheme.colorScheme.primary
                )
                Text("Dana", style = MaterialTheme.typography.headlineMedium)
            }
        },
        actions = {
            TextButton(onClick = { viewModel.startNewSession(saveOld = true) }) {
                Icon(Icons.Filled.Add, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(6.dp))
                Text("New chat")
            }
            Box {
                Surface(
                    shape = RoundedCornerShape(20.dp),
                    color = MaterialTheme.colorScheme.surfaceContainerHigh,
                    modifier = Modifier.clip(RoundedCornerShape(20.dp)).clickable(enabled = !isCallActive) { menuOpen = true }
                ) {
                    Row(Modifier.padding(horizontal = 10.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                        MonoLabel(if (selectedModel.contains("mini")) "Mini" else "Realtime", color = MaterialTheme.colorScheme.primary)
                        Icon(Icons.Default.ArrowDropDown, contentDescription = "Select Realtime Model", modifier = Modifier.size(16.dp))
                    }
                }
                DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                    listOf("gpt-realtime" to "GPT Realtime", "gpt-realtime-mini" to "GPT Realtime Mini").forEach { (id, label) ->
                        DropdownMenuItem(
                            text = { Text(label) },
                            onClick = { viewModel.setRealtimeModel(id); menuOpen = false },
                            leadingIcon = { if (selectedModel == id) Icon(Icons.Default.Check, contentDescription = null) }
                        )
                    }
                }
            }
            IconButton(onClick = {
                if (isCallActive) viewModel.endRealtimeCall()
                else if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED)
                    viewModel.startRealtimeCall()
                else micLauncher.launch(Manifest.permission.RECORD_AUDIO)
            }) {
                if (isCallActive) Icon(Icons.Default.CallEnd, contentDescription = "Hang Up Call", tint = MaterialTheme.colorScheme.error)
                else Icon(Icons.Default.Phone, contentDescription = "Start Realtime Call", tint = MaterialTheme.colorScheme.primary)
            }
            if (onToggleContext != null) {
                IconButton(onClick = onToggleContext) {
                    Icon(
                        Icons.Outlined.ViewSidebar,
                        contentDescription = if (contextOpen) "Hide context" else "Show context",
                        tint = if (contextOpen) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
        }
    )
}
