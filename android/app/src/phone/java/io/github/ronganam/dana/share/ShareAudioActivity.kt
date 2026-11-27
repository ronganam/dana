package io.github.ronganam.dana.share

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.DocumentsContract
import android.provider.OpenableColumns
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.dp
import androidx.lifecycle.lifecycleScope
import io.github.ronganam.dana.data.network.ServerConfig
import io.github.ronganam.dana.data.projects.Project
import io.github.ronganam.dana.data.projects.ProjectsApi
import io.github.ronganam.dana.data.sync.CallUploads
import io.github.ronganam.dana.data.sync.LoggedCall
import io.github.ronganam.dana.data.sync.RecordingInspector
import io.github.ronganam.dana.data.sync.RecordingName
import io.github.ronganam.dana.data.sync.RecordingNames
import io.github.ronganam.dana.data.sync.RecordingsFolder
import io.github.ronganam.dana.data.sync.SharedAudio
import io.github.ronganam.dana.ui.components.InkChip
import io.github.ronganam.dana.ui.components.MetaLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.screens.calls.formatDuration
import io.github.ronganam.dana.ui.theme.DanaTheme
import io.github.ronganam.dana.ui.theme.ThemeSettings
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** One shared audio file, copied into the app (the share's read grant ends with this activity). */
private data class SharedRecording(
    val file: File,
    val name: String,
    val mime: String?,
    val modifiedMs: Long,
    val rec: RecordingName,
    val durationSeconds: Int,
    val guess: LoggedCall?
)

/** What the user says a shared file is. */
private sealed interface ShareChoice {
    data class Call(val call: LoggedCall) : ShareChoice
    /** A call that isn't in the call log list: the file name's details. */
    data object OtherCall : ShareChoice
    /** Not a call: a note in a project, transcribed by the server. */
    data class Note(val project: Project) : ShareChoice
}

/**
 * Share to Dana for audio (ACTION_SEND / SEND_MULTIPLE `audio/` from any
 * app): one small sheet per file asks which call it was — recent calls from
 * the call log, the one the file matches picked already ([RecordingInspector.matchCall]),
 * or "a call not in this list" — and uploads it like a found recording
 * ([CallUploads]). With the knowledge addon, "Not a call: just transcribe and
 * keep it" saves it as a note in a project instead ([SharedAudioNoteWorker]).
 */
class ShareAudioActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        ThemeSettings.load(applicationContext)
        if (!ServerConfig.load(this).isConfigured) {
            Toast.makeText(this, "Pair Dana with your server first.", Toast.LENGTH_LONG).show()
            finish()
            return
        }
        val uris = sharedUris(intent)
        if (uris.isEmpty()) {
            finish()
            return
        }
        val files = mutableStateOf<List<SharedRecording>?>(null)
        val calls = mutableStateOf(RecordingInspector.recentCalls(this))
        val projects = mutableStateOf<List<Project>?>(null)
        lifecycleScope.launch {
            files.value = withContext(Dispatchers.IO) { uris.mapNotNull { load(it) } }
            if (files.value.isNullOrEmpty()) {
                Toast.makeText(this@ShareAudioActivity, "Couldn't read the shared file.", Toast.LENGTH_LONG).show()
                finish()
            }
        }
        lifecycleScope.launch {
            // Notes need the knowledge addon: no answer (offline, not connected) = calls only.
            projects.value = runCatching {
                val api = ServerConfig.load(this@ShareAudioActivity).api()
                if (ProjectsApi.ADDON !in api.devices.me().addons) emptyList()
                else ProjectsApi(applicationContext) { api }.projects().filter { it.status == "active" }
            }.getOrDefault(emptyList())
        }
        setContent {
            DanaTheme {
                val list = files.value
                if (list != null && list.isNotEmpty()) {
                    ShareFlow(
                        files = list,
                        calls = calls.value,
                        projects = projects.value.orEmpty(),
                        onCallLogGranted = {
                            calls.value = RecordingInspector.recentCalls(this)
                            files.value = list.map { it.copy(guess = RecordingInspector.matchCall(this, it.rec, it.modifiedMs)) }
                        },
                        onDone = ::finish
                    )
                }
            }
        }
    }

    private fun sharedUris(intent: Intent): List<Uri> {
        val clip = intent.clipData?.let { c -> (0 until c.itemCount).mapNotNull { c.getItemAt(it).uri } }.orEmpty()
        val single: Uri?
        val multiple: List<Uri>?
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            single = intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
            multiple = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java)
        } else {
            @Suppress("DEPRECATION")
            single = if (intent.action == Intent.ACTION_SEND) intent.getParcelableExtra(Intent.EXTRA_STREAM) else null
            @Suppress("DEPRECATION")
            multiple = if (intent.action == Intent.ACTION_SEND_MULTIPLE) intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM) else null
        }
        return SharedAudio.pick(intent.action, intent.type, single, multiple, clip) { contentResolver.getType(it) }
    }

    /** Copy [uri] into the app and read what it says about itself. */
    private fun load(uri: Uri): SharedRecording? {
        var name: String? = null
        var size = -1L
        var modified = 0L
        runCatching {
            contentResolver.query(uri, null, null, null, null)?.use { c ->
                if (c.moveToFirst()) {
                    c.getColumnIndex(OpenableColumns.DISPLAY_NAME).takeIf { it >= 0 }?.let { name = c.getString(it) }
                    c.getColumnIndex(OpenableColumns.SIZE).takeIf { it >= 0 && !c.isNull(it) }?.let { size = c.getLong(it) }
                    c.getColumnIndex(DocumentsContract.Document.COLUMN_LAST_MODIFIED).takeIf { it >= 0 && !c.isNull(it) }
                        ?.let { modified = c.getLong(it) }
                }
            }
        }
        val fileName = name?.takeIf { it.isNotBlank() } ?: uri.lastPathSegment?.substringAfterLast('/') ?: "recording.m4a"
        val file = RecordingsFolder.copy(this, uri, fileName, size) ?: return null
        val rec = RecordingNames.parse(fileName)
        return SharedRecording(
            file, fileName, contentResolver.getType(uri), modified, rec,
            RecordingInspector.durationSeconds(file), RecordingInspector.matchCall(this, rec, modified)
        )
    }
}

@Composable
private fun ShareFlow(
    files: List<SharedRecording>,
    calls: List<LoggedCall>,
    projects: List<Project>,
    onCallLogGranted: () -> Unit,
    onDone: () -> Unit
) {
    val context = androidx.compose.ui.platform.LocalContext.current
    var index by remember { mutableIntStateOf(0) }
    var sent by remember { mutableIntStateOf(0) }
    val current = files[index.coerceAtMost(files.lastIndex)]
    fun next(uploaded: Boolean) {
        if (uploaded) sent++ else RecordingsFolder.release(context, current.file)
        if (index < files.lastIndex) index++ else {
            if (sent > 0) Toast.makeText(context, if (sent == 1) "Sent to Dana" else "$sent files sent to Dana", Toast.LENGTH_SHORT).show()
            onDone()
        }
    }
    key(index) {
        ShareSheet(
            item = current,
            position = if (files.size > 1) "${index + 1} of ${files.size}" else null,
            calls = calls,
            projects = projects,
            onCallLogGranted = onCallLogGranted,
            onSend = { choice ->
                when (choice) {
                    is ShareChoice.Call -> CallUploads.enqueue(context, RecordingInspector.describe(current.file, current.rec, choice.call, current.modifiedMs), replace = true)
                    ShareChoice.OtherCall -> CallUploads.enqueue(context, RecordingInspector.describe(current.file, current.rec, null, current.modifiedMs), replace = true)
                    is ShareChoice.Note -> SharedAudioNoteWorker.enqueue(
                        context, current.file, current.mime, current.rec.name ?: current.file.nameWithoutExtension,
                        choice.project.id, choice.project.title
                    )
                }
                next(uploaded = true)
            },
            onSkip = { next(uploaded = false) },
            onClose = {
                files.drop(index).forEach { RecordingsFolder.release(context, it.file) }
                onDone()
            }
        )
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ShareSheet(
    item: SharedRecording,
    position: String?,
    calls: List<LoggedCall>,
    projects: List<Project>,
    onCallLogGranted: () -> Unit,
    onSend: (ShareChoice) -> Unit,
    onSkip: () -> Unit,
    onClose: () -> Unit
) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val looksLikeCall = item.guess != null || item.rec.start != null || item.rec.number != null
    var choice by remember(item) {
        mutableStateOf<ShareChoice?>(item.guess?.let { ShareChoice.Call(it) } ?: if (looksLikeCall) ShareChoice.OtherCall else null)
    }
    var noteChosen by remember(item) { mutableStateOf(false) }
    val callLogGranted = context.checkSelfPermission(Manifest.permission.READ_CALL_LOG) == PackageManager.PERMISSION_GRANTED
    val askCallLog = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { if (it) onCallLogGranted() }
    // The matched call first, then the rest newest first.
    val listed = remember(calls, item.guess) { listOfNotNull(item.guess) + calls.filterNot { it == item.guess } }

    Box(
        Modifier
            .fillMaxSize()
            .background(Color.Black.copy(alpha = 0.45f))
            .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, onClick = onClose)
            .statusBarsPadding(),
        contentAlignment = Alignment.BottomCenter
    ) {
        Column(
            Modifier
                .widthIn(max = 560.dp)
                .fillMaxWidth()
                .heightIn(max = 720.dp)
                .clip(RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp))
                .background(MaterialTheme.colorScheme.background)
                .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null) {}
                .navigationBarsPadding()
                .padding(horizontal = 24.dp, vertical = 20.dp)
        ) {
            MetaLabel(
                buildString {
                    append("Share to Dana")
                    position?.let { append(" · ").append(it) }
                    if (item.durationSeconds > 0) append(" · ").append(formatDuration(item.durationSeconds))
                },
                color = MaterialTheme.colorScheme.primary
            )
            Spacer(Modifier.height(6.dp))
            Text(
                item.name,
                style = MaterialTheme.typography.titleLarge.copy(textDirection = TextDirection.Content),
                color = MaterialTheme.colorScheme.onBackground,
                maxLines = 2
            )
            Spacer(Modifier.height(12.dp))
            Column(Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState())) {
                Text("Which call was it?", style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.onBackground)
                Spacer(Modifier.height(4.dp))
                if (!callLogGranted) {
                    Text(
                        "Allow the call log to pick the call, with its number and direction.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    TextButton(onClick = { askCallLog.launch(Manifest.permission.READ_CALL_LOG) }) { Text("Allow call log") }
                }
                listed.forEach { call ->
                    ChoiceRow(
                        title = call.name ?: call.number ?: "Unknown number",
                        detail = callDetail(call) + if (call == item.guess) " · matches the file" else "",
                        selected = choice == ShareChoice.Call(call),
                        onClick = { choice = ShareChoice.Call(call); noteChosen = false }
                    )
                }
                ChoiceRow(
                    title = "A call not in this list",
                    detail = listOfNotNull(item.rec.name, item.rec.number).joinToString(" · ").ifBlank { "Details from the file" },
                    selected = choice == ShareChoice.OtherCall,
                    onClick = { choice = ShareChoice.OtherCall; noteChosen = false }
                )
                if (projects.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    Rule()
                    ChoiceRow(
                        title = "Not a call: just transcribe and keep it",
                        detail = "A note in a project, with the recording and its transcript",
                        selected = noteChosen,
                        onClick = { noteChosen = true; choice = null }
                    )
                    if (noteChosen) {
                        FlowRow(
                            Modifier.padding(start = 34.dp, bottom = 4.dp),
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                            verticalArrangement = Arrangement.spacedBy(6.dp)
                        ) {
                            projects.forEach { project ->
                                InkChip(
                                    project.title,
                                    selected = (choice as? ShareChoice.Note)?.project?.id == project.id,
                                    onClick = { choice = ShareChoice.Note(project) }
                                )
                            }
                        }
                    }
                }
            }
            Spacer(Modifier.height(14.dp))
            Button(
                onClick = { choice?.let(onSend) },
                enabled = choice != null,
                modifier = Modifier.fillMaxWidth().height(48.dp)
            ) { Text(if (choice is ShareChoice.Note) "Save" else "Upload") }
            Spacer(Modifier.height(8.dp))
            OutlinedButton(onClick = onSkip, modifier = Modifier.fillMaxWidth().height(48.dp)) {
                Text(if (position != null) "Skip this file" else "Cancel")
            }
        }
    }
}

@Composable
private fun ChoiceRow(title: String, detail: String, selected: Boolean, onClick: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .selectable(selected = selected, onClick = onClick, role = Role.RadioButton)
            .heightIn(min = 48.dp)
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        RadioButton(selected = selected, onClick = null)
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge.copy(textDirection = TextDirection.Content), color = MaterialTheme.colorScheme.onBackground)
            Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

private fun callDetail(call: LoggedCall): String {
    val time = SimpleDateFormat("EEE d MMM, HH:mm", Locale.getDefault()).format(Date(call.startMs))
    return "$time · ${formatDuration(call.durationSeconds)} · ${call.direction}"
}
