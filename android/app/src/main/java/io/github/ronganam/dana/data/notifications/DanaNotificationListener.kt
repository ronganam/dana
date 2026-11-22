package io.github.ronganam.dana.data.notifications

import android.app.ActivityOptions
import android.app.Notification
import android.app.PendingIntent
import android.content.Context
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import io.github.ronganam.dana.data.money.MoneySignals
import io.github.ronganam.dana.data.money.string

/**
 * Sees every notification the phone shows (once the user grants notification
 * access) and queues the ones worth triaging for Dana's Inbox. Removals from
 * the shade are queued too, so the Inbox knows what was opened or swiped.
 *
 * Suggest-only: this service never dismisses anything on its own. It cancels
 * a notification only when the user dismissed it in Dana ([cancel]), and
 * snoozes one only when the user snoozed it in Dana ([snooze]).
 */
class DanaNotificationListener : NotificationListenerService() {

    // Tap actions of what's in the shade, so Dana's alert screen can open the
    // notification in its own app (the same thing a tap in the shade does).
    private val contentIntents = object : LinkedHashMap<String, PendingIntent>(128, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, PendingIntent>?) = size > 300
    }

    // Why each group summary left the shade (group key → reason, time): its
    // children are reported as "group summary cancelled" and take this reason,
    // so swiping a whole group counts as swiping each notification in it.
    private val summaryReasons = object : LinkedHashMap<String, Pair<Int, Long>>(32, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Pair<Int, Long>>?) = size > 64
    }

    override fun onListenerConnected() {
        super.onListenerConnected()
        instance = this
        // Android rebinds the listener after an update or a kill even when
        // nothing else of the app comes back: keep the call watcher alive too.
        io.github.ronganam.dana.CallRecordingObserverService.ensureRunning(this)
        // Signals that need a live process (this one is kept bound): unlocks, car mode.
        io.github.ronganam.dana.data.wake.UnlockWatcher.register(this)
        io.github.ronganam.dana.data.signals.PhoneSignals.watch(this)
        // A bank sign-in code the server still waits for (only while one is armed).
        io.github.ronganam.dana.data.money.MoneyCodes.scanShade(this)
        if (!NotificationSettings.enabled(this)) return
        // What is already in the shade (the server dedupes repeats).
        try {
            activeNotifications?.forEach { capture(it) }
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't read active notifications", e)
        }
        NotificationSyncWorker.schedule(this)
        NotificationSyncWorker.ensurePeriodic(this)
    }

    override fun onListenerDisconnected() {
        if (instance === this) instance = null
        super.onListenerDisconnected()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        sbn ?: return
        // A bank sign-in code Dana's server is waiting for: looked at before any
        // capture, redaction or skipping, and only while a request is armed.
        io.github.ronganam.dana.data.money.MoneyCodes.onPosted(this, sbn)
        if (!NotificationSettings.enabled(this)) return
        if (capture(sbn)) NotificationSyncWorker.schedule(this)
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification?, rankingMap: RankingMap?, reason: Int) {
        sbn ?: return
        if (!NotificationSettings.enabled(this)) return
        val group = sbn.groupKey
        val now = System.currentTimeMillis()
        if (group != null && ((sbn.notification?.flags ?: 0) and Notification.FLAG_GROUP_SUMMARY) != 0) {
            synchronized(summaryReasons) { summaryReasons[group] = reason to now }
        }
        if (!NotificationExtractor.isCapturable(this, sbn)) return
        val key = sbn.key ?: return
        val pkg = sbn.packageName ?: return
        if (MoneySignals.routes(this, pkg)) return  // money signals carry no removals
        if (NotificationSettings.isDenied(this, pkg, NotificationExtractor.appName(this, pkg, sbn))) return
        val effective = if (reason == REASON_GROUP_SUMMARY_CANCELED) {
            val fromSummary = group?.let { g -> synchronized(summaryReasons) { summaryReasons[g] } }
                ?.takeIf { now - it.second < 10_000 }?.first
            // The summary always goes first; without it, a group vanishing at
            // once is almost always the user clearing it.
            if (fromSummary != null && fromSummary != REASON_GROUP_SUMMARY_CANCELED) fromSummary else REASON_CANCEL
        } else reason
        val name = NotificationExtractor.reasonName(effective)
        if (name == "other") Log.i(TAG, "Removed from $pkg with unmapped reason $reason")
        NotificationOutbox.addRemoved(this, key, name, reason)
        NotificationSyncWorker.schedule(this)
    }

    /** Queue [sbn] when it is new content; true if something was queued. */
    private fun capture(sbn: StatusBarNotification): Boolean {
        // Bank / card apps go to the money addon instead, never to the Inbox
        // (whatever "skip banking apps" says), while the user has it.
        if (MoneySignals.routes(this, sbn.packageName)) return captureMoney(sbn)
        val item = try {
            NotificationExtractor.extract(this, sbn)
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't read a notification from ${sbn.packageName}", e)
            null
        } ?: return false
        AppIcons.remember(this, sbn)
        sbn.notification?.contentIntent?.let { pi -> synchronized(contentIntents) { contentIntents[sbn.key] = pi } }
        val fingerprint = "${sbn.key}\u0000${item.title}\u0000${item.text}"
        synchronized(recent) {
            if (recent.containsKey(fingerprint)) return false
            recent[fingerprint] = System.currentTimeMillis()
        }
        NotificationOutbox.addPosted(this, item)
        return true
    }

    /** Queue [sbn] as a money signal when it is new content. */
    private fun captureMoney(sbn: StatusBarNotification): Boolean {
        val item = try {
            MoneySignals.item(this, sbn)
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't read a notification from ${sbn.packageName}", e)
            null
        } ?: return false
        val fingerprint = "money\u0000${sbn.key}\u0000${item.string("title")}\u0000${item.string("text")}"
        synchronized(recent) {
            if (recent.containsKey(fingerprint)) return false
            recent[fingerprint] = System.currentTimeMillis()
        }
        MoneySignals.add(this, item)
        return true
    }

    companion object {
        // Apps re-post identical content (progress pings, re-alerts); send each
        // once. Process-wide: Android re-binds the listener now and then (a new
        // service instance), and that shouldn't re-send the whole shade.
        private val recent = object : LinkedHashMap<String, Long>(256, 0.75f, true) {
            override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Long>?) = size > 400
        }

        private const val TAG = "DanaNotifListener"

        @Volatile
        private var instance: DanaNotificationListener? = null

        val isConnected: Boolean get() = instance != null

        /** What is in the shade right now (empty without notification access). */
        fun shade(): List<StatusBarNotification> = try {
            instance?.activeNotifications?.filterNotNull() ?: emptyList()
        } catch (e: Exception) {
            emptyList()
        }

        /** Keys currently in the shade (so the server can say which were dismissed in Dana). */
        fun activeKeys(): List<String> = try {
            instance?.activeNotifications?.mapNotNull { it.key } ?: emptyList()
        } catch (e: Exception) {
            emptyList()
        }

        /** Keys parked in Android's snooze list (out of the shade until they return). */
        fun snoozedKeys(): List<String> = try {
            instance?.snoozedNotifications?.mapNotNull { it.key } ?: emptyList()
        } catch (e: Exception) {
            emptyList()
        }

        /**
         * Snooze [untilByKey] (shade key → epoch ms) with Android's own snooze:
         * they leave the shade and Android posts them again at that time.
         * Returns the keys that were in the shade and got snoozed.
         */
        fun snooze(untilByKey: Map<String, Long>): Set<String> {
            val svc = instance ?: return emptySet()
            val inShade = activeKeys().toSet()
            val now = System.currentTimeMillis()
            val done = mutableSetOf<String>()
            untilByKey.forEach { (key, until) ->
                if (key !in inShade || until <= now + 5_000) return@forEach
                try {
                    svc.snoozeNotification(key, until - now)
                    done += key
                } catch (e: Exception) {
                    Log.w(TAG, "Couldn't snooze $key", e)
                }
            }
            return done
        }

        /**
         * Bring snoozed notifications back to the shade now. There's no public
         * unsnooze, but re-snoozing a snoozed key for a second reposts it.
         */
        fun unsnooze(keys: Collection<String>) {
            val svc = instance ?: return
            val inShade = activeKeys().toSet()
            keys.filter { it.isNotBlank() && it !in inShade }.forEach { key ->
                try {
                    svc.snoozeNotification(key, 1_000)
                } catch (e: Exception) {
                    Log.w(TAG, "Couldn't unsnooze $key", e)
                }
            }
        }

        /**
         * Open a notification in its app, as a tap in the shade would. False
         * when it's no longer in the shade (or its app gave it no tap action).
         */
        fun open(context: Context, key: String?): Boolean {
            val svc = instance ?: return false
            key ?: return false
            val pi = synchronized(svc.contentIntents) { svc.contentIntents[key] }
                ?: try { svc.activeNotifications?.firstOrNull { it.key == key }?.notification?.contentIntent } catch (e: Exception) { null }
                ?: return false
            return try {
                if (Build.VERSION.SDK_INT >= 34) {
                    val opts = ActivityOptions.makeBasic().apply {
                        @Suppress("DEPRECATION")
                        setPendingIntentBackgroundActivityStartMode(ActivityOptions.MODE_BACKGROUND_ACTIVITY_START_ALLOWED)
                    }
                    pi.send(context, 0, null, null, null, null, opts.toBundle())
                } else {
                    pi.send()
                }
                true
            } catch (e: Exception) {
                Log.w(TAG, "Couldn't open $key", e)
                false
            }
        }

        /** The live notification behind [key], if it's still in the shade. */
        private fun live(key: String?): StatusBarNotification? {
            val svc = instance ?: return null
            key ?: return null
            return try { svc.activeNotifications?.firstOrNull { it.key == key } } catch (e: Exception) { null }
        }

        /** Buttons of [key] as it is in the shade right now: title → takes typed text. */
        fun liveActions(key: String?): List<Pair<String, Boolean>> =
            live(key)?.notification?.actions.orEmpty().mapNotNull { a ->
                val t = a?.title?.toString()?.trim().orEmpty()
                if (t.isEmpty() || a.actionIntent == null) null
                else t to (a.remoteInputs?.any { it.allowFreeFormInput } == true)
            }

        /** The app's suggested replies on [key]'s reply button (RemoteInput choices / smart replies), if any. */
        fun replyChoices(key: String?): List<String> =
            live(key)?.notification?.actions.orEmpty().flatMap { a ->
                a?.remoteInputs.orEmpty().flatMap { ri -> ri.choices.orEmpty().map { it.toString() } }
            }.map { it.trim() }.filter { it.isNotEmpty() }.distinct().take(4)

        /** True while [key] is in the shade (its buttons can be pressed). */
        fun inShade(key: String?): Boolean = live(key) != null

        /**
         * Press the button titled [title] on notification [key] (case-insensitive),
         * filling [text] into its reply field when it has one. Returns null when
         * done, else why not (shown to the user).
         */
        fun perform(context: Context, key: String?, title: String, text: String = ""): String? {
            if (instance == null) return "Notification access is off"
            val sbn = live(key) ?: return "It's no longer in the notification shade"
            val action = sbn.notification?.actions?.firstOrNull {
                it?.title?.toString()?.trim().equals(title.trim(), ignoreCase = true)
            } ?: return "The notification no longer has a “$title” button"
            val pi = action.actionIntent ?: return "That button does nothing"
            val inputs = action.remoteInputs?.filter { it.allowFreeFormInput }.orEmpty()
            if (inputs.isNotEmpty() && text.isBlank()) return "Type a reply first"
            return try {
                val fill = android.content.Intent()
                if (inputs.isNotEmpty()) {
                    val results = android.os.Bundle().apply { inputs.forEach { putCharSequence(it.resultKey, text) } }
                    android.app.RemoteInput.addResultsToIntent(inputs.toTypedArray(), fill, results)
                }
                if (Build.VERSION.SDK_INT >= 34) {
                    val opts = ActivityOptions.makeBasic().apply {
                        @Suppress("DEPRECATION")
                        setPendingIntentBackgroundActivityStartMode(ActivityOptions.MODE_BACKGROUND_ACTIVITY_START_ALLOWED)
                    }
                    pi.send(context, 0, fill, null, null, null, opts.toBundle())
                } else {
                    pi.send(context, 0, fill)
                }
                null
            } catch (e: Exception) {
                Log.w(TAG, "Couldn't press “$title” on $key", e)
                "The app refused it (${e.javaClass.simpleName})"
            }
        }

        /** Clear notifications the user dismissed in Dana. */
        fun cancel(keys: Collection<String>) {
            val svc = instance ?: return
            if (keys.isEmpty()) return
            try {
                svc.cancelNotifications(keys.toTypedArray())
            } catch (e: Exception) {
                Log.w(TAG, "Couldn't cancel ${keys.size} notification(s)", e)
            }
            // A listener can't cancel a snoozed notification: bring it back for
            // a moment, then cancel it, so it doesn't return after a dismissal.
            val snoozed = snoozedKeys().toSet()
            val parked = keys.filter { it in snoozed }
            if (parked.isEmpty()) return
            unsnooze(parked)
            Handler(Looper.getMainLooper()).postDelayed({
                try {
                    instance?.cancelNotifications(parked.toTypedArray())
                } catch (e: Exception) {
                    Log.w(TAG, "Couldn't cancel ${parked.size} snoozed notification(s)", e)
                }
            }, 2_500)
        }
    }
}
