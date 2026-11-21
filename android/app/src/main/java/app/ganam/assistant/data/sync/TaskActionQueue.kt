package app.ganam.assistant.data.sync

import android.content.Context
import android.util.Log
import app.ganam.assistant.data.network.ApiService
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.UUID

/** What happened to a reminder action the user just took. */
enum class SubmitResult {
    /** The server accepted it. */
    SENT,
    /** The server couldn't be reached; it is saved and will be retried. */
    QUEUED,
    /** The server refused it (e.g. the task no longer exists). */
    REJECTED
}

/**
 * Complete / snooze actions taken on a reminder (phone alarm or watch).
 *
 * Reminders fire when you're away from home, which is exactly when the VPN
 * is down. Before this queue, the action failed with a toast and was lost.
 * Now it is tried right away and, if the server can't be reached, saved to
 * `filesDir/pending_task_actions.json` and sent by [PendingSyncWorker] (with
 * a network constraint and backoff) or as soon as the app sees the server
 * come back online.
 */
object TaskActionQueue {
    private const val TAG = "TaskActionQueue"
    private const val FILE_NAME = "pending_task_actions.json"

    /** Give up on the immediate attempt quickly; the queue retries later. */
    private const val IMMEDIATE_TIMEOUT_MS = 6_000L

    private val fileLock = Any()
    private val flushMutex = Mutex()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    data class Action(
        val id: String,
        val taskId: String,
        /** "status" (payload: {status}), "snooze" (payload: snooze body) or "update" (payload: changed fields). */
        val kind: String,
        val payload: JSONObject,
        val createdAt: Long
    )

    suspend fun complete(context: Context, taskId: String): SubmitResult = setStatus(context, taskId, "completed")

    suspend fun setStatus(context: Context, taskId: String, status: String): SubmitResult =
        submit(context, newAction(taskId, KIND_STATUS, JSONObject().put("status", status)))

    suspend fun snooze(context: Context, taskId: String, payload: JSONObject): SubmitResult =
        submit(context, newAction(taskId, KIND_SNOOZE, payload))

    /**
     * Change some of a task's fields (`PUT /api/tasks/{id}` with only those
     * keys: title, description, priority, triggers, group_id…). Edits made in
     * the reminder editor while offline merge into one queued update.
     */
    suspend fun update(context: Context, taskId: String, fields: JSONObject): SubmitResult =
        submit(context, newAction(taskId, KIND_UPDATE, fields))

    private fun newAction(taskId: String, kind: String, payload: JSONObject) =
        Action(UUID.randomUUID().toString(), taskId, kind, payload, System.currentTimeMillis())

    suspend fun submit(context: Context, action: Action): SubmitResult = withContext(Dispatchers.IO) {
        val app = context.applicationContext
        val code = withTimeoutOrNull(IMMEDIATE_TIMEOUT_MS) { attempt(app, action) } ?: -1
        when {
            code in 200..299 -> SubmitResult.SENT
            isRetryable(code) -> {
                enqueue(app, action)
                PendingSyncWorker.schedule(app)
                SubmitResult.QUEUED
            }
            else -> {
                Log.w(TAG, "Server rejected ${action.kind} for task ${action.taskId}: HTTP $code")
                SubmitResult.REJECTED
            }
        }
    }

    /** Number of actions still waiting for the server. */
    fun pendingCount(context: Context): Int = synchronized(fileLock) { read(context).size }

    /** Fire-and-forget [flush], e.g. when the server comes back online. */
    fun flushAsync(context: Context) {
        val app = context.applicationContext
        if (pendingCount(app) == 0) return
        scope.launch { flush(app) }
    }

    /**
     * Send every queued action, oldest first. Returns how many are still
     * waiting (unreachable / server error).
     */
    suspend fun flush(context: Context): Int = flushMutex.withLock {
        val app = context.applicationContext
        val pending = synchronized(fileLock) { read(app) }
        if (pending.isEmpty()) return@withLock 0
        val done = mutableSetOf<String>()
        for (action in pending) {
            val code = attempt(app, action)
            if (code in 200..299 || !isRetryable(code)) {
                if (code !in 200..299) Log.w(TAG, "Dropping ${action.kind} for ${action.taskId}: HTTP $code")
                done += action.id
            } else {
                // Still unreachable: the rest would fail the same way.
                break
            }
        }
        synchronized(fileLock) {
            // Re-read: new actions may have been queued while we were sending.
            val remaining = read(app).filter { it.id !in done }
            write(app, remaining)
            if (done.isNotEmpty()) Log.i(TAG, "Sent ${done.size} queued reminder action(s), ${remaining.size} left")
            remaining.size
        }
    }

    /** HTTP status of one attempt, or -1 when the server couldn't be reached. */
    private suspend fun attempt(context: Context, action: Action): Int = try {
        val api = ApiService.fromPrefs(context)
        when (action.kind) {
            KIND_STATUS -> api.updateTaskStatusCode(action.taskId, action.payload.optString("status", "completed"))
            KIND_SNOOZE -> api.snoozeTaskCode(action.taskId, action.payload)
            KIND_UPDATE -> api.updateTaskFieldsCode(action.taskId, action.payload)
            else -> 400
        }
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        // IOException (VPN down) or a missing/invalid server URL.
        Log.w(TAG, "Reminder action not delivered: ${e.message}")
        -1
    }

    internal fun isRetryable(code: Int): Boolean = code == -1 || code == 408 || code == 429 || code >= 500

    private fun enqueue(context: Context, action: Action) = synchronized(fileLock) {
        write(context, merge(read(context), action))
    }

    /**
     * Add [action] to the queue. The latest decision about a task wins:
     * completing replaces an earlier queued snooze and vice versa. Field
     * updates merge into one (newer values win) placed after everything else,
     * so they still apply on top of an earlier snooze.
     */
    internal fun merge(queue: List<Action>, action: Action): List<Action> {
        if (action.kind == KIND_UPDATE) {
            val earlier = queue.firstOrNull { it.taskId == action.taskId && it.kind == KIND_UPDATE }
            val payload = JSONObject(earlier?.payload?.toString() ?: "{}")
            action.payload.keys().forEach { payload.put(it, action.payload.get(it)) }
            return queue.filter { it !== earlier } + action.copy(payload = payload)
        }
        return queue.filter { it.taskId != action.taskId || it.kind == KIND_UPDATE } + action
    }

    private fun file(context: Context) = File(context.filesDir, FILE_NAME)

    private fun read(context: Context): List<Action> = try {
        val f = file(context)
        if (!f.exists()) emptyList() else decode(f.readText())
    } catch (e: Exception) {
        Log.e(TAG, "Couldn't read the reminder action queue", e)
        emptyList()
    }

    private fun write(context: Context, actions: List<Action>) {
        val f = file(context)
        if (actions.isEmpty()) {
            f.delete()
            return
        }
        val tmp = File(f.parentFile, "$FILE_NAME.tmp")
        tmp.writeText(encode(actions))
        if (!tmp.renameTo(f)) {
            f.writeText(tmp.readText())
            tmp.delete()
        }
    }

    internal fun encode(actions: List<Action>): String = JSONArray().apply {
        actions.forEach {
            put(JSONObject().apply {
                put("id", it.id)
                put("task_id", it.taskId)
                put("kind", it.kind)
                put("payload", it.payload)
                put("created_at", it.createdAt)
            })
        }
    }.toString()

    internal fun decode(text: String): List<Action> {
        val arr = JSONArray(text)
        return (0 until arr.length()).mapNotNull { i ->
            val o = arr.optJSONObject(i) ?: return@mapNotNull null
            Action(
                id = o.optString("id"),
                taskId = o.optString("task_id"),
                kind = o.optString("kind"),
                payload = o.optJSONObject("payload") ?: JSONObject(),
                createdAt = o.optLong("created_at")
            )
        }.filter { it.id.isNotEmpty() && it.taskId.isNotEmpty() }
    }

    private const val KIND_STATUS = "status"
    private const val KIND_SNOOZE = "snooze"
    private const val KIND_UPDATE = "update"
}
