package io.github.ronganam.dana

import androidx.annotation.StringRes
import io.github.ronganam.dana.data.network.ServerConfig
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.app.NotificationCompat
import io.github.ronganam.dana.data.models.Briefing
import io.github.ronganam.dana.data.models.PersonCard
import io.github.ronganam.dana.data.wear.WearCards
import io.github.ronganam.dana.data.network.PeopleApi
import androidx.core.content.edit
import io.github.ronganam.dana.R

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
    const val KEY_BRIEFING = "briefing_cache"

    private const val CH_BRIEFING = "dana_briefing"
    private const val CH_PEOPLE = "dana_people"
    private const val CH_REVIEW = "dana_review"

    private fun channel(context: Context, nm: NotificationManager, id: String, @StringRes name: Int, importance: Int) {
        nm.createNotificationChannel(NotificationChannel(id, context.getString(name), importance))
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
        val raw = ServerConfig.prefs(context).getString(KEY_BRIEFING, null) ?: return null
        return PeopleApi.briefing(raw)
    }

    fun storeBriefing(context: Context, b: Briefing) {
        storeBriefingQuietly(context, b)
        DanaWidgets.refresh(context)
    }

    /** Store without asking the widgets to refresh (used by the widget refresh itself). */
    fun storeBriefingQuietly(context: Context, b: Briefing) {
        ServerConfig.prefs(context).edit { putString(KEY_BRIEFING, PeopleApi.briefingText(b)) }
    }

    fun showBriefing(context: Context, b: Briefing) {
        if (b.headline.isBlank()) return
        storeBriefing(context, b)
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        channel(context, nm, CH_BRIEFING, R.string.channel_briefing, NotificationManager.IMPORTANCE_DEFAULT)
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

    /** The `context_card` push: [c] before a meeting called [event], [mins] from now. */
    fun showMeetingCard(context: Context, c: PersonCard, event: String, mins: Int) {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        channel(context, nm, CH_PEOPLE, R.string.channel_people, NotificationManager.IMPORTANCE_HIGH)
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

    /** The `weekly_review` push: [items] things to decide, [summaryLines] the review's summary. */
    fun showReviewReady(context: Context, headline: String, items: Int, summaryLines: List<String>) {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        channel(context, nm, CH_REVIEW, R.string.channel_review, NotificationManager.IMPORTANCE_DEFAULT)
        val summary = summaryLines.joinToString(" · ")
        post(context, "weekly_review", 3, NotificationCompat.Builder(context, CH_REVIEW)
            .setSmallIcon(android.R.drawable.ic_menu_agenda)
            .setContentTitle("🗓️ Weekly review — $headline")
            .setContentText(if (items > 0) "$items things to decide. Tap to review." else "Nothing to decide this week.")
            .setStyle(NotificationCompat.BigTextStyle().bigText(listOf(summary, if (items > 0) "$items things to decide. Tap to review." else "").filter { it.isNotBlank() }.joinToString("\n")))
            .setAutoCancel(true)
            .setContentIntent(openApp(context, 7103) { putExtra(EXTRA_OPEN_REVIEW, true) }))
    }
}
