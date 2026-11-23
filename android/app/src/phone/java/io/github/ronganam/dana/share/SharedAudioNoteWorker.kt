package io.github.ronganam.dana.share

import android.content.Context
import android.util.Log
import android.webkit.MimeTypeMap
import androidx.core.app.NotificationCompat
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import io.github.ronganam.dana.data.network.ServerConfig
import io.github.ronganam.dana.data.projects.EntryChanges
import io.github.ronganam.dana.data.projects.ProjectsApi
import io.github.ronganam.dana.data.projects.ProjectsApiError
import io.github.ronganam.dana.data.sync.CallUploads
import io.github.ronganam.dana.data.sync.RecordingsFolder
import kotlinx.coroutines.CancellationException
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * A shared recording that is not a call ("just transcribe and keep it"): a
 * note in one of the user's projects with the audio as its file
 * (`POST /api/entries`, then `POST /api/entries/{id}/files`); the server
 * transcribes the audio in the background (knowledge addon, docs/knowledge.md
 * → Files). Waits for a network and retries like the call uploads.
 */
class SharedAudioNoteWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val ctx = applicationContext
        val file = File(inputData.getString(KEY_PATH) ?: return Result.failure())
        val projectId = inputData.getString(KEY_PROJECT) ?: return Result.failure()
        val title = inputData.getString(KEY_TITLE).orEmpty().ifBlank { file.nameWithoutExtension }
        val mime = inputData.getString(KEY_MIME)?.takeIf { it.startsWith("audio/") }
            ?: MimeTypeMap.getSingleton().getMimeTypeFromExtension(file.extension.lowercase())
            ?: "audio/mpeg"
        val projectTitle = inputData.getString(KEY_PROJECT_TITLE).orEmpty()
        if (!file.exists()) return Result.failure()
        val api = ProjectsApi(ctx) { ServerConfig.load(ctx).api() }
        var entryId: String? = null
        return try {
            val entry = api.createEntry(EntryChanges(projectId = projectId, title = title, kind = "note"))
                ?: return failed(ctx, file, title, "The project is gone.")
            entryId = entry.id
            api.uploadFile(entry.id, file.name, mime, file.length(), note = "") { file.inputStream() }
            RecordingsFolder.release(ctx, file)
            CallUploads.notify(ctx, file.name, NotificationCompat.Builder(ctx, CallUploads.CHANNEL_ID)
                .setSmallIcon(android.R.drawable.stat_sys_upload_done)
                .setContentTitle("Saved “$title”")
                .setContentText(if (projectTitle.isBlank()) "Dana is transcribing it." else "In $projectTitle. Dana is transcribing it.")
                .setAutoCancel(true))
            Result.success()
        } catch (e: CancellationException) {
            throw e
        } catch (e: ProjectsApiError) {
            // A refusal (too large, no access): trying again won't help.
            entryId?.let { id -> runCatching { api.deleteEntry(id) } }
            failed(ctx, file, title, e.message)
        } catch (e: IOException) {
            // Unreachable: drop a half-made note, try again later.
            entryId?.let { id -> runCatching { api.deleteEntry(id) } }
            Log.w(TAG, "Saving ${file.name} failed; will retry", e)
            if (runAttemptCount + 1 < MAX_ATTEMPTS) Result.retry() else failed(ctx, file, title, "The server can't be reached.")
        }
    }

    private fun failed(ctx: Context, file: File, title: String, reason: String): Result {
        CallUploads.notify(ctx, file.name, NotificationCompat.Builder(ctx, CallUploads.CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_notify_error)
            .setContentTitle("Couldn't save “$title”")
            .setContentText(reason)
            .setAutoCancel(true))
        RecordingsFolder.release(ctx, file)
        return Result.failure()
    }

    companion object {
        private const val TAG = "SharedAudioNote"
        private const val MAX_ATTEMPTS = 5
        private const val KEY_PATH = "path"
        private const val KEY_PROJECT = "project"
        private const val KEY_PROJECT_TITLE = "project_title"
        private const val KEY_TITLE = "title"
        private const val KEY_MIME = "mime"

        /** Queue [file] (a local copy) as a note titled [title] in the project [projectId]. */
        fun enqueue(context: Context, file: File, mime: String?, title: String, projectId: String, projectTitle: String) {
            val request = OneTimeWorkRequestBuilder<SharedAudioNoteWorker>()
                .setInputData(workDataOf(
                    KEY_PATH to file.absolutePath, KEY_PROJECT to projectId, KEY_PROJECT_TITLE to projectTitle,
                    KEY_TITLE to title, KEY_MIME to mime
                ))
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 1, TimeUnit.MINUTES)
                .build()
            WorkManager.getInstance(context.applicationContext).enqueue(request)
        }
    }
}
