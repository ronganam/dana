package io.github.ronganam.dana.ui.viewmodels

import io.github.ronganam.dana.data.network.SuggestionEdits
import android.content.Context
import android.net.Uri
import android.util.Log
import io.github.ronganam.dana.data.models.CallLabel
import io.github.ronganam.dana.data.models.CallSuggestion
import io.github.ronganam.dana.data.models.PhoneCall
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream
import androidx.core.net.toUri
import io.github.ronganam.dana.data.sync.CallUploadStore
import io.github.ronganam.dana.data.sync.FolderScan
import io.github.ronganam.dana.data.sync.RecordingInspector
import io.github.ronganam.dana.data.sync.RecordingNames
import io.github.ronganam.dana.data.sync.RecordingsFolder
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale
import java.util.TimeZone

/** A file of the recordings folder offered for a manual upload. */
data class PendingRecordingFile(
    val uri: Uri,
    val name: String,
    val contactName: String,
    val timestamp: String,
    val durationSeconds: Int = 0
)

data class UploadFormState(
    val selectedUri: Uri? = null,
    val displayFileName: String = "",
    val caller: String = "",
    val receiver: String = "Me",
    val timestamp: String = "",
    val instructions: String = "",
    val isSubmitting: Boolean = false
)

/** Call recordings: list, re-analysis, and the manual upload flow. */
class CallsController(private val ctx: ControllerContext) {

    private val _calls = MutableStateFlow<List<PhoneCall>>(emptyList())
    val calls: StateFlow<List<PhoneCall>> = _calls.asStateFlow()

    private val _isLoadingCalls = MutableStateFlow(false)
    val isLoadingCalls: StateFlow<Boolean> = _isLoadingCalls.asStateFlow()

    private val _isScanningRecordings = MutableStateFlow(false)
    val isScanningRecordings: StateFlow<Boolean> = _isScanningRecordings.asStateFlow()

    private val _recordingSelectionList = MutableStateFlow<List<PendingRecordingFile>?>(null)
    val recordingSelectionList: StateFlow<List<PendingRecordingFile>?> = _recordingSelectionList.asStateFlow()

    private val _uploadFormState = MutableStateFlow<UploadFormState?>(null)
    val uploadFormState: StateFlow<UploadFormState?> = _uploadFormState.asStateFlow()

    private val _viewingCallId = MutableStateFlow<String?>(null)
    /** The call open on screen right now (Dana gets it as on-screen context). */
    val viewingCallId: StateFlow<String?> = _viewingCallId.asStateFlow()
    fun setViewing(callId: String?) { _viewingCallId.value = callId }

    private val _suggestions = MutableStateFlow<List<CallSuggestion>>(emptyList())
    /** Pending suggestions from calls (welcome area, Debrief, call detail). */
    val suggestions: StateFlow<List<CallSuggestion>> = _suggestions.asStateFlow()

    private val _busySuggestionIds = MutableStateFlow<Set<String>>(emptySet())
    val busySuggestionIds: StateFlow<Set<String>> = _busySuggestionIds.asStateFlow()

    private val _labelCatalog = MutableStateFlow<List<CallLabel>>(emptyList())
    val labelCatalog: StateFlow<List<CallLabel>> = _labelCatalog.asStateFlow()

    fun fetchSuggestions() {
        ctx.scope.launch {
            ctx.load("Suggestions") { ctx.api.calls.suggestions("pending") }?.let { _suggestions.value = it }
        }
    }

    fun fetchLabelCatalog() {
        if (_labelCatalog.value.isNotEmpty()) return
        ctx.scope.launch { ctx.load("CallLabels") { ctx.api.calls.labels() }?.let { _labelCatalog.value = it } }
    }

    private fun setBusy(id: String, busy: Boolean) {
        _busySuggestionIds.value = if (busy) _busySuggestionIds.value + id else _busySuggestionIds.value - id
    }

    /** Create the task/event. [onDone] runs after a success (e.g. refresh tasks). */
    fun acceptSuggestion(suggestion: CallSuggestion, overrides: SuggestionEdits = SuggestionEdits.None, onDone: () -> Unit = {}) {
        if (suggestion.id in _busySuggestionIds.value) return
        setBusy(suggestion.id, true)
        ctx.scope.launch {
            try {
                val result = ctx.api.calls.accept(suggestion.id, overrides)
                _suggestions.value = _suggestions.value.filterNot { it.id == suggestion.id }
                ctx.messages.post(
                    when (result.kind) {
                        "event" -> "Added to your calendar"
                        "task" -> "Reminder created"
                        else -> "Done"
                    }
                )
                onDone()
                fetchCalls()
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                ctx.messages.post(ctx.texts.reason(e))
            } finally {
                setBusy(suggestion.id, false)
            }
        }
    }

    fun dismissSuggestion(suggestion: CallSuggestion) {
        val before = _suggestions.value
        _suggestions.value = before.filterNot { it.id == suggestion.id }
        ctx.scope.launch {
            val ok = ctx.load("DismissSuggestion") { ctx.api.calls.dismiss(suggestion.id) } ?: false
            if (!ok) {
                _suggestions.value = before
                ctx.messages.post("Couldn't dismiss the suggestion")
            } else fetchCalls()
        }
    }

    fun updateCallLabels(callId: String, labelIds: List<String>) {
        ctx.action("Couldn't update labels") {
            ctx.api.calls.setLabels(callId, labelIds).also { if (it) fetchCalls() }
        }
    }

    fun fetchCalls() {
        ctx.scope.launch {
            _isLoadingCalls.value = true
            try {
                ctx.load("Calls") { ctx.api.calls.calls() }?.let { _calls.value = it }
            } finally {
                _isLoadingCalls.value = false
            }
        }
    }

    fun deleteCall(callId: String) {
        ctx.action("Couldn't delete recording") {
            ctx.api.calls.delete(callId).also { if (it) fetchCalls() }
        }
    }

    fun reanalyzeCall(callId: String, customInstructions: String?) {
        ctx.action("Couldn't start re-analysis") {
            ctx.api.calls.reanalyze(callId, customInstructions).also { if (it) fetchCalls() }
        }
    }

    fun startRecordingSelection(context: Context) {
        ctx.scope.launch {
            _isScanningRecordings.value = true
            val uploadedFilenames = _calls.value.map { it.filename }.toSet()
            val results = withContext(Dispatchers.IO) { scanRecordings(context, uploadedFilenames) }
            _isScanningRecordings.value = false
            _recordingSelectionList.value = results
        }
    }

    /** The recordings folder's newest audio files not uploaded from here yet (empty without a folder). */
    private fun scanRecordings(context: Context, uploadedFilenames: Set<String>): List<PendingRecordingFile> =
        RecordingsFolder.list(context)
            .filter { FolderScan.isAudio(it.name, it.mime) && it.name !in uploadedFilenames }
            .filter { CallUploadStore.state(context, it.name) != CallUploadStore.State.UPLOADED }
            .sortedByDescending { it.modifiedMs }
            .take(100)
            .map { doc ->
                val rec = RecordingNames.parse(doc.name)
                val call = RecordingInspector.matchCall(context, rec, doc.modifiedMs)
                val contact = rec.name ?: call?.name ?: rec.number ?: call?.number ?: doc.name.substringBeforeLast('.')
                val startMs = call?.startMs ?: rec.start?.atZone(java.time.ZoneId.systemDefault())?.toInstant()?.toEpochMilli() ?: doc.modifiedMs
                PendingRecordingFile(doc.uri.toUri(), doc.name, contact, isoLocal(startMs), call?.durationSeconds ?: 0)
            }

    fun clearRecordingSelection() {
        _recordingSelectionList.value = null
    }

    fun showUploadForm(uri: Uri, displayName: String, context: Context) {
        val ts = try {
            context.contentResolver.query(uri, null, null, null, null)?.use {
                val modIdx = it.getColumnIndex("last_modified")
                if (modIdx >= 0 && it.moveToFirst()) {
                    val cal = Calendar.getInstance().also { c -> c.timeInMillis = it.getLong(modIdx) * 1000 }
                    String.format(
                        Locale.US, "%04d-%02d-%02dT%02d:%02d:%02d",
                        cal.get(Calendar.YEAR), cal.get(Calendar.MONTH) + 1, cal.get(Calendar.DAY_OF_MONTH),
                        cal.get(Calendar.HOUR_OF_DAY), cal.get(Calendar.MINUTE), cal.get(Calendar.SECOND)
                    )
                } else ""
            } ?: ""
        } catch (e: Exception) {
            ""
        }

        val caller = displayName
            .removeSuffix(".m4a").removeSuffix(".mp3").removeSuffix(".wav")
            .removeSuffix(".ogg").removeSuffix(".flac")
            .replace("_", " ")
            .trim()

        _uploadFormState.value = UploadFormState(
            selectedUri = uri,
            displayFileName = displayName,
            caller = caller,
            receiver = "Me",
            timestamp = ts
        )
    }

    fun showUploadFormForRecording(pending: PendingRecordingFile) {
        _uploadFormState.value = UploadFormState(
            selectedUri = pending.uri,
            displayFileName = pending.name,
            caller = pending.contactName,
            receiver = "Me",
            timestamp = pending.timestamp
        )
    }

    fun dismissUploadForm() {
        _uploadFormState.value = null
    }

    fun updateUploadField(caller: String? = null, receiver: String? = null, timestamp: String? = null, instructions: String? = null) {
        val current = _uploadFormState.value ?: return
        _uploadFormState.value = current.copy(
            caller = caller ?: current.caller,
            receiver = receiver ?: current.receiver,
            timestamp = timestamp ?: current.timestamp,
            instructions = instructions ?: current.instructions
        )
    }

    fun submitUploadForm(context: Context) {
        val state = _uploadFormState.value ?: return
        val uri = state.selectedUri ?: return
        if (state.isSubmitting) return

        _uploadFormState.value = state.copy(isSubmitting = true)

        ctx.scope.launch {
            val file = withContext(Dispatchers.IO) { copyUriToTempFile(context, uri, state.displayFileName) }
            if (file == null) {
                _uploadFormState.value = state.copy(isSubmitting = false)
                ctx.messages.post("Couldn't read the selected file")
                return@launch
            }
            try {
                val uploaded = ctx.api.calls.upload(
                    file, state.caller, state.receiver, state.timestamp, state.instructions
                )
                if (uploaded != null) {
                    _uploadFormState.value = null
                    fetchCalls()
                } else {
                    _uploadFormState.value = state.copy(isSubmitting = false)
                    ctx.messages.post("Upload failed: the server rejected the file")
                }
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                Log.e("CallsController", "Error in manual upload", e)
                _uploadFormState.value = state.copy(isSubmitting = false)
                ctx.messages.post("Upload failed: ${ctx.texts.reason(e)}")
            } finally {
                file.delete()
            }
        }
    }

    private fun copyUriToTempFile(context: Context, uri: Uri, displayName: String): File? = try {
        val tempDir = File(context.cacheDir, "uploads").apply { mkdirs() }
        // Never let a provider-supplied name escape the cache directory.
        val file = File(tempDir, File(displayName).name.ifBlank { "recording.m4a" })
        val copied = context.contentResolver.openInputStream(uri)?.use { input ->
            FileOutputStream(file).use { output -> input.copyTo(output) }
        }
        if (copied == null) null else file
    } catch (e: Exception) {
        Log.e("CallsController", "Error copying URI to temp file", e)
        null
    }

    private fun isoLocal(ms: Long): String =
        SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).apply { timeZone = TimeZone.getDefault() }.format(ms)
}
