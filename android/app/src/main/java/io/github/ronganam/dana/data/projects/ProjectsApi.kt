package io.github.ronganam.dana.data.projects

import android.content.Context
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.ApiService
import io.github.ronganam.dana.data.network.HttpClients
import io.github.ronganam.dana.data.network.HttpResult
import io.github.ronganam.dana.data.network.Me
import io.github.ronganam.dana.data.network.OptString
import kotlinx.serialization.KSerializer
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody
import okio.BufferedSink
import okio.source
import java.io.File
import java.io.InputStream
import java.net.URLEncoder

/**
 * The server refused a Projects request: HTTP [code], its `error` (`read_only`, `owner_only`,
 * `invalid`, `too_large`, `not_connected`…) and a message to show as is.
 */
class ProjectsApiError(val code: Int, val error: String, override val message: String) : Exception(message)

@Serializable
private data class ErrorWire(val error: String = "", val message: OptString = null)

@Serializable
private data class CreatedWire(val id: String = "")

@Serializable
private data class EntryAnswer(val entry: JsonObject? = null)

@Serializable
private data class FileAnswer(val file: JsonObject? = null)

/**
 * The `/api/projects*`, `/api/sections*`, `/api/entries*` routes (server
 * addons/knowledge/api.py). Non-2xx throws [ProjectsApiError], an
 * unreachable server `IOException`. A 403 `not_connected` forgets the add-on.
 */
class ProjectsApi(private val context: Context, private val api: () -> ApiService) {

    suspend fun projects(): List<Project> = ProjectsJson.projects(raw("GET", "/api/projects"))

    suspend fun items(projectId: String): ProjectItems = ProjectsJson.items(raw("GET", "/api/projects/${enc(projectId)}/items"))

    /** Creates a project; returns its id. */
    suspend fun createProject(changes: ProjectChanges): String =
        decode(CreatedWire.serializer(), raw("POST", "/api/projects", body(ProjectChanges.serializer(), changes))).id
    suspend fun updateProject(id: String, changes: ProjectChanges) { raw("PUT", "/api/projects/${enc(id)}", body(ProjectChanges.serializer(), changes)) }
    suspend fun deleteProject(id: String) { raw("DELETE", "/api/projects/${enc(id)}") }

    suspend fun createSection(projectId: String, changes: SectionChanges): String =
        decode(CreatedWire.serializer(), raw("POST", "/api/projects/${enc(projectId)}/sections", body(SectionChanges.serializer(), changes))).id
    suspend fun updateSection(id: String, changes: SectionChanges) { raw("PUT", "/api/sections/${enc(id)}", body(SectionChanges.serializer(), changes)) }
    suspend fun deleteSection(id: String) { raw("DELETE", "/api/sections/${enc(id)}") }

    /** Creates an entry; returns the new entry. */
    suspend fun createEntry(changes: EntryChanges): Entry? =
        decode(EntryAnswer.serializer(), raw("POST", "/api/entries", body(EntryChanges.serializer(), changes))).entry?.let(ProjectsJson::entry)
    /** Partial update; returns the entry as the server now has it. */
    suspend fun updateEntry(id: String, changes: EntryChanges): Entry? =
        decode(EntryAnswer.serializer(), raw("PUT", "/api/entries/${enc(id)}", body(EntryChanges.serializer(), changes))).entry?.let(ProjectsJson::entry)
    suspend fun deleteEntry(id: String) { raw("DELETE", "/api/entries/${enc(id)}") }

    suspend fun deleteFile(file: EntryFile) { raw("DELETE", file.url) }

    /**
     * `POST /api/entries/{id}/files`: multipart `file` (streamed from [open], [size] bytes or -1)
     * and `note`. The name is percent-encoded (the server unquotes it), so Hebrew names arrive intact.
     */
    suspend fun uploadFile(entryId: String, name: String, mime: String, size: Long, note: String, open: () -> InputStream): EntryFile? {
        val type = mime.ifBlank { "application/octet-stream" }.toMediaTypeOrNull()
        val stream = object : RequestBody() {
            override fun contentType() = type
            override fun contentLength() = size
            override fun writeTo(sink: BufferedSink) {
                open().use { input -> input.source().use { sink.writeAll(it) } }
            }
        }
        val form = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("file", android.net.Uri.encode(name), stream)
            .apply { if (note.isNotBlank()) addFormDataPart("note", note.trim()) }
            .build()
        val http = api().http
        val r = http.result(http.request("/api/entries/${enc(entryId)}/files") { post(form) }, HttpClients.upload)
        if (r.code == 413) throw ProjectsApiError(r.code, "too_large", error(r.body).message ?: "The file is too big (60 MB at most).")
        check(r)
        return decode(FileAnswer.serializer(), r.body).file?.let(ProjectsJson::file)
    }

    /** Downloads a file's `url` into [dest]. */
    suspend fun download(file: EntryFile, dest: File) {
        val http = api().http
        val code = http.download(http.request(file.url), dest)
        if (code !in 200..299) throw ProjectsApiError(code, "", if (code == 404) "That file is gone." else "Couldn't download the file (HTTP $code).")
    }

    private fun <T> body(serializer: KSerializer<T>, value: T): JsonElement = ApiJson.encodeToJsonElement(serializer, value)

    private fun <T> decode(serializer: KSerializer<T>, text: String): T = ApiJson.decodeFromString(serializer, text.ifBlank { "{}" })

    private fun error(text: String): ErrorWire = runCatching { decode(ErrorWire.serializer(), text) }.getOrDefault(ErrorWire())

    private suspend fun raw(method: String, path: String, body: JsonElement? = null): String {
        val r = api().http.call(method, path, body)
        check(r)
        return r.body
    }

    private fun check(r: HttpResult) {
        if (r.ok) return
        val code = r.code
        val e = error(r.body)
        val error = e.error
        if (code == 403 && error == "not_connected") Me.drop(context, ADDON)
        val message = e.message ?: when {
            code == 403 && error == "read_only" -> "This project is shared with you read-only."
            code == 403 && error == "owner_only" -> "Only the owner of the project can change that."
            code == 403 -> "Projects aren't connected for you."
            code == 404 -> "That is gone."
            error.isNotBlank() && error.length < 80 -> error
            else -> "The server answered HTTP $code."
        }
        throw ProjectsApiError(code, error, message)
    }

    private fun enc(s: String) = URLEncoder.encode(s, "UTF-8")

    companion object { const val ADDON = "knowledge" }
}
