package io.github.ronganam.dana.data.money

import io.github.ronganam.dana.data.network.ServerConfig
import android.app.Notification
import android.content.Context
import android.service.notification.StatusBarNotification
import android.util.Log
import androidx.core.app.NotificationCompat
import io.github.ronganam.dana.data.notifications.DanaNotificationListener
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Bank sign-in codes for the server's `money` addon. When Dana's server signs
 * in to a bank that texts a one-time code, it pushes `otp_request` with only
 * a request id. [handle] fetches the request (`GET
 * /api/money/code-requests/{id}`) and, while it is pending, **arms** it:
 * which SMS senders to look for, the regex that finds the code, until when
 * (`filesDir/money_code_requests.json`, so it survives the process dying;
 * never a code). It then looks at what is already in the shade, and
 * [onPosted] — called by the notification listener before any capture,
 * redaction or skipping — looks at each new notification, but only while a
 * request is armed: with none armed it reads nothing. A match is POSTed to
 * that request and nowhere else; the code is never logged, stored or shown,
 * and the Inbox copy of the SMS is still redacted to "[code]".
 */
object MoneyCodes {
    private const val TAG = "MoneyCodes"
    private const val FILE_NAME = "money_code_requests.json"

    /**
     * Messages from longer than this before the request was armed belong to an
     * earlier sign-in (an old SMS still in the shade, whose code is spent).
     */
    const val LOOKBACK_MS = 2 * 60_000L
    /** When `expires_at` can't be read: the push's own lifetime. */
    const val DEFAULT_LIFETIME_MS = 240_000L
    /** However far `expires_at` is, a request stays armed at most this long. */
    const val MAX_ARMED_MS = 10 * 60_000L

    /** An armed request; messages before [since] are ignored. Never holds a code. */
    data class Request(val id: String, val senders: List<String>, val regex: String, val since: Long, val expiresAt: Long)

    /** One message as a notification shows it: who sent it, what it says, when (epoch ms). */
    data class Message(val sender: String, val text: String, val at: Long)

    // ---------- pure matching ----------

    private val BIDI = Regex("[‎‏‪-‮⁦-⁩]")

    /** Without direction marks (an RTL phone wraps names and numbers in them), trimmed. */
    fun clean(s: String): String = s.replace(BIDI, "").trim()

    /** [pattern] as a regex, or null when it doesn't compile here (Java syntax). */
    fun compile(pattern: String): Regex? = if (pattern.isBlank()) null else try {
        Regex(pattern)
    } catch (e: Exception) {
        null
    }

    fun senderMatches(sender: String, req: Request): Boolean {
        val s = clean(sender)
        return s.isNotEmpty() && req.senders.any { clean(it).equals(s, ignoreCase = true) }
    }

    /**
     * The code in [text] from [sender] for [req], else null: the regex's group
     * 1, else the digits of the whole match. Null when the sender isn't one of
     * the request's, nothing matches, the request expired or its regex doesn't
     * compile.
     */
    fun match(sender: String, text: String, req: Request, now: Long): String? {
        if (now >= req.expiresAt || !senderMatches(sender, req)) return null
        val m = compile(req.regex)?.find(clean(text)) ?: return null
        val code = (if (m.groups.size > 1) m.groups[1]?.value else null) ?: m.value.filter(Char::isDigit)
        return code.trim().takeIf { it.isNotEmpty() }
    }

    /** The newest message from [Request.since] on that carries a code for [req], with that code. */
    fun find(messages: List<Message>, req: Request, now: Long): Pair<Message, String>? =
        messages.filter { it.at >= req.since }.sortedByDescending { it.at }
            .firstNotNullOfOrNull { m -> match(m.sender, m.text, req, now)?.let { m to it } }

    /**
     * What a notification says, as messages. A MessagingStyle notification:
     * its messages, each with its own sender (the title when it has none:
     * the conversation) and time. Otherwise the title is the sender (Google
     * Messages titles an SMS with it) of the text and of the big text.
     */
    fun messages(title: String, text: String, bigText: String, style: List<Message>?, postedAt: Long): List<Message> =
        if (!style.isNullOrEmpty()) {
            style.map { m -> m.copy(sender = m.sender.ifBlank { title }, at = if (m.at > 0) m.at else postedAt) }
        } else {
            listOf(text, bigText).filter { it.isNotBlank() }.distinct().map { Message(title, it, postedAt) }
        }

    /** `expires_at` (ISO, with or without an offset; none = UTC) → epoch ms. */
    fun parseInstant(s: String): Long? {
        if (s.isBlank() || s == "null") return null
        return try {
            java.time.OffsetDateTime.parse(s).toInstant().toEpochMilli()
        } catch (e: Exception) {
            try {
                java.time.LocalDateTime.parse(s).toInstant(java.time.ZoneOffset.UTC).toEpochMilli()
            } catch (e2: Exception) {
                null
            }
        }
    }

    /**
     * The server's request → what to arm, or null when there is nothing to
     * wait for: not pending, no senders, a regex that doesn't compile, expired.
     */
    fun parse(id: String, o: JSONObject, now: Long): Request? {
        if (o.optString("status") != "pending") return null
        val senders = o.optJSONArray("senders")?.let { a ->
            (0 until a.length()).map { a.optString(it) }.filter { clean(it).isNotEmpty() }
        }.orEmpty()
        val regex = o.optString("code_regex")
        if (senders.isEmpty() || compile(regex) == null) return null
        val expires = parseInstant(o.optString("expires_at")) ?: (now + DEFAULT_LIFETIME_MS)
        val expiresAt = minOf(expires, now + MAX_ARMED_MS)
        if (expiresAt <= now) return null
        return Request(id, senders, regex, now - LOOKBACK_MS, expiresAt)
    }

    // ---------- armed requests ----------

    private val lock = Any()
    /** Null until read from disk (once per process). */
    @Volatile
    private var armed: List<Request>? = null
    /** Requests with a POST on its way (a second match waits for it). */
    private val sending = HashSet<String>()
    /** Messages whose code was already handed over (shade key + time + sender; never the text). In memory only. */
    private val used = LinkedHashSet<String>()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val ID = Regex("[A-Za-z0-9_-]{8,64}")

    /** The `otp_request` push: fetch the request, arm it while pending, then check the shade. */
    suspend fun handle(context: Context, rid: String) {
        if (!rid.matches(ID)) return
        val app = context.applicationContext
        val o = try {
            ServerConfig.load(app).api().money.codeRequest(rid)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't fetch code request $rid: ${e.message}")
            return
        }
        val now = System.currentTimeMillis()
        val req = parse(rid, o, now)
        if (req == null) {
            val why = if (o.optString("status") != "pending") "status ${o.optString("status")}" else "no senders, a bad regex or expired"
            Log.i(TAG, "Not waiting for a code for $rid ($why)")
            return
        }
        synchronized(lock) { save(app, live(app, now).filter { it.id != rid } + req) }
        Log.i(TAG, "Waiting for a code from ${req.senders} for $rid until ${java.time.Instant.ofEpochMilli(req.expiresAt)}")
        if (!DanaNotificationListener.isConnected) Log.w(TAG, "Notification access is off: the code's SMS can't be seen")
        // The SMS may already be in the shade.
        scanShade(app)
    }

    /** Look at what is in the shade now (after arming, and when the listener reconnects). */
    fun scanShade(context: Context) {
        val app = context.applicationContext
        if (live(app, System.currentTimeMillis()).isEmpty()) return
        DanaNotificationListener.shade().forEach { onPosted(app, it) }
    }

    /**
     * A notification was posted: if a request is armed and this is its SMS,
     * hand over the code. Reads nothing of [sbn] while no request is armed.
     */
    fun onPosted(context: Context, sbn: StatusBarNotification) {
        try {
            val now = System.currentTimeMillis()
            val reqs = live(context, now)
            if (reqs.isEmpty()) return
            val key = sbn.key.orEmpty()
            val spent = synchronized(used) { used.toSet() }
            val fresh = messagesOf(sbn).filter { fingerprint(key, it) !in spent }
            if (fresh.isEmpty()) return
            for (req in reqs) {
                val (message, code) = find(fresh, req, now) ?: continue
                if (submit(context.applicationContext, req, code)) {
                    synchronized(used) {
                        used += fingerprint(key, message)
                        if (used.size > 100) used.remove(used.first())
                    }
                }
            }
        } catch (e: Exception) {
            // Never in the way of the normal capture. (The message can't carry a code: none is put in an exception.)
            Log.w(TAG, "Couldn't check a notification for a sign-in code (${e.javaClass.simpleName})")
        }
    }

    private fun fingerprint(key: String, m: Message) = "$key\u0000${m.at}\u0000${clean(m.sender)}"

    private fun messagesOf(sbn: StatusBarNotification): List<Message> {
        val n = sbn.notification ?: return emptyList()
        val extras = n.extras ?: return emptyList()
        fun cs(k: String) = extras.getCharSequence(k)?.toString().orEmpty()
        val style = runCatching { NotificationCompat.MessagingStyle.extractMessagingStyleFromNotification(n) }.getOrNull()
            ?.messages?.map { Message(it.person?.name?.toString().orEmpty(), it.text?.toString().orEmpty(), it.timestamp) }
        return messages(cs(Notification.EXTRA_TITLE), cs(Notification.EXTRA_TEXT), cs(Notification.EXTRA_BIG_TEXT), style, sbn.postTime)
    }

    /**
     * POST [code] to [req], retrying an unreachable server (or a 5xx) with
     * backoff until the request expires. Any other answer (200 taken, 400
     * `bad_code`, 404, 409 `not_pending`) disarms it. False when a POST for
     * [req] is already on its way.
     */
    private fun submit(context: Context, req: Request, code: String): Boolean {
        synchronized(sending) { if (!sending.add(req.id)) return false }
        scope.launch {
            try {
                var wait = 2_000L
                while (true) {
                    val status = try {
                        ServerConfig.load(context).api().money.postCode(req.id, code)
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        -1
                    }
                    if (status in 200..499) {
                        Log.i(TAG, if (status in 200..299) "Sign-in code handed over for ${req.id}" else "Code request ${req.id} answered $status; no longer waiting")
                        disarm(context, req.id)
                        return@launch
                    }
                    if (System.currentTimeMillis() + wait >= req.expiresAt) {
                        Log.w(TAG, "Couldn't reach the server with the code for ${req.id} before it expired")
                        disarm(context, req.id)
                        return@launch
                    }
                    delay(wait)
                    wait = (wait * 2).coerceAtMost(30_000L)
                }
            } finally {
                synchronized(sending) { sending.remove(req.id) }
            }
        }
        return true
    }

    private fun disarm(context: Context, id: String) = synchronized(lock) {
        save(context, live(context, System.currentTimeMillis()).filter { it.id != id })
    }

    /** Armed, unexpired requests (expired ones are dropped from disk too). */
    private fun live(context: Context, now: Long): List<Request> {
        val all = armed ?: synchronized(lock) { armed ?: load(context).also { armed = it } }
        if (all.isEmpty()) return all
        val alive = all.filter { now < it.expiresAt }
        if (alive.size != all.size) synchronized(lock) { save(context, alive) }
        return alive
    }

    private fun file(context: Context) = File(context.filesDir, FILE_NAME)

    /** Requests kept on disk; one whose regex doesn't compile is not armed. */
    private fun load(context: Context): List<Request> = try {
        val f = file(context)
        if (!f.exists()) emptyList() else decode(f.readText()).filter { compile(it.regex) != null }
    } catch (e: Exception) {
        Log.w(TAG, "Armed code requests unreadable; dropping them")
        emptyList()
    }

    private fun save(context: Context, list: List<Request>) {
        armed = list
        val f = file(context)
        try {
            if (list.isEmpty()) {
                f.delete()
                return
            }
            val tmp = File(f.parentFile, "$FILE_NAME.tmp")
            tmp.writeText(encode(list))
            if (!tmp.renameTo(f)) {
                f.writeText(encode(list))
                tmp.delete()
            }
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't save armed code requests", e)
        }
    }

    fun encode(list: List<Request>): String = JSONArray().apply {
        list.forEach { r ->
            put(JSONObject().put("id", r.id).put("senders", JSONArray(r.senders)).put("code_regex", r.regex)
                .put("since", r.since).put("expires_at", r.expiresAt))
        }
    }.toString()

    fun decode(text: String): List<Request> {
        val a = JSONArray(text)
        return (0 until a.length()).mapNotNull { i ->
            val o = a.optJSONObject(i) ?: return@mapNotNull null
            val senders = o.optJSONArray("senders")?.let { s -> (0 until s.length()).map { s.optString(it) } }.orEmpty()
            val id = o.optString("id")
            if (id.isEmpty() || senders.isEmpty()) null
            else Request(id, senders, o.optString("code_regex"), o.optLong("since"), o.optLong("expires_at"))
        }
    }
}
