package io.github.ronganam.dana.data.sync

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import android.util.Log
import androidx.core.content.edit
import androidx.core.net.toUri
import io.github.ronganam.dana.data.network.ServerConfig
import java.io.File

/**
 * The folder the phone's call recorder writes to, picked by the user with the
 * Storage Access Framework (Settings → Call recordings → "Recordings folder").
 * Dana keeps a persisted read grant on that tree and nothing else: no storage
 * permission. Files are read through the tree's document URIs; one being
 * handled is copied into `filesDir/call_recordings/` so the upload pipeline
 * (a `File`) can retry it after the app is killed.
 */
object RecordingsFolder {
    private const val TAG = "RecordingsFolder"
    private const val KEY_TREE = "call_recordings_tree"
    private const val COPIES = "call_recordings"
    private const val EXTERNAL_STORAGE = "com.android.externalstorage.documents"
    /** Where Samsung's built-in recorder saves calls: offered first in the picker when it exists. */
    private const val SAMSUNG_DIR = "Recordings/Call"

    /** The picked tree, when one was picked. */
    fun tree(context: Context): Uri? =
        ServerConfig.prefs(context).getString(KEY_TREE, null)?.toUri()

    /** A folder is picked and Android still lets Dana read it. */
    fun readable(context: Context): Boolean {
        val tree = tree(context) ?: return false
        return context.contentResolver.persistedUriPermissions.any { it.uri == tree && it.isReadPermission }
    }

    /** Keep [tree] (from ACTION_OPEN_DOCUMENT_TREE): persist its read grant and drop the previous folder's. */
    fun set(context: Context, tree: Uri) {
        val resolver = context.contentResolver
        val old = tree(context)
        resolver.takePersistableUriPermission(tree, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        if (old != null && old != tree) {
            runCatching { resolver.releasePersistableUriPermission(old, Intent.FLAG_GRANT_READ_URI_PERMISSION) }
        }
        ServerConfig.prefs(context).edit { putString(KEY_TREE, tree.toString()) }
    }

    /** The picker's starting place: Samsung's recordings folder when the phone has one, else the picker's own default. */
    fun suggestedStart(): Uri? {
        @Suppress("DEPRECATION")
        val root = android.os.Environment.getExternalStorageDirectory()
        if (!File(root, SAMSUNG_DIR).isDirectory) return null
        return DocumentsContract.buildDocumentUri(EXTERNAL_STORAGE, "primary:$SAMSUNG_DIR")
    }

    /** The folder's name for Settings ("Call", "call_rec"…), or null when it can't be read. */
    fun displayName(context: Context): String? {
        val tree = tree(context) ?: return null
        val doc = DocumentsContract.buildDocumentUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree))
        return runCatching {
            context.contentResolver.query(doc, arrayOf(DocumentsContract.Document.COLUMN_DISPLAY_NAME), null, null, null)
                ?.use { c -> if (c.moveToFirst()) c.getString(0) else null }
        }.getOrNull() ?: DocumentsContract.getTreeDocumentId(tree).substringAfter(':').ifBlank { null }
    }

    /** The files directly in the folder (empty when none is picked or it can't be read). */
    fun list(context: Context): List<RecordingDoc> {
        val tree = tree(context) ?: return emptyList()
        val children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree))
        val columns = arrayOf(
            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE,
            DocumentsContract.Document.COLUMN_LAST_MODIFIED,
            DocumentsContract.Document.COLUMN_SIZE
        )
        return try {
            context.contentResolver.query(children, columns, null, null, null)?.use { c ->
                buildList {
                    while (c.moveToNext()) {
                        val mime = c.getString(2)
                        if (mime == DocumentsContract.Document.MIME_TYPE_DIR) continue
                        val uri = DocumentsContract.buildDocumentUriUsingTree(tree, c.getString(0))
                        add(RecordingDoc(uri.toString(), c.getString(1) ?: continue, mime, c.getLong(3), c.getLong(4)))
                    }
                }
            }.orEmpty()
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't list the recordings folder", e)
            emptyList()
        }
    }

    /** A local copy of [doc] to upload (reused when an earlier copy is complete), or null when it can't be read. */
    fun copy(context: Context, doc: RecordingDoc): File? = copy(context, doc.uri.toUri(), doc.name, doc.size)

    /** A local copy of any readable [uri] named [name] ([size] -1 when unknown). */
    fun copy(context: Context, uri: Uri, name: String, size: Long = -1): File? {
        val dir = File(context.filesDir, COPIES).apply { mkdirs() }
        // Never let a provider-supplied name leave the folder.
        val out = File(dir, File(name).name.ifBlank { "recording.m4a" })
        if (out.exists() && size > 0 && out.length() == size) return out
        return try {
            val tmp = File(dir, "${out.name}.part")
            val copied = context.contentResolver.openInputStream(uri)?.use { input ->
                tmp.outputStream().use { input.copyTo(it) }
            } ?: return null
            if (copied <= 0 || !tmp.renameTo(out)) { tmp.delete(); null } else out
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't copy $name", e)
            null
        }
    }

    /** Delete [file] when it is one of the local copies (after its upload, or when skipped). */
    fun release(context: Context, file: File) {
        val dir = File(context.filesDir, COPIES)
        if (file.parentFile?.canonicalPath == dir.canonicalPath) file.delete()
    }

    /** Drop copies nobody needs any more: older than two weeks and not waiting for an upload. */
    fun prune(context: Context, nowMs: Long) {
        File(context.filesDir, COPIES).listFiles().orEmpty().forEach { f ->
            val waiting = CallUploadStore.state(context, f.name) == CallUploadStore.State.QUEUED
            if (!waiting && nowMs - f.lastModified() > 14L * 24 * 3600 * 1000) f.delete()
        }
    }
}
