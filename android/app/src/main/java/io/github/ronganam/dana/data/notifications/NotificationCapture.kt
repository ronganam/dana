package io.github.ronganam.dana.data.notifications

import io.github.ronganam.dana.data.network.ServerConfig
import android.app.Notification
import android.content.Context
import android.content.Intent
import android.provider.Settings
import android.service.notification.StatusBarNotification
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.CapturedNotification
import io.github.ronganam.dana.data.network.LenientListSerializer
import io.github.ronganam.dana.data.network.NotificationButton
import io.github.ronganam.dana.data.network.RemovedNotification
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonObject
import java.io.File
import androidx.core.content.edit

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
    fun setEnabled(context: Context, on: Boolean) = prefs(context).edit { putBoolean(KEY_ENABLED, on) }

    fun skipSensitive(context: Context) = prefs(context).getBoolean(KEY_SKIP_SENSITIVE, true)
    fun setSkipSensitive(context: Context, on: Boolean) = prefs(context).edit { putBoolean(KEY_SKIP_SENSITIVE, on) }

    fun deniedPackages(context: Context): Set<String> = prefs(context).getStringSet(KEY_DENIED, emptySet())?.toSet() ?: emptySet()
    fun deny(context: Context, pkg: String) = prefs(context).edit { putStringSet(KEY_DENIED, deniedPackages(context) + pkg) }
    fun allow(context: Context, pkg: String) = prefs(context).edit { putStringSet(KEY_DENIED, deniedPackages(context) - pkg) }

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

    private fun prefs(context: Context) = ServerConfig.prefs(context)
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

/** StatusBarNotification → what the server's `posted` list expects (null = skip it). */
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

    fun extract(context: Context, sbn: StatusBarNotification): CapturedNotification? {
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

        return CapturedNotification(
            key = sbn.key,
            packageName = pkg,
            appName = app,
            title = Redactor.redact(title).take(500),
            text = Redactor.redact(text).take(4000),
            subText = Redactor.redact(extras.getCharSequence(Notification.EXTRA_SUB_TEXT)?.toString().orEmpty()).take(300),
            sender = sender.take(200),
            conversation = conversation.take(200),
            isGroup = isGroup,
            category = n.category ?: "",
            channel = n.channelId ?: "",
            postedAt = sbn.postTime,
            actions = buttons(n)
        )
    }

    /** The notification's buttons (title + whether it takes typed text), pressable from Dana. */
    fun buttons(n: Notification): List<NotificationButton> {
        val out = mutableListOf<NotificationButton>()
        n.actions?.forEach { a ->
            val title = a?.title?.toString()?.trim().orEmpty()
            if (title.isEmpty() || a.actionIntent == null || out.size >= 6) return@forEach
            out += NotificationButton(title.take(60), a.remoteInputs?.any { it.allowFreeFormInput } == true)
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

    data class Batch(val ids: List<Long>, val posted: List<CapturedNotification>, val removed: List<RemovedNotification>) {
        val isEmpty get() = ids.isEmpty()
    }

    /** One queued entry of `notification_outbox.json`: `{id, type: posted | removed, data}`. */
    @Serializable
    private data class Entry(val id: Long = 0, val type: String = "", val data: JsonObject? = null)

    fun addPosted(context: Context, item: CapturedNotification) =
        add(context, "posted", ApiJson.encodeToJsonElement(CapturedNotification.serializer(), item) as JsonObject)

    fun addRemoved(context: Context, key: String, reason: String, code: Int? = null) =
        add(context, "removed", ApiJson.encodeToJsonElement(RemovedNotification.serializer(), RemovedNotification(key, reason, System.currentTimeMillis(), code)) as JsonObject)

    private fun add(context: Context, type: String, data: JsonObject) {
        synchronized(lock) {
            val all = read(context) + Entry(++seq, type, data)
            // Over the cap (days offline): drop the oldest, never the newest.
            val trimmed = if (all.size > MAX_ITEMS) {
                Log.w(TAG, "Outbox over $MAX_ITEMS items; dropping the oldest")
                all.takeLast(MAX_ITEMS)
            } else all
            write(context, trimmed)
        }
    }

    fun peek(context: Context, max: Int = 200): Batch = synchronized(lock) {
        val ids = mutableListOf<Long>()
        val posted = mutableListOf<CapturedNotification>()
        val removed = mutableListOf<RemovedNotification>()
        for (e in read(context).take(max)) {
            ids += e.id
            val data = e.data ?: continue
            if (e.type == "removed") {
                runCatching { ApiJson.decodeFromJsonElement(RemovedNotification.serializer(), data) }.getOrNull()?.let { removed += it }
            } else {
                runCatching { ApiJson.decodeFromJsonElement(CapturedNotification.serializer(), data) }.getOrNull()?.let { posted += it }
            }
        }
        Batch(ids, posted, removed)
    }

    /** Drop what the server confirmed. */
    fun remove(context: Context, ids: Collection<Long>) {
        if (ids.isEmpty()) return
        val drop = ids.toHashSet()
        synchronized(lock) { write(context, read(context).filter { it.id !in drop }) }
    }

    fun size(context: Context): Int = synchronized(lock) { read(context).size }

    fun clear(context: Context) = synchronized(lock) { write(context, emptyList()) }

    private fun file(context: Context) = File(context.filesDir, FILE_NAME)

    private fun read(context: Context): List<Entry> = try {
        val f = file(context)
        if (f.exists()) ApiJson.decodeFromString(LenientListSerializer(Entry.serializer()), f.readText()) else emptyList()
    } catch (e: Exception) {
        Log.e(TAG, "Outbox unreadable; starting over", e)
        emptyList()
    }

    private fun write(context: Context, entries: List<Entry>) {
        // Write-then-rename so a crash mid-write never corrupts the outbox.
        val text = ApiJson.encodeToString(ListSerializer(Entry.serializer()), entries)
        val f = file(context)
        val tmp = File(f.parentFile, "$FILE_NAME.tmp")
        tmp.writeText(text)
        if (!tmp.renameTo(f)) {
            f.writeText(text)
            tmp.delete()
        }
    }
}
