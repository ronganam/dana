package io.github.ronganam.dana.push

import io.github.ronganam.dana.data.network.ServerConfig
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import io.github.ronganam.dana.NotificationActionReceiver
import io.github.ronganam.dana.NotificationAlertReceiver
import io.github.ronganam.dana.ReminderReceiver
import io.github.ronganam.dana.SuggestionsReceiver
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import io.github.ronganam.dana.data.network.PeopleApi
import io.github.ronganam.dana.data.network.long
import io.github.ronganam.dana.data.network.text
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import java.util.UUID

/**
 * Direct push from the server (replaces the Home Assistant companion route).
 *
 * The server stores each event in a numbered outbox and wakes the phone with
 * a Firebase Cloud Messaging data message carrying only the number (phone
 * flavor: `DanaMessagingService`). [catchUp] then fetches every event after
 * the last one handled — with this app's token, so no content goes through
 * Google — and shows it. It also runs on app start and in the hourly sync, so
 * a lost ping only delays an event; the server drops expired ones (an old
 * urgent alert never pops up late).
 */
object PhoneEvents {
    private const val TAG = "PhoneEvents"
    private const val KEY_SEQ = "push_last_seq"
    private const val KEY_DEVICE = "push_device_id"
    private const val KEY_TOKEN = "push_token"
    private const val KEY_REGISTERED_AT = "push_registered_at"
    /** Firebase's advice: send the token again regularly so it never goes stale. */
    private const val REFRESH_MS = 7L * 24 * 3600 * 1000
    private val lock = Mutex()
    /** Events that change what Now / Notifications show (they refresh when one arrives). */
    private val REFRESHING = setOf("alert", "suggestions", "reminder", "reminder_clear", "briefing", "weekly_review", "context_card")

    fun deviceId(context: Context): String {
        val prefs = ServerConfig.prefs(context)
        return prefs.getString(KEY_DEVICE, null) ?: UUID.randomUUID().toString().also {
            prefs.edit().putString(KEY_DEVICE, it).apply()
        }
    }

    private fun configured(context: Context): Boolean = ServerConfig.load(context).isConfigured

    /** Registered within the last week (else the sync registers again). */
    fun isRegistered(context: Context): Boolean {
        val prefs = ServerConfig.prefs(context)
        return prefs.contains(KEY_TOKEN) &&
            System.currentTimeMillis() - prefs.getLong(KEY_REGISTERED_AT, 0) < REFRESH_MS
    }

    /** Send the FCM token to the server (on start and when Firebase rotates it). */
    suspend fun register(context: Context, token: String) {
        if (!configured(context)) return
        val prefs = ServerConfig.prefs(context)
        try {
            val version = context.packageManager.getPackageInfo(context.packageName, 0).versionName.orEmpty()
            val res = ServerConfig.load(context).api().devices.registerPush(deviceId(context), token, Build.MODEL, version)
            prefs.edit().putString(KEY_TOKEN, token).putLong(KEY_REGISTERED_AT, System.currentTimeMillis()).apply()
            // A fresh install starts from now instead of replaying old events.
            if (!prefs.contains(KEY_SEQ)) prefs.edit().putLong(KEY_SEQ, res).apply()
            Log.i(TAG, "Registered for push (latest $res)")
        } catch (e: Exception) {
            Log.w(TAG, "Push registration failed", e)
        }
        catchUp(context)
    }

    /** Fetch and show everything newer than the last handled event. */
    suspend fun catchUp(context: Context) {
        if (!configured(context)) return
        val prefs = ServerConfig.prefs(context)
        if (!prefs.contains(KEY_SEQ)) return  // not registered yet
        lock.withLock {
            val api = ServerConfig.load(context).api()
            repeat(5) {  // at most 5 pages of 50
                val after = prefs.getLong(KEY_SEQ, 0)
                val res = try {
                    api.devices.phoneEvents(after, deviceId(context))
                } catch (e: Exception) {
                    Log.w(TAG, "Couldn't fetch events", e)
                    return
                }
                val latest = res.latest
                // The server no longer knows this phone (token dropped, DB restored):
                // register again, otherwise pings would silently stop.
                if (!res.registered) {
                    Log.w(TAG, "Server forgot this device; registering again")
                    prefs.edit().remove(KEY_TOKEN).apply()
                    PushSetup.ensureRegistered(context)
                }
                val events = res.events
                if (events.isEmpty()) {
                    // The server's numbering went back (restored database): follow it.
                    if (latest < after) prefs.edit().putLong(KEY_SEQ, latest).apply()
                    return
                }
                for (ev in events) {
                    try {
                        dispatch(context, ev.type, ev.data)
                    } catch (e: Exception) {
                        Log.e(TAG, "Event ${ev.seq} (${ev.type}) failed", e)
                    }
                    prefs.edit().putLong(KEY_SEQ, ev.seq).apply()
                }
                if (events.size < 50) return
            }
        }
    }

    private suspend fun dispatch(context: Context, type: String, d: JsonObject) {
        fun s(k: String) = d.text(k)?.takeIf { it.isNotBlank() }
        Log.i(TAG, "Event: $type")
        if (type in REFRESHING) io.github.ronganam.dana.data.sync.LiveRefresh.ping(type)
        when (type) {
            "alert" -> NotificationAlertReceiver.Alert.from(d)?.let { NotificationAlertReceiver.show(context, it) }
            "suggestions" -> {
                val ids = (d["ids"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
                if (ids.isNotEmpty()) {
                    SuggestionsReceiver.show(context, ids, s("heading"), "${s("source_type")}:${s("source_id") ?: ids.first()}")
                }
            }
            "reminder" -> s("task_id")?.let {
                ReminderReceiver.show(context, it, s("task_title") ?: "Reminder", s("priority") ?: "medium", s("trigger_type") ?: "time")
            }
            "reminder_clear" -> s("task_id")?.let { ReminderReceiver.clear(context, it) }
            "notification_action" -> s("request_id")?.let { NotificationActionReceiver.handle(context, it) }
            // Dana's server is signing in to a bank that texts a code: watch for that SMS (only the id travels).
            "otp_request" -> s("request_id")?.let { io.github.ronganam.dana.data.money.MoneyCodes.handle(context, it) }
            "notify" -> notify(context, s("title") ?: "Dana", s("text").orEmpty(), s("tag"), s("channel") ?: "messages")
            // The shopping list changed (silent): refresh the cache, the widget and the watch.
            "shopping" -> s("list_id")?.let { io.github.ronganam.dana.data.shopping.ShoppingSync.onPush(context, it, d.long("version") ?: 0L) }
            "briefing" -> io.github.ronganam.dana.DanaCards.showBriefing(context, PeopleApi.briefing(d))
            "context_card" -> (d["card"] as? JsonObject)?.let { card ->
                io.github.ronganam.dana.DanaCards.showMeetingCard(
                    context, PeopleApi.card(card), s("event_title").orEmpty(), d.long("minutes")?.toInt() ?: 0
                )
            }
            "weekly_review" -> io.github.ronganam.dana.DanaCards.showReviewReady(
                context,
                s("headline") ?: "Weekly review",
                d.long("items")?.toInt() ?: 0,
                (d["summary"] as? JsonArray).orEmpty().map { (it as? JsonPrimitive)?.contentOrNull.orEmpty() }
            )
            // The laptop's coding-agent sessions (the whole list each time): live notifications.
            "agent_sessions" -> AgentSessions.show(context, d)
            else -> Log.w(TAG, "Unknown event type $type (newer server?)")
        }
    }

    private val channels = mapOf(
        "messages" to Triple("dana_messages", "Messages from Dana", NotificationManager.IMPORTANCE_HIGH),
        "important" to Triple("dana_important", "Important (from Dana)", NotificationManager.IMPORTANCE_HIGH),
        "digest" to Triple("dana_digest", "Notification digest", NotificationManager.IMPORTANCE_DEFAULT),
    )

    /** A plain notification from the server (voice transcripts, the digest, …). */
    private fun notify(context: Context, title: String, text: String, tag: String?, channel: String) {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val (id, name, importance) = channels[channel] ?: channels.getValue("messages")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nm.createNotificationChannel(NotificationChannel(id, name, importance))
        }
        // "Robin shared their shopping list with you" opens Shopping (under More).
        val open = if (tag?.startsWith("shopping-invite-") == true) {
            android.content.Intent(context, io.github.ronganam.dana.MainActivity::class.java)
                .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK or android.content.Intent.FLAG_ACTIVITY_CLEAR_TOP)
                .putExtra(io.github.ronganam.dana.EXTRA_OPEN_SCREEN, "shopping")
        } else context.packageManager.getLaunchIntentForPackage(context.packageName)
        val builder = NotificationCompat.Builder(context, id)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setAutoCancel(true)
        if (open != null) {
            builder.setContentIntent(PendingIntent.getActivity(context, (tag ?: title).hashCode(), open,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
        }
        try {
            // Same tag = replace (a redelivered transcript doesn't stack).
            nm.notify(tag, if (tag != null) 1 else (title + text).hashCode(), builder.build())
        } catch (e: SecurityException) {
            Log.w(TAG, "No notification permission", e)
        }
    }
}
