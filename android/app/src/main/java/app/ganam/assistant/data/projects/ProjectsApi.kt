package app.ganam.assistant.data.projects

import android.content.Context
import app.ganam.assistant.data.network.ApiService
import app.ganam.assistant.data.network.Me
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.InputStream
import java.net.URLEncoder

/**
 * The server refused a Projects request: HTTP [code], its `error` (`read_only`, `owner_only`,
 * `invalid`, `too_large`, `not_connected`…) and a message to show as is.
 */
class ProjectsApiError(val code: Int, val error: String, override val message: String) : Exception(message)

/**
 * The `/api/projects*`, `/api/sections*`, `/api/entries*` routes (server
 * integrations/knowledge/api.py). Non-2xx throws [ProjectsApiError], an
 * unreachable server `IOException`. A 403 `not_connected` forgets the kind.
 */
class ProjectsApi(private val context: Context, private val api: () -> ApiService) {

    suspend fun projects(): List<Project> = ProjectsJson.projects(JSONArray(raw("GET", "/api/projects", null).ifBlank { "[]" }))

    suspend fun items(projectId: String): ProjectItems = ProjectsJson.items(call("GET", "/api/projects/${enc(projectId)}/items"))

    /** Creates a project; returns its id. */
    suspend fun createProject(body: JSONObject): String = call("POST", "/api/projects", body).optString("id")
    suspend fun updateProject(id: String, body: JSONObject) { call("PUT", "/api/projects/${enc(id)}", body) }
    suspend fun deleteProject(id: String) { call("DELETE", "/api/projects/${enc(id)}") }

    suspend fun createSection(projectId: String, body: JSONObject): String =
        call("POST", "/api/projects/${enc(projectId)}/sections", body).optString("id")
    suspend fun updateSection(id: String, body: JSONObject) { call("PUT", "/api/sections/${enc(id)}", body) }
    suspend fun deleteSection(id: String) { call("DELETE", "/api/sections/${enc(id)}") }

    /** Creates an entry; returns the new entry. */
    suspend fun createEntry(body: JSONObject): Entry? =
        call("POST", "/api/entries", body).optJSONObject("entry")?.let(ProjectsJson::entry)
    /** Partial update; returns the entry as the server now has it. */
    suspend fun updateEntry(id: String, changes: JSONObject): Entry? =
        call("PUT", "/api/entries/${enc(id)}", changes).optJSONObject("entry")?.let(ProjectsJson::entry)
    suspend fun deleteEntry(id: String) { call("DELETE", "/api/entries/${enc(id)}") }

    suspend fun deleteFile(file: EntryFile) { call("DELETE", file.url) }

    suspend fun uploadFile(entryId: String, name: String, mime: String, size: Long, note: String, open: () -> InputStream): EntryFile? {
        val (code, text) = api().uploadEntryFile(entryId, name, mime, size, note, open)
        val json = runCatching { JSONObject(text) }.getOrDefault(JSONObject())
        if (code == 413) throw ProjectsApiError(code, "too_large", json.optString("message").ifBlank { "The file is too big (60 MB at most)." })
        check(code, json)
        return json.optJSONObject("file")?.let(ProjectsJson::file)
    }

    /** Downloads a file's `url` into [dest]. */
    suspend fun download(file: EntryFile, dest: File) {
        val code = api().downloadTo(file.url, dest)
        if (code !in 200..299) throw ProjectsApiError(code, "", if (code == 404) "That file is gone." else "Couldn't download the file (HTTP $code).")
    }

    private suspend fun raw(method: String, path: String, body: JSONObject?): String {
        val (code, text) = api().jsonCall(method, path, body)
        if (code !in 200..299) check(code, runCatching { JSONObject(text) }.getOrDefault(JSONObject()))
        return text
    }

    private suspend fun call(method: String, path: String, body: JSONObject? = null): JSONObject =
        runCatching { JSONObject(raw(method, path, body)) }.getOrDefault(JSONObject())

    private fun check(code: Int, json: JSONObject) {
        if (code in 200..299) return
        val error = json.optString("error")
        if (code == 403 && error == "not_connected") Me.drop(context, KIND)
        val message = json.optString("message").ifBlank {
            when {
                code == 403 && error == "read_only" -> "This project is shared with you read-only."
                code == 403 && error == "owner_only" -> "Only the owner of the project can change that."
                code == 403 -> "Projects aren't connected for you."
                code == 404 -> "That is gone."
                error.isNotBlank() && error.length < 80 -> error
                else -> "The server answered HTTP $code."
            }
        }
        throw ProjectsApiError(code, error, message)
    }

    private fun enc(s: String) = URLEncoder.encode(s, "UTF-8")

    companion object { const val KIND = "knowledge" }
}
