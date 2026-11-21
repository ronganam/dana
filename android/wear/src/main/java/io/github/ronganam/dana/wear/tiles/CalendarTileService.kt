package io.github.ronganam.dana.wear.tiles

import androidx.wear.protolayout.ActionBuilders
import androidx.wear.protolayout.ColorBuilders
import androidx.wear.protolayout.DimensionBuilders
import androidx.wear.protolayout.LayoutElementBuilders
import androidx.wear.protolayout.LayoutElementBuilders.Box
import androidx.wear.protolayout.LayoutElementBuilders.Column
import androidx.wear.protolayout.LayoutElementBuilders.Row
import androidx.wear.protolayout.LayoutElementBuilders.Spacer
import androidx.wear.protolayout.ModifiersBuilders
import androidx.wear.protolayout.ResourceBuilders
import androidx.wear.protolayout.TimelineBuilders
import androidx.wear.tiles.RequestBuilders
import androidx.wear.tiles.TileBuilders.Tile
import androidx.wear.tiles.TileService
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import io.github.ronganam.dana.wear.data.WearPrefs
import org.json.JSONArray
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import kotlin.math.sqrt

class CalendarTileService : TileService() {

    companion object {
        private const val RESOURCES_VERSION = "1"
        private const val MAX_EVENTS_PER_DAY = 3
        private const val SCREEN_RADIUS_DP = 107.5f
        private const val ROW_HEIGHT_DP = 16f
        private const val MIN_PAD_DP = 6f
    }

    override fun onTileRequest(
        requestParams: RequestBuilders.TileRequest
    ): ListenableFuture<Tile> {
        val prefs = WearPrefs(applicationContext)
        val events = parseFilteredEvents(prefs.cachedEvents)
        val today = Calendar.getInstance()
        val tomorrow = Calendar.getInstance().apply { add(Calendar.DAY_OF_MONTH, 1) }

        val todayEvents = events.filter { isSameDay(it.startTime, today) }
        val tomorrowEvents = events.filter { isSameDay(it.startTime, tomorrow) }

        val layout = buildLayout(todayEvents, tomorrowEvents)

        val tile = Tile.Builder()
            .setResourcesVersion(RESOURCES_VERSION)
            .setFreshnessIntervalMillis(15 * 60 * 1000L)
            .setTileTimeline(TimelineBuilders.Timeline.fromLayoutElement(layout))
            .build()

        return Futures.immediateFuture(tile)
    }

    override fun onTileResourcesRequest(
        requestParams: RequestBuilders.ResourcesRequest
    ): ListenableFuture<ResourceBuilders.Resources> {
        return Futures.immediateFuture(
            ResourceBuilders.Resources.Builder()
                .setVersion(RESOURCES_VERSION)
                .build()
        )
    }

    private fun buildLayout(
        todayEvents: List<CalendarEventItem>,
        tomorrowEvents: List<CalendarEventItem>
    ): Box {
        val todayList = todayEvents.take(MAX_EVENTS_PER_DAY)
        val tomorrowList = tomorrowEvents.take(MAX_EVENTS_PER_DAY)

        val totalRows = 1 +
                todayList.size.coerceAtLeast(1) +
                1 +
                tomorrowList.size.coerceAtLeast(1) +
                (if (todayEvents.size > MAX_EVENTS_PER_DAY) 1 else 0) +
                (if (tomorrowEvents.size > MAX_EVENTS_PER_DAY) 1 else 0)

        val column = Column.Builder()
            .setWidth(DimensionBuilders.expand())
            .setHeight(DimensionBuilders.wrap())

        var row = 0

        column.addContent(buildDayHeader("TODAY", COLOR_TEAL, row++, totalRows))

        if (todayList.isEmpty()) {
            column.addContent(buildPaddedText("  No events", COLOR_LIGHT_GRAY, 11f, row++, totalRows))
        } else {
            todayList.forEach { event ->
                column.addContent(buildEventRow(event, COLOR_TEAL, row++, totalRows))
            }
            if (todayEvents.size > MAX_EVENTS_PER_DAY) {
                column.addContent(buildPaddedText(
                    "  +${todayEvents.size - MAX_EVENTS_PER_DAY} more",
                    COLOR_LIGHT_GRAY, 10f, row++, totalRows
                ))
            }
        }

        column.addContent(Spacer.Builder().setHeight(DimensionBuilders.dp(3f)).build())

        column.addContent(buildDayHeader("TOMORROW", COLOR_GRAY, row++, totalRows))

        if (tomorrowList.isEmpty()) {
            column.addContent(buildPaddedText("  No events", COLOR_LIGHT_GRAY, 11f, row++, totalRows))
        } else {
            tomorrowList.forEach { event ->
                column.addContent(buildEventRow(event, COLOR_GRAY, row++, totalRows))
            }
            if (tomorrowEvents.size > MAX_EVENTS_PER_DAY) {
                column.addContent(buildPaddedText(
                    "  +${tomorrowEvents.size - MAX_EVENTS_PER_DAY} more",
                    COLOR_LIGHT_GRAY, 10f, row++, totalRows
                ))
            }
        }

        val clickable = ModifiersBuilders.Clickable.Builder()
            .setOnClick(
                ActionBuilders.LaunchAction.Builder()
                    .setAndroidActivity(
                        ActionBuilders.AndroidActivity.Builder()
                            .setPackageName("io.github.ronganam.dana")
                            .setClassName("io.github.ronganam.dana.wear.MainActivity")
                            .build()
                    )
                    .build()
            )
            .build()

        return Box.Builder()
            .setWidth(DimensionBuilders.expand())
            .setHeight(DimensionBuilders.expand())
            .setVerticalAlignment(LayoutElementBuilders.VERTICAL_ALIGN_CENTER)
            .setModifiers(
                ModifiersBuilders.Modifiers.Builder()
                    .setClickable(clickable)
                    .build()
            )
            .addContent(column.build())
            .build()
    }

    private fun rowPadding(row: Int, total: Int): ModifiersBuilders.Modifiers {
        val padDp = if (total <= 1) MIN_PAD_DP else {
            val y = (row - (total - 1) / 2f) * ROW_HEIGHT_DP
            val y2 = y * y
            val r2 = SCREEN_RADIUS_DP * SCREEN_RADIUS_DP
            if (y2 >= r2) SCREEN_RADIUS_DP
            else maxOf(MIN_PAD_DP, SCREEN_RADIUS_DP - sqrt(r2 - y2))
        }
        return ModifiersBuilders.Modifiers.Builder()
            .setPadding(
                ModifiersBuilders.Padding.Builder()
                    .setStart(DimensionBuilders.dp(padDp))
                    .setEnd(DimensionBuilders.dp(padDp))
                    .build()
            )
            .build()
    }

    private fun buildDayHeader(text: String, color: Int, row: Int, total: Int): Row {
        return Row.Builder()
            .setWidth(DimensionBuilders.expand())
            .setModifiers(rowPadding(row, total))
            .addContent(
                LayoutElementBuilders.Text.Builder()
                    .setText(text)
                    .setFontStyle(
                        LayoutElementBuilders.FontStyle.Builder()
                            .setSize(DimensionBuilders.sp(12f))
                            .setColor(ColorBuilders.argb(color))
                            .setWeight(LayoutElementBuilders.FONT_WEIGHT_BOLD)
                            .build()
                    )
                    .build()
            )
            .build()
    }

    private fun buildEventRow(event: CalendarEventItem, accentColor: Int, row: Int, total: Int): Row {
        val timeStr = formatEventTime(event.startTime)
        val title = truncateTitle(event.title, 32)
        return Row.Builder()
            .setWidth(DimensionBuilders.expand())
            .setModifiers(rowPadding(row, total))
            .addContent(
                LayoutElementBuilders.Text.Builder()
                    .setText(timeStr)
                    .setFontStyle(
                        LayoutElementBuilders.FontStyle.Builder()
                            .setSize(DimensionBuilders.sp(12f))
                            .setColor(ColorBuilders.argb(accentColor))
                            .setWeight(LayoutElementBuilders.FONT_WEIGHT_BOLD)
                            .build()
                    )
                    .build()
            )
            .addContent(
                LayoutElementBuilders.Text.Builder()
                    .setText(" $title")
                    .setFontStyle(
                        LayoutElementBuilders.FontStyle.Builder()
                            .setSize(DimensionBuilders.sp(12f))
                            .setColor(ColorBuilders.argb(COLOR_WHITE))
                            .build()
                    )
                    .build()
            )
            .build()
    }

    private fun buildPaddedText(text: String, color: Int, sizeSp: Float, row: Int, total: Int): LayoutElementBuilders.Text {
        return LayoutElementBuilders.Text.Builder()
            .setText(text)
            .setModifiers(rowPadding(row, total))
            .setFontStyle(
                LayoutElementBuilders.FontStyle.Builder()
                    .setSize(DimensionBuilders.sp(sizeSp))
                    .setColor(ColorBuilders.argb(color))
                    .build()
            )
            .build()
    }

    private fun truncateTitle(title: String, maxLen: Int): String {
        val cleaned = title
            .replace("Canceled: ", "")
            .replace("Daily Vikings / Core / SRE", "Vikings")
            .replace("SRE Overview", "SRE")
            .trim()
        return if (cleaned.length > maxLen) cleaned.substring(0, maxLen - 1) + ".." else cleaned
    }

    private fun parseFilteredEvents(json: String): List<CalendarEventItem> {
        val result = mutableListOf<CalendarEventItem>()
        try {
            val array = JSONArray(json)
            val now = Date().time
            val graceMs = 30 * 60 * 1000L
            val twoDaysLater = Calendar.getInstance().apply {
                add(Calendar.DAY_OF_MONTH, 2)
                set(Calendar.HOUR_OF_DAY, 23)
                set(Calendar.MINUTE, 59)
                set(Calendar.SECOND, 59)
            }.timeInMillis

            for (i in 0 until array.length()) {
                val obj = array.getJSONObject(i)
                if (obj.optBoolean("all_day", false)) continue

                val startStr = obj.optString("start", "")
                val endStr = obj.optString("end", "")

                val startMs = parseIsoTime(startStr) ?: continue
                val endMs = if (endStr.isNotEmpty()) parseIsoTime(endStr) else null

                if (endMs != null && now > endMs + graceMs) continue
                if (startMs > twoDaysLater) continue

                result.add(
                    CalendarEventItem(
                        title = obj.optString("summary", ""),
                        startTime = startStr
                    )
                )
            }
        } catch (e: org.json.JSONException) {
            android.util.Log.w("CalendarTile", "Cached events aren't valid JSON", e)
        }
        return result.sortedBy { parseIsoTime(it.startTime) ?: 0L }
    }

    private fun isSameDay(isoDate: String, calendar: Calendar): Boolean {
        val ms = parseIsoTime(isoDate) ?: return false
        val eventCal = Calendar.getInstance().apply { timeInMillis = ms }
        return eventCal.get(Calendar.YEAR) == calendar.get(Calendar.YEAR) &&
                eventCal.get(Calendar.DAY_OF_YEAR) == calendar.get(Calendar.DAY_OF_YEAR)
    }

    private fun formatEventTime(isoDate: String): String {
        val ms = parseIsoTime(isoDate) ?: return "--"
        val cal = Calendar.getInstance().apply { timeInMillis = ms }
        val h = cal.get(Calendar.HOUR_OF_DAY)
        val m = cal.get(Calendar.MINUTE)
        return if (m == 0) "$h" else String.format(Locale.US, "%d:%02d", h, m)
    }

    private fun parseIsoTime(s: String): Long? {
        val formats = listOf(
            "yyyy-MM-dd'T'HH:mm:ss'Z'",
            "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'",
            "yyyy-MM-dd'T'HH:mm:ssZ",
            "yyyy-MM-dd'T'HH:mm:ss.SSSZ",
            "yyyy-MM-dd'T'HH:mm:ssXXX",
            "yyyy-MM-dd'T'HH:mm:ss.SSSXXX"
        )
        for (fmt in formats) {
            try {
                val sdf = SimpleDateFormat(fmt, Locale.US).apply {
                    timeZone = TimeZone.getTimeZone("UTC")
                }
                val date = sdf.parse(s) ?: continue
                return date.time
            } catch (_: java.text.ParseException) {
                // Not this format: try the next one.
            }
        }
        return null
    }

    private data class CalendarEventItem(
        val title: String,
        val startTime: String
    )
}
