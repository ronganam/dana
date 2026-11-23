package io.github.ronganam.dana.wear.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import androidx.wear.compose.material.Text
import io.github.ronganam.dana.protocol.DanaSnapshot
import io.github.ronganam.dana.wear.R
import io.github.ronganam.dana.wear.ui.theme.DanaWear
import java.time.LocalDate
import java.time.format.TextStyle as DateStyle

/** What the watch's idle screen says ("06:52" in the showreel): a greeting, a name, up to three dot lines. */
data class WatchToday(val greeting: Greeting, val name: String?, val lines: List<Line>) {
    enum class Greeting { MORNING, AFTERNOON, EVENING, NIGHT }
    enum class Kind { EVENT, INBOX, TASK, OVERDUE }

    /** One dot line; [count] is set for [Kind.INBOX] (its text is a plural). */
    data class Line(val kind: Kind, val text: String, val count: Int = 0)

    companion object {
        private const val MAX_LINES = 3

        /** The greeting for [hour]: morning 5–11, afternoon 12–17, evening 18–22, else night. */
        fun greeting(hour: Int): Greeting = when (hour) {
            in 5..11 -> Greeting.MORNING
            in 12..17 -> Greeting.AFTERNOON
            in 18..22 -> Greeting.EVENING
            else -> Greeting.NIGHT
        }

        /**
         * From the phone's snapshot (null: none yet): the next events ("Standup
         * 09:30"), what needs you in the Inbox, then due tasks (overdue first
         * as the phone sorts them); three lines at most. [userName]'s first
         * word is the title.
         */
        fun build(snapshot: DanaSnapshot?, hour: Int, userName: String): WatchToday {
            val s = snapshot ?: DanaSnapshot()
            val lines = buildList {
                s.events.take(2).forEach { e -> add(Line(Kind.EVENT, listOf(e.title, e.time).filter { it.isNotBlank() }.joinToString(" "))) }
                s.inboxNeeds?.takeIf { it > 0 }?.let { add(Line(Kind.INBOX, "", it)) }
                s.tasks.forEach { t -> add(Line(if (t.due == "overdue") Kind.OVERDUE else Kind.TASK, t.title)) }
            }.filter { it.kind == Kind.INBOX || it.text.isNotBlank() }.take(MAX_LINES)
            return WatchToday(greeting(hour), userName.trim().split(Regex("\\s+")).firstOrNull()?.takeIf { it.isNotEmpty() }, lines)
        }
    }
}

/**
 * The idle screen's block, centred: the greeting in mono caps (ember text),
 * the name in Instrument Serif (the weekday when the name is unknown), then
 * the dot list (events sky, the Inbox teal, tasks amber, overdue ember).
 */
@Composable
fun TodayBlock(today: WatchToday, modifier: Modifier = Modifier) {
    val greeting = stringResource(
        when (today.greeting) {
            WatchToday.Greeting.MORNING -> R.string.greeting_morning
            WatchToday.Greeting.AFTERNOON -> R.string.greeting_afternoon
            WatchToday.Greeting.EVENING -> R.string.greeting_evening
            WatchToday.Greeting.NIGHT -> R.string.greeting_night
        }
    )
    val title = today.name ?: LocalDate.now().dayOfWeek.getDisplayName(DateStyle.FULL, androidx.compose.ui.platform.LocalLocale.current.platformLocale)
    Column(modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        Text(
            greeting.uppercase(),
            style = TextStyle(fontFamily = DanaWear.Mono, fontSize = 10.sp, letterSpacing = 0.16.em),
            color = DanaWear.EmberText,
            maxLines = 1
        )
        Text(
            title,
            style = TextStyle(fontFamily = DanaWear.Serif, fontSize = 32.sp, lineHeight = 34.sp, textDirection = TextDirection.Content),
            color = DanaWear.Ink,
            textAlign = TextAlign.Center,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.padding(top = 2.dp, bottom = 6.dp).semantics { heading() }
        )
        if (today.lines.isNotEmpty()) Column(Modifier.width(IntrinsicSize.Max), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            today.lines.forEach { line ->
                val text = if (line.kind == WatchToday.Kind.INBOX) pluralStringResource(R.plurals.today_needs_you, line.count, line.count) else line.text
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(7.dp).clip(CircleShape).background(line.kind.hue()))
                    Spacer(Modifier.width(7.dp))
                    Text(
                        text,
                        style = TextStyle(fontSize = 13.sp, textDirection = TextDirection.Content),
                        color = DanaWear.InkSoft,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            }
        }
    }
}

private fun WatchToday.Kind.hue(): Color = when (this) {
    WatchToday.Kind.EVENT -> DanaWear.Sky
    WatchToday.Kind.INBOX -> DanaWear.Teal
    WatchToday.Kind.TASK -> DanaWear.Amber
    WatchToday.Kind.OVERDUE -> DanaWear.Ember
}
