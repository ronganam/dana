package io.github.ronganam.dana.data.money

import android.app.Notification
import android.content.Context
import android.service.notification.StatusBarNotification
import android.util.Log
import io.github.ronganam.dana.data.network.ApiService
import io.github.ronganam.dana.data.network.Me
import io.github.ronganam.dana.data.notifications.NotificationExtractor
import io.github.ronganam.dana.data.notifications.Redactor
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.io.File

/**
 * Notifications from bank / card apps for the server's `money` addon
 * (`POST /api/money/signals`). While the user has `money` in `/api/me`
 * (cached by [Me]), a notification from one of [PACKAGES] goes here, codes
 * hidden by [Redactor], and **never** to the Inbox — whatever "Skip banking &
 * authenticator apps" says (that switch governs the Inbox only). Without
 * `money` these apps behave as before.
 *
 * Durable like the Inbox outbox (`filesDir/money_signals.json`,
 * write-then-rename, capped); [NotificationSyncWorker][io.github.ronganam.dana.data.notifications.NotificationSyncWorker]
 * drains it in the same debounced, retried runs. A 403 `not_connected`
 * forgets the addon (until the next `/api/me` says otherwise) and the queue.
 */
object MoneySignals {
    private const val TAG = "MoneySignals"
    const val ADDON = "money"
    private const val FILE_NAME = "money_signals.json"
    private const val MAX_ITEMS = 500
    private const val BATCH = 100

    /** Bank / card apps whose notifications are money signals (Cal, Hapoalim, Max, Isracard). */
    val PACKAGES = setOf("com.onoapps.cal4u", "com.ideomobile.hapoalim", "com.max.android", "com.isracard.isracardapp")

    /** True when [pkg]'s notifications go to money instead of the Inbox (the user has the addon). */
    fun routes(context: Context, pkg: String?): Boolean =
        pkg != null && pkg in PACKAGES && Me.cached(context)?.has(ADDON) == true

    /** The signal for [sbn], or null (ongoing, a group summary, empty …). */
    fun item(context: Context, sbn: StatusBarNotification): JsonObject? {
        if (!NotificationExtractor.isCapturable(context, sbn)) return null
        val extras = sbn.notification?.extras ?: return null
        fun cs(k: String) = extras.getCharSequence(k)?.toString()?.trim().orEmpty()
        val text = cs(Notification.EXTRA_BIG_TEXT).ifEmpty { cs(Notification.EXTRA_TEXT) }.ifEmpty {
            extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)?.joinToString("\n") { it.toString() }.orEmpty()
        }
        val pkg = sbn.packageName ?: return null
        return build(sbn.key ?: return null, pkg, NotificationExtractor.appName(context, pkg, sbn), cs(Notification.EXTRA_TITLE), text, sbn.postTime)
    }

    /** The payload of one signal, codes hidden; null when it says nothing. */
    fun build(key: String, pkg: String, appName: String, title: String, text: String, postedAt: Long): JsonObject? {
        if (title.isBlank() && text.isBlank()) return null
        return buildJsonObject {
            put("key", key)
            put("package", pkg)
            put("app_name", appName)
            put("title", Redactor.redact(title).take(500))
            put("text", Redactor.redact(text).take(4000))
            put("posted_at", postedAt)
        }
    }

    // ---------- the queue ----------

    private val lock = Any()
    private var seq = System.currentTimeMillis()

    fun add(context: Context, item: JsonObject) = synchronized(lock) {
        val list = read(context).toMutableList()
        list += buildJsonObject {
            put("id", ++seq)
            put("data", item)
        }
        val kept = if (list.size > MAX_ITEMS) {
            Log.w(TAG, "Over $MAX_ITEMS queued; dropping the oldest")
            list.subList(list.size - MAX_ITEMS, list.size)
        } else list
        write(context, JsonArray(kept))
    }

    fun size(context: Context): Int = synchronized(lock) { read(context).size }

    /**
     * Send what is queued. True when nothing is left to retry (sent, refused
     * or the user lacks the addon); false = unreachable or 5xx, try again later.
     */
    suspend fun drain(context: Context, api: ApiService): Boolean {
        repeat(10) {
            val (ids, batch) = peek(context)
            if (ids.isEmpty()) return true
            val (code, body) = try {
                api.money.postSignals(batch)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Log.w(TAG, "Couldn't send ${ids.size} money signal(s): ${e.message}")
                return false
            }
            when {
                code in 200..299 -> {
                    remove(context, ids)
                    Log.i(TAG, "Sent ${ids.size} money signal(s): ${body.intValue("accepted")} accepted")
                }
                code == 403 && body.string("error") == "not_connected" -> {
                    // Not (or no longer) connected: stop until /api/me says otherwise.
                    Me.drop(context, ADDON)
                    synchronized(lock) { write(context, EmptyJsonArray) }
                    Log.i(TAG, "No money addon; dropped the queued signals")
                    return true
                }
                code in 400..499 -> {
                    remove(context, ids)
                    Log.w(TAG, "Server refused ${ids.size} money signal(s) (HTTP $code); dropped them")
                }
                else -> {
                    Log.w(TAG, "Money signals: HTTP $code; will retry")
                    return false
                }
            }
        }
        return true
    }

    private fun peek(context: Context): Pair<List<Long>, JsonArray> = synchronized(lock) {
        val arr = read(context)
        val ids = mutableListOf<Long>()
        val out = buildJsonArray {
            for (o in arr.objects().take(BATCH)) {
                ids += o.longValue("id")
                o.obj("data")?.let { add(it) }
            }
        }
        ids to out
    }

    private fun remove(context: Context, ids: Collection<Long>) = synchronized(lock) {
        val drop = ids.toHashSet()
        val kept = read(context).objects().filter { it.longValue("id") !in drop }
        write(context, JsonArray(kept))
    }

    private fun file(context: Context) = File(context.filesDir, FILE_NAME)

    private fun read(context: Context): JsonArray = try {
        val f = file(context)
        if (f.exists()) jsonArrayOf(f.readText()) else EmptyJsonArray
    } catch (e: Exception) {
        Log.e(TAG, "Queue unreadable; starting over", e)
        EmptyJsonArray
    }

    private fun write(context: Context, arr: JsonArray) {
        val f = file(context)
        if (arr.isEmpty()) {
            f.delete()
            return
        }
        val tmp = File(f.parentFile, "$FILE_NAME.tmp")
        tmp.writeText(arr.toString())
        if (!tmp.renameTo(f)) {
            f.writeText(arr.toString())
            tmp.delete()
        }
    }
}
