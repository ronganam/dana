package io.github.ronganam.dana.data.notifications

import android.app.Notification
import android.content.Context
import android.content.Intent
import android.provider.Settings
import android.service.notification.StatusBarNotification
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * What the phone sends to Dana's notification Inbox, and what it never sends.
 *
 * - Off switch + a deny list of packages (Settings › Notifications).
 * - Banking / authenticator / password-manager apps are skipped by default
 *   ([skipSensitive]), by package and by app name.
 * - One-time codes are replaced by "[code]" before anything leaves the phone.
 * - Ongoing, foreground-service, progress and group-summary notifications,
 *   and Dana's own, are ignored.
 */
object NotificationSettings {
    private const val PREFS = "DanaPrefs"
    private const val KEY_ENABLED = "notif_capture_enabled"
    private const val KEY_DENIED = "notif_denied_packages"
    private const val KEY_SKIP_SENSITIVE = "notif_skip_sensitive"

    /** Packages skipped while [skipSensitive] is on (plus any app whose name matches [SENSITIVE_NAME]). */
    val SENSITIVE_PACKAGES = setOf(
        "com.google.android.apps.authenticator2", "com.azure.authenticator", "com.authy.authy",
        "com.lastpass.lpandroid", "com.x8bit.bitwarden", "com.onepassword.android", "com.agilebits.onepassword",
        "com.ideomobile.hapoalim", "com.leumi.leumiwallet", "com.leumi.leumidigital", "com.ideomobile.discount",
        "com.mizrahitefahot.nh", "com.unionbank.app", "com.fibi.nativeapp", "com.onoapps.cal4u",
        "com.max.android", "com.isracard.isracardapp", "com.ideomobile.mercantile", "com.ideomobile.leumicard"
    )
    private val SENSITIVE_NAME = Regex("(?i)(bank|בנק|authenticator|password|bitwarden|1password|lastpass)")

    fun enabled(context: Context) = prefs(context).getBoolean(KEY_ENABLED, true)
    fun setEnabled(context: Context, on: Boolean) = prefs(context).edit().putBoolean(KEY_ENABLED, on).apply()

    fun skipSensitive(context: Context) = prefs(context).getBoolean(KEY_SKIP_SENSITIVE, true)
    fun setSkipSensitive(context: Context, on: Boolean) = prefs(context).edit().putBoolean(KEY_SKIP_SENSITIVE, on).apply()

    fun deniedPackages(context: Context): Set<String> = prefs(context).getStringSet(KEY_DENIED, emptySet())?.toSet() ?: emptySet()
    fun deny(context: Context, pkg: String) = prefs(context).edit().putStringSet(KEY_DENIED, deniedPackages(context) + pkg).apply()
    fun allow(context: Context, pkg: String) = prefs(context).edit().putStringSet(KEY_DENIED, deniedPackages(context) - pkg).apply()

    fun isDenied(context: Context, pkg: String, appName: String): Boolean {
        if (pkg in deniedPackages(context)) return true
        if (skipSensitive(context) && (pkg in SENSITIVE_PACKAGES || SENSITIVE_NAME.containsMatchIn(appName))) return true
        return false
    }

    /** Has the user granted notification access to Dana? */
    fun accessGranted(context: Context): Boolean =
        NotificationManagerCompat.getEnabledListenerPackages(context).contains(context.packageName)

    fun openAccessSettings(context: Context) {
        try {
            context.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (e: Exception) {
            Log.w("NotificationSettings", "Can't open notification access settings", e)
        }
    }

    private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}

/** Hides one-time codes (the server does the same as a safety net). */
object Redactor {
    private val context = Regex("(?i)(code|otp|password|passcode|verification|verify|pin|קוד|סיסמ|אימות)")
    private val number = Regex("(?<!\\d)(\\d[\\d\\- ]{2,8}\\d)(?!\\d)")

    fun redact(text: String): String {
        if (text.isEmpty() || !context.containsMatchIn(text)) return text
        return number.replace(text) { m -> if (m.value.count(Char::isDigit) >= 4) "[code]" else m.value }
    }
}

/** StatusBarNotification → the JSON the server's `posted` list expects (null = skip it). */
object NotificationExtractor {
    private val SKIP_CATEGORIES = setOf(
        Notification.CATEGORY_TRANSPORT, Notification.CATEGORY_PROGRESS, Notification.CATEGORY_SERVICE,
        Notification.CATEGORY_CALL, Notification.CATEGORY_NAVIGATION, Notification.CATEGORY_STOPWATCH
    )

    fun isCapturable(context: Context, sbn: StatusBarNotification): Boolean {
        val pkg = sbn.packageName ?: return false
        if (pkg.startsWith("io.github.ronganam.dana")) return false
        val n = sbn.notification ?: return false
        val flags = n.flags
        if (flags and Notification.FLAG_ONGOING_EVENT != 0) return false
        if (flags and Notification.FLAG_FOREGROUND_SERVICE != 0) return false
        if (flags and Notification.FLAG_GROUP_SUMMARY != 0) return false
        if (n.category in SKIP_CATEGORIES) return false
        return true
    }

    /**
     * The app's label. The notification carries its sender's ApplicationInfo
     * ("android.appInfo"), which works even where package visibility hides
     * the app from a getApplicationInfo() lookup.
     */
    fun appName(context: Context, pkg: String, sbn: StatusBarNotification? = null): String {
        val pm = context.packageManager
        val fromNotification = try {
            @Suppress("DEPRECATION")
            (sbn?.notification?.extras?.get("android.appInfo") as? android.content.pm.ApplicationInfo)
                ?.let { pm.getApplicationLabel(it).toString() }
        } catch (e: Exception) {
            null
        }
        if (!fromNotification.isNullOrBlank() && fromNotification != pkg) return fromNotification
        return try {
            pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString()
        } catch (e: Exception) {
            pkg
        }
    }

    fun extract(context: Context, sbn: StatusBarNotification): JSONObject? {
        if (!isCapturable(context, sbn)) return null
        val pkg = sbn.packageName
        val app = appName(context, pkg, sbn)
        if (NotificationSettings.isDenied(context, pkg, app)) return null
        val n = sbn.notification
        val extras = n.extras ?: return null

        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString()?.trim().orEmpty()
        var text = extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString()?.trim()
            ?.takeIf { it.isNotEmpty() }
            ?: extras.getCharSequence(Notification.EXTRA_TEXT)?.toString()?.trim().orEmpty()
        var sender = ""
        var conversation = extras.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE)?.toString()?.trim().orEmpty()
        var isGroup = extras.getBoolean(Notification.EXTRA_IS_GROUP_CONVERSATION, false)

        // Chats: the last few messages with their senders read better than "3 new messages".
        val style = runCatching { NotificationCompat.MessagingStyle.extractMessagingStyleFromNotification(n) }.getOrNull()
        if (style != null && style.messages.isNotEmpty()) {
            val recent = style.messages.takeLast(5)
            text = recent.joinToString("\n") { m ->
                val who = m.person?.name?.toString()?.takeIf { it.isNotBlank() }
                if (who != null && (isGroup || style.isGroupConversation)) "$who: ${m.text}" else m.text?.toString().orEmpty()
            }.trim()
            sender = recent.lastOrNull()?.person?.name?.toString().orEmpty()
            if (conversation.isEmpty()) conversation = style.conversationTitle?.toString().orEmpty()
            isGroup = isGroup || style.isGroupConversation
        } else {
            // Inbox style (e.g. several emails): the lines are the content.
            extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)?.takeIf { it.isNotEmpty() }?.let { lines ->
                text = lines.takeLast(6).joinToString("\n") { it.toString() }
            }
        }
        if (title.isEmpty() && text.isEmpty()) return null

        return JSONObject()
            .put("key", sbn.key)
            .put("package", pkg)
            .put("app_name", app)
            .put("title", Redactor.redact(title).take(500))
            .put("text", Redactor.redact(text).take(4000))
            .put("sub_text", Redactor.redact(extras.getCharSequence(Notification.EXTRA_SUB_TEXT)?.toString().orEmpty()).take(300))
            .put("sender", sender.take(200))
            .put("conversation", conversation.take(200))
            .put("is_group", isGroup)
            .put("category", n.category ?: "")
            .put("channel", n.channelId ?: "")
            .put("posted_at", sbn.postTime)
            .put("actions", actionsJson(n))
    }

    /** The notification's buttons (title + whether it takes typed text), pressable from Dana. */
    fun actionsJson(n: Notification): JSONArray {
        val out = JSONArray()
        n.actions?.forEach { a ->
            val title = a?.title?.toString()?.trim().orEmpty()
            if (title.isEmpty() || a.actionIntent == null || out.length() >= 6) return@forEach
            out.put(JSONObject().put("title", title.take(60)).put("reply", a.remoteInputs?.any { it.allowFreeFormInput } == true))
        }
        return out
    }

    /** NotificationListenerService.REASON_* → the server's names. */
    fun reasonName(reason: Int): String = when (reason) {
        1 -> "click"          // REASON_CLICK
        2 -> "cancel"         // REASON_CANCEL (swiped)
        3 -> "cancel_all"     // REASON_CANCEL_ALL (clear all)
        24 -> "cancel"        // REASON_BUNDLE_DISMISSED (swiped a bundle)
        8, 9 -> "app"         // REASON_APP_CANCEL(_ALL)
        10, 11 -> "dana"      // REASON_LISTENER_CANCEL(_ALL): we did it
        18 -> "snoozed"
        19 -> "timeout"
        else -> "other"
    }
}

/**
 * Durable outbox for captured notifications and removals
 * (`filesDir/notification_outbox.json`): nothing is lost while the VPN is off
 * or the server is down — [NotificationSyncWorker] drains it.
 */
object NotificationOutbox {
    private const val TAG = "NotificationOutbox"
    private const val FILE_NAME = "notification_outbox.json"
    private const val MAX_ITEMS = 3000
    private val lock = Any()
    private var seq = System.currentTimeMillis()

    data class Batch(val ids: List<Long>, val posted: JSONArray, val removed: JSONArray) {
        val isEmpty get() = ids.isEmpty()
    }

    fun addPosted(context: Context, item: JSONObject) = add(context, "posted", item)
    fun addRemoved(context: Context, key: String, reason: String, code: Int? = null) =
        add(context, "removed", JSONObject().put("key", key).put("reason", reason).put("at", System.currentTimeMillis())
            .apply { code?.let { put("code", it) } })

    private fun add(context: Context, type: String, data: JSONObject) {
        synchronized(lock) {
            val arr = read(context)
            arr.put(JSONObject().put("id", ++seq).put("type", type).put("data", data))
            // Over the cap (days offline): drop the oldest, never the newest.
            val trimmed = if (arr.length() > MAX_ITEMS) {
                Log.w(TAG, "Outbox over $MAX_ITEMS items; dropping the oldest")
                JSONArray().also { out -> for (i in arr.length() - MAX_ITEMS until arr.length()) out.put(arr.get(i)) }
            } else arr
            write(context, trimmed)
        }
    }

    fun peek(context: Context, max: Int = 200): Batch = synchronized(lock) {
        val arr = read(context)
        val ids = mutableListOf<Long>()
        val posted = JSONArray()
        val removed = JSONArray()
        for (i in 0 until minOf(arr.length(), max)) {
            val o = arr.optJSONObject(i) ?: continue
            ids += o.optLong("id")
            val data = o.optJSONObject("data") ?: continue
            if (o.optString("type") == "removed") removed.put(data) else posted.put(data)
        }
        Batch(ids, posted, removed)
    }

    /** Drop what the server confirmed. */
    fun remove(context: Context, ids: Collection<Long>) {
        if (ids.isEmpty()) return
        val drop = ids.toHashSet()
        synchronized(lock) {
            val arr = read(context)
            val out = JSONArray()
            for (i in 0 until arr.length()) {
                val o = arr.optJSONObject(i) ?: continue
                if (o.optLong("id") !in drop) out.put(o)
            }
            write(context, out)
        }
    }

    fun size(context: Context): Int = synchronized(lock) { read(context).length() }

    fun clear(context: Context) = synchronized(lock) { write(context, JSONArray()) }

    private fun file(context: Context) = File(context.filesDir, FILE_NAME)

    private fun read(context: Context): JSONArray = try {
        val f = file(context)
        if (f.exists()) JSONArray(f.readText()) else JSONArray()
    } catch (e: Exception) {
        Log.e(TAG, "Outbox unreadable; starting over", e)
        JSONArray()
    }

    private fun write(context: Context, arr: JSONArray) {
        // Write-then-rename so a crash mid-write never corrupts the outbox.
        val f = file(context)
        val tmp = File(f.parentFile, "$FILE_NAME.tmp")
        tmp.writeText(arr.toString())
        if (!tmp.renameTo(f)) {
            f.writeText(arr.toString())
            tmp.delete()
        }
    }
}
