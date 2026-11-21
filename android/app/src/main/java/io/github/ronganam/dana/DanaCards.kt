package io.github.ronganam.dana

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import io.github.ronganam.dana.data.models.Briefing
import io.github.ronganam.dana.data.models.PersonCard
import io.github.ronganam.dana.data.wear.WearCards
import org.json.JSONObject

/** Opens the person's page (People screen) from a card, widget or notification. */
const val EXTRA_OPEN_PERSON_ID = "io.github.ronganam.dana.OPEN_PERSON_ID"
/** Opens the weekly review screen. */
const val EXTRA_OPEN_REVIEW = "io.github.ronganam.dana.OPEN_REVIEW"
/** Opens a screen by route ("chat", "tasks", "inbox", "calls", "people"); used by widgets. */
const val EXTRA_OPEN_SCREEN = "io.github.ronganam.dana.OPEN_SCREEN"

/**
 * Server pushes for the newer cards (server docs/people.md):
 * - `briefing`      → the morning briefing as an expandable notification, on the watch, in the widgets
 * - `context_card`  → a person's card before a meeting with them
 * - `weekly_review` → "Weekly review is ready" opening the review screen
 */
object DanaCards {
    private const val TAG = "DanaCards"
    private const val PREFS = "DanaPrefs"
    const val KEY_BRIEFING = "briefing_cache"

    private const val CH_BRIEFING = "dana_briefing"
    private const val CH_PEOPLE = "dana_people"
    private const val CH_REVIEW = "dana_review"

    private fun channel(nm: NotificationManager, id: String, name: String, importance: Int) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) nm.createNotificationChannel(NotificationChannel(id, name, importance))
    }

    private fun openApp(context: Context, code: Int, extras: Intent.() -> Unit): PendingIntent =
        PendingIntent.getActivity(
            context, code,
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP).apply(extras),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

    private fun post(context: Context, tag: String, id: Int, builder: NotificationCompat.Builder) {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        try {
            nm.notify(tag, id, builder.build())
        } catch (e: SecurityException) {
            Log.w(TAG, "No notification permission", e)
        }
    }

    // --- Briefing ---

    fun cachedBriefing(context: Context): Briefing? {
        val raw = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_BRIEFING, null) ?: return null
        return runCatching { Briefing.from(JSONObject(raw)) }.getOrNull()
    }

    fun storeBriefing(context: Context, b: Briefing) {
        storeBriefingQuietly(context, b)
        DanaWidgets.refresh(context)
    }

    /** Store without asking the widgets to refresh (used by the widget refresh itself). */
    fun storeBriefingQuietly(context: Context, b: Briefing) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY_BRIEFING, b.toJson().toString()).apply()
    }

    fun showBriefing(context: Context, d: JSONObject) {
        val b = Briefing.from(d)
        if (b.headline.isBlank()) return
        storeBriefing(context, b)
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        channel(nm, CH_BRIEFING, "Morning briefing", NotificationManager.IMPORTANCE_DEFAULT)
        val style = NotificationCompat.InboxStyle().setBigContentTitle("☀️ ${b.headline}")
        b.points.forEach { style.addLine(it) }
        post(context, "briefing", 1, NotificationCompat.Builder(context, CH_BRIEFING)
            .setSmallIcon(android.R.drawable.ic_menu_day)
            .setContentTitle("☀️ ${b.headline}")
            .setContentText(b.points.firstOrNull().orEmpty())
            .setStyle(style)
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setAutoCancel(true)
            .setContentIntent(openApp(context, 7101) { putExtra(EXTRA_OPEN_INBOX, false) }))
        WearCards.send(context, "briefing", "☀️ ${b.headline}", b.nextEvent.orEmpty(), b.points)
    }

    // --- Person card before a meeting ---

    fun showMeetingCard(context: Context, d: JSONObject) {
        val c = PersonCard.from(d.optJSONObject("card") ?: return)
        val event = d.optString("event_title")
        val mins = d.optInt("minutes")
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        channel(nm, CH_PEOPLE, "Before you meet someone", NotificationManager.IMPORTANCE_HIGH)
        val title = "${c.name} · $event"
        val lines = listOf(c.headline) + c.summaryLines()
        val style = NotificationCompat.InboxStyle().setBigContentTitle(title)
        lines.take(6).forEach { style.addLine(it) }
        post(context, "meeting:${c.personId}", 2, NotificationCompat.Builder(context, CH_PEOPLE)
            .setSmallIcon(android.R.drawable.ic_menu_my_calendar)
            .setContentTitle(title)
            .setContentText(if (mins > 0) "In $mins min · ${c.headline}" else c.headline)
            .setStyle(style)
            .setAutoCancel(true)
            .setTimeoutAfter(45 * 60 * 1000L)
            .setContentIntent(openApp(context, c.personId.hashCode()) { putExtra(EXTRA_OPEN_PERSON_ID, c.personId) }))
        WearCards.send(context, "meeting", c.name, "$event · ${c.headline}", c.summaryLines(), c.personId)
    }

    // --- Weekly review ---

    fun showReviewReady(context: Context, d: JSONObject) {
        val headline = d.optString("headline").ifBlank { "Weekly review" }
        val items = d.optInt("items")
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        channel(nm, CH_REVIEW, "Weekly review", NotificationManager.IMPORTANCE_DEFAULT)
        val summary = d.optJSONArray("summary")?.let { a -> (0 until a.length()).joinToString(" · ") { a.optString(it) } }.orEmpty()
        post(context, "weekly_review", 3, NotificationCompat.Builder(context, CH_REVIEW)
            .setSmallIcon(android.R.drawable.ic_menu_agenda)
            .setContentTitle("🗓️ Weekly review — $headline")
            .setContentText(if (items > 0) "$items things to decide. Tap to review." else "Nothing to decide this week.")
            .setStyle(NotificationCompat.BigTextStyle().bigText(listOf(summary, if (items > 0) "$items things to decide. Tap to review." else "").filter { it.isNotBlank() }.joinToString("\n")))
            .setAutoCancel(true)
            .setContentIntent(openApp(context, 7103) { putExtra(EXTRA_OPEN_REVIEW, true) }))
    }
}
