package io.github.ronganam.dana.push

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationChannelGroup
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Icon
import android.os.Build
import android.util.Log
import androidx.annotation.DrawableRes
import androidx.core.content.edit
import androidx.core.net.toUri
import io.github.ronganam.dana.EXTRA_OPEN_SCREEN
import io.github.ronganam.dana.EXTRA_OPEN_SESSION
import io.github.ronganam.dana.MainActivity
import io.github.ronganam.dana.R
import io.github.ronganam.dana.data.models.LiveAction
import io.github.ronganam.dana.data.models.LiveActivity
import io.github.ronganam.dana.data.models.LiveProgress
import io.github.ronganam.dana.data.network.ApiException
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.LiveApi
import io.github.ronganam.dana.data.network.ServerConfig
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.JsonObject
import java.io.File
import java.io.IOException

/**
 * Dana's live activities on the phone (server `core/live.py`): each `live`
 * push carries every activity, and [show] makes the shade match it as
 * Android 16 Live Updates (promoted ongoing notifications: the status-bar
 * chip, Samsung's Now Bar). One generic renderer for every add-on; what it
 * shows is the activity's own fields overridden by its selected choice's
 * ([LiveActivity.look]).
 *
 * - running → one ongoing notification per activity (tag `live:<id>`),
 *   silent on `live_<addon>`; a new non-empty `alert` re-posts it once on
 *   `live_<addon>_alert`.
 * - ended with a `final` → posted once per `rev` as a normal notification.
 * - missing from the list (or ended without a final) → cancelled.
 *
 * Buttons go to [LiveActivityReceiver]: `next` / `prev` switch the choice
 * here at once (from the cached payload) and tell the server after (kept
 * and sent again with the next push while offline), `server` actions are
 * answered with the new activity, `stop` and swiping it away end it. The
 * delete intent also fires when Android times one out at its `ends_at`
 * (`setTimeoutAfter`): that one is only forgotten here ([dismissIsTimeout]),
 * the server ends it itself. Every intent carries its own data URI
 * ([intentUri]) so no two ever match one another, whatever their request codes.
 */
object LiveActivities {
    private const val TAG = "LiveActivities"
    private const val KEY_SHOWN = "live_shown"
    private const val KEY_PENDING = "live_pending_select"
    private const val CACHE_FILE = "live_activities.json"
    private const val GROUP = "live"
    private const val NOTIFICATION_TAG = "live:"
    const val ACTION = "io.github.ronganam.dana.LIVE_ACTION"
    const val EXTRA_ID = "live_id"
    const val EXTRA_ACTION = "live_action"
    const val EXTRA_KIND = "live_kind"
    /** [EXTRA_KIND] of the delete intent (the user swiped it away, or Android timed it out). */
    const val KIND_DISMISS = "dismiss"
    /** A delete intent this close to (or past) the activity's `ends_at` is its timeout, not the user. */
    const val TIMEOUT_SLACK_MS = 10_000L
    private const val URI_SCHEME = "dana-live"

    /** The cache, prefs and the shade change together. */
    private val lock = Any()

    /** What was shown per activity id: the last `rev` posted, the last alert shown, whether it was live. */
    @Serializable
    data class Shown(val rev: Int = 0, val alert: String = "", val live: Boolean = false)

    /** What [show] does with one activity. */
    sealed class Step {
        data class Live(val activity: LiveActivity, val alert: Boolean) : Step()
        data class Final(val activity: LiveActivity) : Step()
        data class Cancel(val id: String) : Step()
    }

    /** Where tapping one goes. */
    sealed interface Open {
        /** An https page (Dana's web app for a "/path" the phone has no screen for). */
        data class Web(val url: String) : Open
        /** A screen of the app (`MainActivity`'s `EXTRA_OPEN_SCREEN`). */
        data class Screen(val route: String) : Open
        /** A conversation, shown in the thread pane (`EXTRA_OPEN_SESSION`; a watch's agent run, say). */
        data class Session(val id: String) : Open
        /** Dana as it is. */
        data object Home : Open
    }

    /** Tone → colour (ARGB; the notification's accent, segments and points). */
    val TONES: Map<String, Int> = mapOf(
        "neutral" to 0xFF5B8DEF.toInt(),
        "good" to 0xFF3DB86B.toInt(),
        "warn" to 0xFFFF9F1A.toInt(),
        "bad" to 0xFFE5484D.toInt(),
        "muted" to 0xFF8A8F98.toInt(),
    )

    /** The server's ICONS → drawables (Lucide); an unknown name gets Dana's icon. */
    val ICONS: Map<String, Int> = mapOf(
        "train-front" to R.drawable.ic_live_train_front,
        "tram-front" to R.drawable.ic_live_tram_front,
        "bus" to R.drawable.ic_live_bus,
        "car" to R.drawable.ic_live_car,
        "bike" to R.drawable.ic_live_bike,
        "footprints" to R.drawable.ic_live_footprints,
        "plane" to R.drawable.ic_live_plane,
        "ship" to R.drawable.ic_live_ship,
        "timer" to R.drawable.ic_live_timer,
        "clock" to R.drawable.ic_live_clock,
        "bot" to R.drawable.ic_live_bot,
        "terminal" to R.drawable.ic_live_terminal,
        "package" to R.drawable.ic_live_package,
        "truck" to R.drawable.ic_live_truck,
        "chef-hat" to R.drawable.ic_live_chef_hat,
        "utensils" to R.drawable.ic_live_utensils,
        "shopping-cart" to R.drawable.ic_live_shopping_cart,
        "dumbbell" to R.drawable.ic_live_dumbbell,
        "sprout" to R.drawable.ic_live_sprout,
        "droplets" to R.drawable.ic_live_droplets,
        "flame" to R.drawable.ic_live_flame,
        "bell" to R.drawable.ic_live_bell,
        "map-pin" to R.drawable.ic_live_map_pin,
        "house" to R.drawable.ic_live_house,
        "sparkles" to R.drawable.ic_live_sparkles,
        "zap" to R.drawable.ic_live_zap,
    )

    /** The app's screens a "/path" can name (`ShellController.openRoute`); other paths open the web app. */
    private val SCREENS = setOf(
        "chat", "now", "tasks", "inbox", "notifications", "projects", "calls", "people", "shopping",
        "money", "memory", "about", "settings", "signals", "review",
    )

    @DrawableRes
    fun icon(name: String): Int = ICONS[name] ?: R.drawable.ic_launcher_monochrome

    fun tone(name: String): Int = TONES[name] ?: TONES.getValue("neutral")

    /**
     * The steps that make the shade match [list], given what was [shown]:
     * running ones are re-posted (alerting when `alert` is non-empty and not
     * the last one shown), a final is posted once per `rev` (or when one shown
     * live ended), ended ones without a final and shown ones no longer listed
     * are cancelled.
     */
    fun plan(shown: Map<String, Shown>, list: List<LiveActivity>): List<Step> {
        val steps = mutableListOf<Step>()
        for (a in list) {
            val was = shown[a.id]
            when {
                !a.ended -> steps += Step.Live(a, alert = a.alert.isNotEmpty() && a.alert != was?.alert)
                a.final != null -> if (was == null || was.rev != a.rev || was.live) steps += Step.Final(a)
                was != null -> steps += Step.Cancel(a.id)
            }
        }
        val ids = list.map { it.id }.toSet()
        shown.forEach { (id, was) -> if (id !in ids && was.live) steps += Step.Cancel(id) }
        return steps
    }

    /** What is shown after [plan] ran: the listed ids only (a final stays as posted, then is forgotten). */
    fun nextShown(shown: Map<String, Shown>, list: List<LiveActivity>): Map<String, Shown> =
        list.associate { a -> a.id to Shown(a.rev, a.alert.ifEmpty { shown[a.id]?.alert.orEmpty() }, !a.ended) }

    /** The choice key [delta] steps from the selected one; null past either end or without choices. */
    fun neighbour(a: LiveActivity, delta: Int): String? {
        val i = a.choices.indexOfFirst { it.key == a.selectedKey }
        if (i < 0) return null
        return a.choices.getOrNull(i + delta)?.key
    }

    /** The buttons to show: `next` hidden on the last choice, `prev` on the first; at most 3. */
    fun visibleActions(a: LiveActivity): List<LiveAction> = a.actions.filter {
        when (it.kind) {
            "next" -> neighbour(a, 1) != null
            "prev" -> neighbour(a, -1) != null
            else -> true
        }
    }.take(3)

    /**
     * Where [open] leads: https as is, a conversation ("/chat/<id>",
     * "/chat?session=<id>", "/sessions/<id>") to it in the app, a "/path" to
     * that screen (or the web app at [serverUrl]), "" to Dana.
     */
    fun openTarget(open: String, serverUrl: String): Open = when {
        open.startsWith("https://") -> Open.Web(open)
        open.startsWith("/") && sessionOf(open) != null -> sessionOf(open)!!.let { if (it == "main") Open.Screen("chat") else Open.Session(it) }
        open.startsWith("/") -> {
            val first = open.removePrefix("/").split('/', '?', '#').first().lowercase()
            when {
                first in SCREENS -> Open.Screen(first)
                serverUrl.isNotBlank() && first.isNotEmpty() -> Open.Web(serverUrl.trimEnd('/') + open)
                else -> Open.Home
            }
        }
        else -> Open.Home
    }

    /** The session a "/path" names: `/chat/<id>`, `/sessions/<id>`, or `/chat?session=<id>` (`session_id=` too); else null. */
    fun sessionOf(path: String): String? {
        val (route, query) = path.removePrefix("/").substringBefore('#').let { it.substringBefore('?') to it.substringAfter('?', "") }
        val parts = route.split('/').filter { it.isNotEmpty() }
        fun decoded(s: String): String? = try {
            java.net.URLDecoder.decode(s, "UTF-8").trim().takeIf { it.isNotEmpty() }
        } catch (e: IllegalArgumentException) {
            null
        }
        val first = parts.firstOrNull()?.lowercase() ?: return null
        if (first !in setOf("chat", "sessions")) return null
        if (parts.size == 2) return decoded(parts[1])
        if (parts.size == 1 && first == "chat") {
            return query.split('&').firstNotNullOfOrNull { kv ->
                val k = kv.substringBefore('=')
                if (k == "session" || k == "session_id") decoded(kv.substringAfter('=', "")) else null
            }
        }
        return null
    }

    /**
     * Whether a delete intent for an activity ending at [endsAtMs] is Android's
     * `setTimeoutAfter` expiry (it passed, or is within [TIMEOUT_SLACK_MS]) rather
     * than the user swiping it away. Nothing to end on the server then.
     */
    fun dismissIsTimeout(endsAtMs: Long?, nowMs: Long): Boolean = endsAtMs != null && endsAtMs - nowMs <= TIMEOUT_SLACK_MS

    /** The data URI that makes a PendingIntent of activity [id] unique: `dana-live://<id>/<kind>/<action>`, each part URL-encoded. */
    fun intentUri(id: String, kind: String, action: String): String =
        "$URI_SCHEME://${enc(id)}/${enc(kind)}/${enc(action)}"

    private fun enc(part: String): String = java.net.URLEncoder.encode(part, Charsets.UTF_8.name()).replace("+", "%20")

    /** Make the shade match the pushed list (`data.activities`), then send selections made offline. */
    suspend fun show(context: Context, data: JsonObject) {
        val pending = loadPending(context)
        val list = LiveApi.parse(data).map { a ->
            pending[a.id]?.takeIf { key -> a.choices.any { it.key == key } }?.let { a.copy(selected = it) } ?: a
        }
        synchronized(lock) {
            val shown = loadShown(context)
            val nm = manager(context)
            plan(shown, list).forEach { apply(context, nm, it) }
            saveShown(context, nextShown(shown, list))
            saveCache(context, list)
        }
        val stillValid = pending.filter { (id, key) -> list.any { a -> a.id == id && a.choices.any { it.key == key } } }
        if (stillValid != pending) savePending(context, stillValid)
        stillValid.forEach { (id, key) -> sendSelection(context, id, key) }
    }

    /** One activity changed here or in a reply ([a] null: it ended): redraw it, keep the cache and prefs in step. */
    internal fun replace(context: Context, id: String, a: LiveActivity?) {
        synchronized(lock) {
            val nm = manager(context)
            val shown = loadShown(context)
            val cache = loadCache(context)
            if (a == null) {
                cancel(nm, id)
                saveShown(context, shown - id)
                saveCache(context, cache.filter { it.id != id })
                return
            }
            val mine = shown.filterKeys { it == id }
            plan(mine, listOf(a)).forEach { apply(context, nm, it) }
            saveShown(context, shown - id + nextShown(mine, listOf(a)))
            val i = cache.indexOfFirst { it.id == id }
            saveCache(context, if (i < 0) listOf(a) + cache else cache.toMutableList().also { it[i] = a })
        }
    }

    /** The cached activity [id] (the last payload drawn), or null. */
    internal fun cached(context: Context, id: String): LiveActivity? = synchronized(lock) { loadCache(context).firstOrNull { it.id == id } }

    /** `next` / `prev`: show the neighbouring choice now, then tell the server. */
    internal suspend fun step(context: Context, id: String, delta: Int) {
        val a = cached(context, id) ?: return
        val key = neighbour(a, delta) ?: return
        replace(context, id, a.copy(selected = key))
        savePending(context, loadPending(context) + (id to key))
        sendSelection(context, id, key)
    }

    /** POST the selection; done (or refused) → forgotten, unreachable → kept for the next push. */
    private suspend fun sendSelection(context: Context, id: String, key: String) {
        try {
            val a = ServerConfig.load(context).api().live.select(id, key)
            dropPending(context, id, key)
            if (a != null && a.selectedKey == key) replace(context, id, a)
        } catch (e: ApiException) {
            Log.w(TAG, "Selection refused (${e.code})", e)
            dropPending(context, id, key)
        } catch (e: IOException) {
            Log.w(TAG, "Selection kept for later", e)
        }
    }

    /** A `server` action: the add-on answers with the activity after it. */
    internal suspend fun act(context: Context, id: String, action: String) {
        try {
            val a = ServerConfig.load(context).api().live.act(id, action, cached(context, id)?.selectedKey?.ifEmpty { null })
            replace(context, id, a)
        } catch (e: Exception) {
            Log.w(TAG, "Action $action failed", e)
        }
    }

    /** The delete intent: Android's timeout at `ends_at` is only forgotten here; the user's swipe stops it. */
    internal suspend fun dismissed(context: Context, id: String) {
        val a = cached(context, id)
        if (a != null && dismissIsTimeout(a.endsAtMs, System.currentTimeMillis())) {
            replace(context, id, null)
            dropPending(context, id, null)
        } else {
            stop(context, id)
        }
    }

    /** `stop` (cancelled first) or swiped away: end it here and on the server. */
    internal suspend fun stop(context: Context, id: String) {
        replace(context, id, null)
        dropPending(context, id, null)
        try {
            ServerConfig.load(context).api().live.stop(id)
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't stop $id on the server", e)
        }
    }

    private fun apply(context: Context, nm: NotificationManager, step: Step) {
        when (step) {
            is Step.Live -> {
                val left = step.activity.endsAtMs?.let { it - System.currentTimeMillis() }
                if (left != null && left <= 0) cancel(nm, step.activity.id)
                else post(nm, step.activity.id, live(context, nm, step.activity, step.alert, left))
            }
            is Step.Final -> post(nm, step.activity.id, final(context, nm, step.activity))
            is Step.Cancel -> cancel(nm, step.id)
        }
    }

    private fun manager(context: Context) = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    private fun cancel(nm: NotificationManager, id: String) = nm.cancel(NOTIFICATION_TAG + id, 1)

    private fun post(nm: NotificationManager, id: String, n: Notification) {
        try {
            nm.notify(NOTIFICATION_TAG + id, 1, n)
        } catch (e: SecurityException) {
            Log.w(TAG, "No notification permission", e)
        }
    }

    /** The add-on's two channels (created on first use, in the "Live" group): (live, alert). */
    private fun channels(context: Context, nm: NotificationManager, a: LiveActivity): Pair<String, String> {
        val source = a.source.ifBlank { a.addon }
        val live = "live_${a.addon}"
        val alert = "live_${a.addon}_alert"
        if (nm.getNotificationChannel(live) == null || nm.getNotificationChannel(alert) == null) {
            nm.createNotificationChannelGroup(NotificationChannelGroup(GROUP, context.getString(R.string.channel_group_live)))
            nm.createNotificationChannel(NotificationChannel(live, context.getString(R.string.channel_live, source), NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = context.getString(R.string.channel_live_description)
                group = GROUP
                setSound(null, null)
                enableVibration(false)
                setShowBadge(false)
            })
            nm.createNotificationChannel(NotificationChannel(alert, context.getString(R.string.channel_live_alert, source), NotificationManager.IMPORTANCE_HIGH).apply {
                description = context.getString(R.string.channel_live_alert_description)
                group = GROUP
            })
        }
        return live to alert
    }

    private fun tap(context: Context, id: String, open: String): PendingIntent {
        val intent = when (val target = openTarget(open, ServerConfig.load(context).url)) {
            is Open.Web -> Intent(Intent.ACTION_VIEW, target.url.toUri()).addCategory(Intent.CATEGORY_BROWSABLE)
            is Open.Screen -> Intent(context, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP).putExtra(EXTRA_OPEN_SCREEN, target.route)
            is Open.Session -> Intent(context, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP).putExtra(EXTRA_OPEN_SESSION, target.id)
            Open.Home -> Intent(context, MainActivity::class.java)
        }.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (intent.data == null) intent.data = intentUri(id, "open", "").toUri()  // a web tap's data is its page
        return PendingIntent.getActivity(context, "$id/open".hashCode(), intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    private fun broadcast(context: Context, id: String, action: String, kind: String): PendingIntent {
        val intent = Intent(context, LiveActivityReceiver::class.java).setAction(ACTION)
            .setData(intentUri(id, kind, action).toUri())
            .putExtra(EXTRA_ID, id).putExtra(EXTRA_ACTION, action).putExtra(EXTRA_KIND, kind)
        return PendingIntent.getBroadcast(context, "$id/$kind/$action".hashCode(), intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    private fun live(context: Context, nm: NotificationManager, a: LiveActivity, alert: Boolean, left: Long?): Notification {
        val look = a.look()
        val (chLive, chAlert) = channels(context, nm, a)
        val b = Notification.Builder(context, if (alert) chAlert else chLive)
            .setSmallIcon(icon(look.icon))
            .setColor(tone(look.tone))
            .setContentTitle(look.title)
            .setSubText(look.context.ifBlank { a.source })
            .setOngoing(true)
            .setOnlyAlertOnce(!alert)
            .setCategory(if (alert || look.tone == "bad") Notification.CATEGORY_REMINDER else Notification.CATEGORY_PROGRESS)
            .setContentIntent(tap(context, a.id, look.open))
            .setDeleteIntent(broadcast(context, a.id, "", KIND_DISMISS))
        if (look.text.isNotBlank()) b.setContentText(look.text)
        val to = look.countdown?.toMs
        val since = look.sinceMs
        when {
            to != null -> b.setWhen(to).setShowWhen(true).setUsesChronometer(true).setChronometerCountDown(true)
            since != null -> b.setWhen(since).setShowWhen(true).setUsesChronometer(true)
            else -> b.setShowWhen(false)
        }
        if (left != null) b.setTimeoutAfter(left)
        visibleActions(a).forEach { b.addAction(Notification.Action.Builder(null, it.label, broadcast(context, a.id, it.id, it.kind)).build()) }
        b.extras.putBoolean("android.requestPromotedOngoing", true)  // Notification.EXTRA_REQUEST_PROMOTED_ONGOING
        val progress = look.progress
        if (Build.VERSION.SDK_INT >= 36) {
            if (look.chip.isNotBlank()) b.setShortCriticalText(look.chip)
            if (progress != null) b.setStyle(progressStyle(context, progress))
        } else if (progress != null) {
            b.setProgress(progress.max, progress.value.coerceIn(0, progress.max), progress.indeterminate)
        }
        return b.build()
    }

    @androidx.annotation.RequiresApi(36)
    private fun progressStyle(context: Context, p: LiveProgress): Notification.ProgressStyle {
        val style = Notification.ProgressStyle()
            .setStyledByProgress(p.styledByProgress)
            .setProgressIndeterminate(p.indeterminate)
        if (p.segments.isNotEmpty()) {
            style.setProgressSegments(p.segments.map { Notification.ProgressStyle.Segment(it.length.coerceAtLeast(1)).setColor(tone(it.tone)) })
        }
        if (p.points.isNotEmpty()) {
            style.setProgressPoints(p.points.map { Notification.ProgressStyle.Point(it.at.coerceAtLeast(0)).setColor(tone(it.tone)) })
        }
        if (p.tracker.isNotBlank()) style.setProgressTrackerIcon(Icon.createWithResource(context, icon(p.tracker)))
        return style.setProgress(p.value.coerceAtLeast(0))
    }

    private fun final(context: Context, nm: NotificationManager, a: LiveActivity): Notification {
        val f = a.final ?: return live(context, nm, a, alert = false, left = null)
        val look = a.look()
        val (_, chAlert) = channels(context, nm, a)
        val b = Notification.Builder(context, chAlert)
            .setSmallIcon(icon(look.icon))
            .setColor(tone(f.tone))
            .setContentTitle(f.title.ifBlank { look.title })
            .setSubText(look.context.ifBlank { a.source })
            .setAutoCancel(true)
            .setCategory(if (f.tone == "bad") Notification.CATEGORY_ERROR else Notification.CATEGORY_STATUS)
            .setContentIntent(tap(context, a.id, f.open.ifBlank { look.open }))
        if (f.text.isNotBlank()) b.setContentText(f.text).setStyle(Notification.BigTextStyle().bigText(f.text))
        a.updatedMs?.let { b.setWhen(it).setShowWhen(true) }
        return b.build()
    }

    // The shown state and pending selections (prefs) and the last payload (a file).

    private val shownSerializer = MapSerializer(String.serializer(), Shown.serializer())
    private val pendingSerializer = MapSerializer(String.serializer(), String.serializer())

    private fun loadShown(context: Context): Map<String, Shown> =
        runCatching { ApiJson.decodeFromString(shownSerializer, ServerConfig.prefs(context).getString(KEY_SHOWN, null) ?: "{}") }
            .getOrDefault(emptyMap())

    private fun saveShown(context: Context, shown: Map<String, Shown>) =
        ServerConfig.prefs(context).edit { putString(KEY_SHOWN, ApiJson.encodeToString(shownSerializer, shown)) }

    private fun loadPending(context: Context): Map<String, String> = synchronized(lock) {
        runCatching { ApiJson.decodeFromString(pendingSerializer, ServerConfig.prefs(context).getString(KEY_PENDING, null) ?: "{}") }
            .getOrDefault(emptyMap())
    }

    private fun savePending(context: Context, pending: Map<String, String>) = synchronized(lock) {
        ServerConfig.prefs(context).edit { putString(KEY_PENDING, ApiJson.encodeToString(pendingSerializer, pending)) }
    }

    /** Forget the pending selection of [id] (only when it is still [key]; null: whatever it is). */
    private fun dropPending(context: Context, id: String, key: String?) = synchronized(lock) {
        val pending = loadPending(context)
        if (pending.containsKey(id) && (key == null || pending[id] == key)) savePending(context, pending - id)
    }

    private fun cacheFile(context: Context) = File(context.filesDir, CACHE_FILE)

    private fun loadCache(context: Context): List<LiveActivity> =
        runCatching { LiveApi.parse(cacheFile(context).readText()) }.getOrDefault(emptyList())

    private fun saveCache(context: Context, list: List<LiveActivity>) {
        try {
            cacheFile(context).writeText(LiveApi.encode(list))
        } catch (e: IOException) {
            Log.w(TAG, "Couldn't keep the live payload", e)
        }
    }
}

/** A live activity's buttons and its delete intent (not exported). */
class LiveActivityReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != LiveActivities.ACTION) return
        val id = intent.getStringExtra(LiveActivities.EXTRA_ID) ?: return
        val action = intent.getStringExtra(LiveActivities.EXTRA_ACTION).orEmpty()
        val kind = intent.getStringExtra(LiveActivities.EXTRA_KIND).orEmpty()
        val pending = goAsync()
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            try {
                when (kind) {
                    "next" -> LiveActivities.step(context, id, 1)
                    "prev" -> LiveActivities.step(context, id, -1)
                    "stop" -> LiveActivities.stop(context, id)
                    LiveActivities.KIND_DISMISS -> LiveActivities.dismissed(context, id)
                    else -> LiveActivities.act(context, id, action)
                }
            } catch (e: Exception) {
                Log.w("LiveActivities", "Button $kind/$action failed", e)
            } finally {
                pending.finish()
            }
        }
    }
}
