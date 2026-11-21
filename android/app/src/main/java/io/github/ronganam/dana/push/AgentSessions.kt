package io.github.ronganam.dana.push

import io.github.ronganam.dana.data.network.ServerConfig
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.Build
import android.util.Log
import io.github.ronganam.dana.MainActivity
import io.github.ronganam.dana.R
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.text
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.put
import java.time.OffsetDateTime
import androidx.core.content.edit
import androidx.core.net.toUri

/**
 * The user's coding-agent sessions (Claude Code, Codex) on the laptop, live
 * (server: server/addons/phone/agent_sessions.py). Each `agent_sessions` push
 * carries the whole list, so [show] makes the shade match it:
 *
 * - running / waiting → one ongoing notification per session, asked to be
 *   promoted to an Android 16 Live Update (status-bar chip, the Now Bar on
 *   Samsung): what it does now, plan steps as a segmented progress bar, a
 *   timer since the prompt. Silent while running; "Needs you" alerts once.
 * - done / failed → the same notification turns into a normal one that
 *   alerts once (per `rev`, the server's status-change counter).
 * - a running session missing from the list (ended, gone silent) → cancelled.
 * - subagents (Claude Code's own, delegated child sessions) come inside
 *   their parent's entry (`subagents`; the server folds the group's status
 *   into the parent's): one segment each on the parent's progress bar
 *   (green done, blue running, orange waiting, red failed) when it has no
 *   plan, "2/3 subagents done" beside it, and their results in the finished
 *   notification. A group that stops being live is posted finished even when
 *   its `rev` didn't change.
 *
 * Live notifications also time out by themselves after an hour without an
 * update, like the server's stale rule, so a laptop that went to sleep can't
 * leave one behind.
 */
object AgentSessions {
    private const val TAG = "AgentSessions"
    private const val KEY_SHOWN = "agent_sessions_shown"
    private const val CH_LIVE = "agent_sessions_live"
    private const val CH_ALERT = "agent_sessions_alert"
    private const val NOTIFICATION_TAG = "agent_session:"
    private const val STALE_MS = 60L * 60 * 1000
    const val ACTION_HIDE = "io.github.ronganam.dana.AGENT_SESSION_HIDE"
    const val EXTRA_ID = "agent_session_id"

    /** Pushed text the way the server's other clients read it ("" when absent or null). */
    private fun JsonObject.str(key: String): String = text(key).orEmpty()

    /** One of a session's subagents. */
    data class Sub(val id: String, val title: String, val status: String, val activity: String, val summary: String) {
        val finished get() = status == "done" || status == "failed"

        companion object {
            fun from(o: JsonObject): Sub? {
                val id = o.str("id").takeIf { it.isNotBlank() } ?: return null
                return Sub(id, o.str("title").ifBlank { "Subagent" }, o.str("status"), o.str("activity"), o.str("summary"))
            }
        }
    }

    data class Session(
        val id: String, val agent: String, val host: String, val project: String, val title: String,
        val status: String, val activity: String, val summary: String, val stepsDone: Int, val stepsTotal: Int,
        val rev: Int, val startedMs: Long, val updatedMs: Long, val openUrl: String = "",
        val subagents: List<Sub> = emptyList(),
    ) {
        val live get() = status == "running" || status == "waiting"
        val agentName get() = when (agent) { "claude" -> "Claude"; "codex" -> "Codex"; else -> agent }
        /** "2/3 subagents done", or empty without subagents. */
        val subsLine get() = if (subagents.isEmpty()) "" else
            "${subagents.count { it.finished }}/${subagents.size} subagent${if (subagents.size == 1) "" else "s"} done"

        companion object {
            fun from(o: JsonObject): Session? {
                val id = o.str("id").takeIf { it.isNotBlank() } ?: return null
                return Session(
                    id, o.str("agent"), o.str("host"), o.str("project"), o.str("title"),
                    o.str("status"), o.str("activity"), o.str("summary"),
                    o.int("steps_done"), o.int("steps_total"), o.int("rev"),
                    millis(o.str("started_at")), millis(o.str("updated_at")),
                    o.str("open_url").takeIf { it.startsWith("https://") }.orEmpty(),
                    (o["subagents"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonObject)?.let(Sub::from) },
                )
            }

            private fun JsonObject.int(key: String): Int = (this[key] as? JsonPrimitive)?.intOrNull ?: 0

            private fun millis(iso: String): Long =
                runCatching { OffsetDateTime.parse(iso).toInstant().toEpochMilli() }.getOrDefault(System.currentTimeMillis())
        }
    }

    /** What [show] does with one push: post live / alerting-live / finished, cancel the vanished. */
    sealed class Step {
        data class Live(val session: Session, val alert: Boolean) : Step()
        data class Finished(val session: Session) : Step()
        data class Cancel(val id: String) : Step()
    }

    /** What was shown per session id: the `rev` last posted and whether it was live. */
    data class Shown(val rev: Int, val live: Boolean)

    /**
     * The steps that make the shade match [sessions], given what was [shown]:
     * live ones are re-posted (alerting only when one turns to "waiting"),
     * finished ones are posted once per `rev` (and when one shown live stops
     * being live), live ones no longer listed are cancelled.
     */
    fun plan(shown: Map<String, Shown>, sessions: List<Session>): List<Step> {
        val steps = mutableListOf<Step>()
        for (s in sessions) {
            val was = shown[s.id]
            val changed = was?.rev != s.rev
            when {
                s.live -> steps += Step.Live(s, alert = changed && s.status == "waiting")
                changed || was.live -> steps += Step.Finished(s)
            }
        }
        val ids = sessions.map { it.id }.toSet()
        shown.forEach { (id, was) -> if (id !in ids && was.live) steps += Step.Cancel(id) }
        return steps
    }

    /** Make the shade match the pushed list (`data.sessions`). */
    fun show(context: Context, data: JsonObject) {
        val list = data["sessions"] as? JsonArray ?: return
        val sessions = list.mapNotNull { (it as? JsonObject)?.let(Session::from) }
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        channels(nm)
        val prefs = ServerConfig.prefs(context)
        for (step in plan(loadShown(prefs.getString(KEY_SHOWN, null)), sessions)) {
            when (step) {
                is Step.Live -> post(nm, step.session, live(context, step.session, step.alert))
                is Step.Finished -> post(nm, step.session, finished(context, step.session))
                is Step.Cancel -> nm.cancel(NOTIFICATION_TAG + step.id, 1)
            }
        }
        val next = buildJsonObject {
            sessions.forEach { put(it.id, buildJsonObject { put("rev", it.rev); put("live", it.live) }) }
        }
        prefs.edit { putString(KEY_SHOWN, next.toString()) }
    }

    private fun loadShown(raw: String?): Map<String, Shown> {
        return shownMap(raw).mapValues { (_, v) ->
            val o = v as? JsonObject
            Shown((o?.get("rev") as? JsonPrimitive)?.intOrNull ?: 0, (o?.get("live") as? JsonPrimitive)?.booleanOrNull ?: false)
        }
    }

    private fun shownMap(raw: String?): JsonObject =
        runCatching { ApiJson.parseToJsonElement(raw ?: "{}") as? JsonObject }.getOrNull() ?: JsonObject(emptyMap())

    private fun channels(nm: NotificationManager) {
        nm.createNotificationChannel(NotificationChannel(CH_LIVE, "Coding sessions (live)", NotificationManager.IMPORTANCE_DEFAULT).apply {
            description = "Claude Code and Codex sessions while they run"
            setSound(null, null)
            enableVibration(false)
            setShowBadge(false)
        })
        nm.createNotificationChannel(NotificationChannel(CH_ALERT, "Coding sessions (finished / needs you)", NotificationManager.IMPORTANCE_HIGH).apply {
            description = "A coding session finished, failed or is waiting for you"
        })
    }

    private fun post(nm: NotificationManager, s: Session, n: Notification) {
        try {
            nm.notify(NOTIFICATION_TAG + s.id, 1, n)
        } catch (e: SecurityException) {
            Log.w(TAG, "No notification permission", e)
        }
    }

    /**
     * Tapping a session: the `open_url` the server sent (https only), handed to
     * whichever app handles that link (an app that verified the link's domain,
     * else the browser); without one, Dana.
     */
    private fun tapIntent(context: Context, s: Session): PendingIntent {
        val intent = if (s.openUrl.isNotEmpty()) {
            Intent(Intent.ACTION_VIEW, s.openUrl.toUri()).addCategory(Intent.CATEGORY_BROWSABLE)
        } else {
            Intent(context, MainActivity::class.java)
        }
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return PendingIntent.getActivity(context, s.id.hashCode(), intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    private fun hide(context: Context, s: Session): Notification.Action {
        val intent = Intent(context, AgentSessionReceiver::class.java).setAction(ACTION_HIDE).putExtra(EXTRA_ID, s.id)
        val pi = PendingIntent.getBroadcast(context, s.id.hashCode(), intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        return Notification.Action.Builder(null, "Hide", pi).build()
    }

    private fun where(s: Session) = listOf(s.agentName, s.project).filter { it.isNotBlank() }.joinToString(" · ")

    private fun subColor(sub: Sub) = when (sub.status) {
        "done" -> Color.rgb(0x3D, 0xB8, 0x6B)
        "failed" -> Color.rgb(0xE5, 0x48, 0x4D)
        "waiting" -> Color.rgb(0xFF, 0x6A, 0x3D)
        else -> Color.rgb(0x5B, 0x8D, 0xEF)
    }

    private fun live(context: Context, s: Session, alert: Boolean): Notification {
        val waiting = s.status == "waiting"
        val steps = if (s.stepsTotal > 0) "${s.stepsDone}/${s.stepsTotal}" else ""
        val text = listOf(s.activity, steps.takeIf { it.isNotEmpty() }?.let { "step $it" }, s.subsLine.ifEmpty { null })
            .filterNot { it.isNullOrBlank() }.joinToString(" · ")
        val b = Notification.Builder(context, if (alert) CH_ALERT else CH_LIVE)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(if (waiting) "Needs you · ${s.title.ifBlank { s.project }}" else s.title.ifBlank { s.project })
            .setContentText(text)
            .setSubText(where(s))
            .setWhen(s.startedMs).setShowWhen(true).setUsesChronometer(true)
            .setOngoing(true)
            .setOnlyAlertOnce(!alert)
            .setCategory(if (waiting) Notification.CATEGORY_REMINDER else Notification.CATEGORY_PROGRESS)
            .setTimeoutAfter(STALE_MS)
            .setContentIntent(tapIntent(context, s))
            .addAction(hide(context, s))
        b.extras.putBoolean("android.requestPromotedOngoing", true)  // Notification.EXTRA_REQUEST_PROMOTED_ONGOING
        if (Build.VERSION.SDK_INT >= 36) {
            val subs = if (s.subagents.isEmpty()) "" else "${s.subagents.count { it.finished }}/${s.subagents.size}"
            b.setShortCriticalText(if (waiting) "Needs you" else steps.ifEmpty { subs.ifEmpty { s.agentName } })
            val style = Notification.ProgressStyle()
            if (s.stepsTotal > 0) {
                style.setProgressSegments((1..s.stepsTotal).map {
                    Notification.ProgressStyle.Segment(1).setColor(if (waiting) Color.rgb(0xFF, 0x6A, 0x3D) else Color.rgb(0x5B, 0x8D, 0xEF))
                }).setProgress(s.stepsDone)
            } else if (s.subagents.isNotEmpty()) {
                // Finished subagents first, so the filled part of the bar is what's done.
                val ordered = s.subagents.sortedBy { if (it.finished) 0 else 1 }
                style.setProgressSegments(ordered.map { Notification.ProgressStyle.Segment(1).setColor(subColor(it)) })
                    .setProgress(ordered.count { it.finished })
            } else {
                style.setProgressIndeterminate(!waiting)
            }
            b.setStyle(style)
        } else if (s.stepsTotal > 0) {
            b.setProgress(s.stepsTotal, s.stepsDone, false)
        } else if (s.subagents.isNotEmpty()) {
            b.setProgress(s.subagents.size, s.subagents.count { it.finished }, false)
        } else if (!waiting) {
            b.setProgress(0, 0, true)
        }
        return b.build()
    }

    private fun finished(context: Context, s: Session): Notification {
        val failed = s.status == "failed"
        val minutes = ((s.updatedMs - s.startedMs) / 60_000).coerceAtLeast(0)
        val took = if (minutes < 1) "under a minute" else "${minutes}m"
        val subs = s.subagents.joinToString("\n") { sub ->
            val mark = when (sub.status) { "done" -> "✓"; "failed" -> "✗"; else -> "…" }
            "$mark ${sub.title}" + (sub.summary.takeIf { it.isNotBlank() }?.let { ": $it" } ?: "")
        }
        val body = listOf(s.summary.ifBlank { s.title }, subs).filter { it.isNotBlank() }.joinToString("\n\n")
        return Notification.Builder(context, CH_ALERT)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(if (failed) "Failed · ${s.title.ifBlank { s.project }}" else "Done · ${s.title.ifBlank { s.project }}")
            .setContentText(body)
            .setStyle(Notification.BigTextStyle().bigText(body))
            .setSubText("${where(s)} · $took")
            .setWhen(s.updatedMs).setShowWhen(true)
            .setAutoCancel(true)
            .setCategory(if (failed) Notification.CATEGORY_ERROR else Notification.CATEGORY_STATUS)
            .setContentIntent(tapIntent(context, s))
            .build()
    }

    /** "Hide" on a live notification: forget the session on the server, drop it here. */
    internal fun hideNow(context: Context, id: String) {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.cancel(NOTIFICATION_TAG + id, 1)
        val prefs = ServerConfig.prefs(context)
        val shown = JsonObject(shownMap(prefs.getString(KEY_SHOWN, "{}")) - id)
        prefs.edit { putString(KEY_SHOWN, shown.toString()) }
    }
}

/** The live notification's Hide button. */
class AgentSessionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != AgentSessions.ACTION_HIDE) return
        val id = intent.getStringExtra(AgentSessions.EXTRA_ID) ?: return
        AgentSessions.hideNow(context, id)
        val pending = goAsync()
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            try {
                ServerConfig.load(context).api().devices.hideAgentSession(id)
            } catch (e: Exception) {
                Log.w("AgentSessions", "Couldn't hide session on the server", e)
            } finally {
                pending.finish()
            }
        }
    }
}
