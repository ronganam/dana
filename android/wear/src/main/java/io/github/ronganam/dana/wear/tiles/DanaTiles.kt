package io.github.ronganam.dana.wear.tiles

import androidx.wear.protolayout.ActionBuilders
import androidx.wear.protolayout.ColorBuilders
import androidx.wear.protolayout.DimensionBuilders
import androidx.wear.protolayout.LayoutElementBuilders
import androidx.wear.protolayout.ModifiersBuilders
import androidx.wear.protolayout.ResourceBuilders
import androidx.wear.protolayout.TimelineBuilders
import androidx.wear.tiles.RequestBuilders
import androidx.wear.tiles.TileBuilders.Tile
import androidx.wear.tiles.TileService
import io.github.ronganam.dana.wear.cards.DanaSnapshot
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Small protolayout helpers shared by the Today and Waiting tiles. */
internal object DanaTile {
    const val RESOURCES_VERSION = "1"

    fun text(s: String, color: Int, size: Float, bold: Boolean = false, lines: Int = 1): LayoutElementBuilders.Text =
        LayoutElementBuilders.Text.Builder()
            .setText(s)
            .setMaxLines(lines)
            .setOverflow(LayoutElementBuilders.TEXT_OVERFLOW_ELLIPSIZE)
            .setMultilineAlignment(LayoutElementBuilders.TEXT_ALIGN_CENTER)
            .setFontStyle(
                LayoutElementBuilders.FontStyle.Builder()
                    .setSize(DimensionBuilders.sp(size))
                    .setColor(ColorBuilders.argb(color))
                    .setWeight(if (bold) LayoutElementBuilders.FONT_WEIGHT_BOLD else LayoutElementBuilders.FONT_WEIGHT_NORMAL)
                    .build()
            )
            .build()

    fun gap(dp: Float) = LayoutElementBuilders.Spacer.Builder().setHeight(DimensionBuilders.dp(dp)).build()

    fun launch(className: String, id: String, extras: Map<String, String> = emptyMap()): ModifiersBuilders.Clickable {
        val activity = ActionBuilders.AndroidActivity.Builder()
            .setPackageName("io.github.ronganam.dana")
            .setClassName(className)
        extras.forEach { (k, v) -> activity.addKeyToExtraMapping(k, ActionBuilders.AndroidStringExtra.Builder().setValue(v).build()) }
        return ModifiersBuilders.Clickable.Builder()
            .setId(id)
            .setOnClick(ActionBuilders.LaunchAction.Builder().setAndroidActivity(activity.build()).build())
            .build()
    }

    /** Full-screen box, content centred with enough side padding for a round screen. */
    fun screen(content: LayoutElementBuilders.LayoutElement, onClick: ModifiersBuilders.Clickable?): LayoutElementBuilders.Box {
        val mods = ModifiersBuilders.Modifiers.Builder()
            .setPadding(
                ModifiersBuilders.Padding.Builder()
                    .setStart(DimensionBuilders.dp(22f)).setEnd(DimensionBuilders.dp(22f))
                    .setTop(DimensionBuilders.dp(18f)).setBottom(DimensionBuilders.dp(18f))
                    .build()
            )
        onClick?.let { mods.setClickable(it) }
        return LayoutElementBuilders.Box.Builder()
            .setWidth(DimensionBuilders.expand())
            .setHeight(DimensionBuilders.expand())
            .setHorizontalAlignment(LayoutElementBuilders.HORIZONTAL_ALIGN_CENTER)
            .setVerticalAlignment(LayoutElementBuilders.VERTICAL_ALIGN_CENTER)
            .setModifiers(mods.build())
            .addContent(content)
            .build()
    }

    fun tile(layout: LayoutElementBuilders.LayoutElement, freshnessMs: Long = 30 * 60 * 1000L): Tile =
        Tile.Builder()
            .setResourcesVersion(RESOURCES_VERSION)
            .setFreshnessIntervalMillis(freshnessMs)
            .setTileTimeline(TimelineBuilders.Timeline.fromLayoutElement(layout))
            .build()

    fun resources(): ListenableFuture<ResourceBuilders.Resources> =
        Futures.immediateFuture(ResourceBuilders.Resources.Builder().setVersion(RESOURCES_VERSION).build())

    fun column() = LayoutElementBuilders.Column.Builder()
        .setWidth(DimensionBuilders.expand())
        .setHeight(DimensionBuilders.wrap())
        .setHorizontalAlignment(LayoutElementBuilders.HORIZONTAL_ALIGN_CENTER)
}

/**
 * Today: the morning briefing's headline (today's only), what's next on the
 * calendar and the top due tasks. Fed by the phone's widget snapshot.
 */
class TodayTileService : TileService() {

    override fun onTileRequest(requestParams: RequestBuilders.TileRequest): ListenableFuture<Tile> =
        Futures.immediateFuture(DanaTile.tile(build(DanaSnapshot.load(applicationContext))))

    override fun onTileResourcesRequest(requestParams: RequestBuilders.ResourcesRequest): ListenableFuture<ResourceBuilders.Resources> =
        DanaTile.resources()

    private fun build(s: JSONObject): LayoutElementBuilders.LayoutElement {
        val today = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
        val col = DanaTile.column()
        col.addContent(DanaTile.text("TODAY", COLOR_ORANGE, 11f, bold = true))
        col.addContent(DanaTile.gap(2f))

        val headline = s.optString("briefing_headline").takeIf { it.isNotBlank() && s.optString("briefing_date") == today }
        if (headline != null) {
            col.addContent(DanaTile.text(headline, COLOR_WHITE, 13f, bold = true, lines = 2))
            col.addContent(DanaTile.gap(4f))
        }

        val events = s.optJSONArray("events")
        var shown = 0
        if (events != null) {
            for (i in 0 until minOf(2, events.length())) {
                val e = events.optJSONObject(i) ?: continue
                col.addContent(DanaTile.text("${e.optString("time")}  ${e.optString("title")}", COLOR_TEAL, 12f))
                shown++
            }
        }
        val tasks = s.optJSONArray("tasks")
        if (tasks != null) {
            val max = if (headline != null) 2 else 3
            for (i in 0 until minOf(max, tasks.length())) {
                val t = tasks.optJSONObject(i) ?: continue
                val due = t.optString("due")
                val color = if (due == "overdue") COLOR_RED else COLOR_LIGHT_GRAY
                col.addContent(DanaTile.text("• ${t.optString("title")}", color, 12f))
                shown++
            }
        }
        if (headline == null && shown == 0) {
            col.addContent(DanaTile.text(if (s.length() == 0) "Open Dana on the phone" else "Nothing due. Enjoy.", COLOR_LIGHT_GRAY, 12f, lines = 2))
        }
        return DanaTile.screen(col.build(), DanaTile.launch("io.github.ronganam.dana.wear.tasks.TasksActivity", "open_tasks"))
    }
}

/**
 * Waiting on you: how many suggestions are pending and the newest one, with
 * Accept / Dismiss (sent to the phone by [io.github.ronganam.dana.wear.cards.SuggestionActionActivity]).
 */
class WaitingTileService : TileService() {

    override fun onTileRequest(requestParams: RequestBuilders.TileRequest): ListenableFuture<Tile> =
        Futures.immediateFuture(DanaTile.tile(build(DanaSnapshot.load(applicationContext))))

    override fun onTileResourcesRequest(requestParams: RequestBuilders.ResourcesRequest): ListenableFuture<ResourceBuilders.Resources> =
        DanaTile.resources()

    private fun button(label: String, color: Int, clickable: ModifiersBuilders.Clickable): LayoutElementBuilders.Box =
        LayoutElementBuilders.Box.Builder()
            .setWidth(DimensionBuilders.dp(58f))
            .setHeight(DimensionBuilders.dp(36f))
            .setHorizontalAlignment(LayoutElementBuilders.HORIZONTAL_ALIGN_CENTER)
            .setVerticalAlignment(LayoutElementBuilders.VERTICAL_ALIGN_CENTER)
            .setModifiers(
                ModifiersBuilders.Modifiers.Builder()
                    .setClickable(clickable)
                    .setBackground(
                        ModifiersBuilders.Background.Builder()
                            .setColor(ColorBuilders.argb(color))
                            .setCorner(ModifiersBuilders.Corner.Builder().setRadius(DimensionBuilders.dp(18f)).build())
                            .build()
                    )
                    .build()
            )
            .addContent(DanaTile.text(label, 0xFF13100E.toInt(), 13f, bold = true))
            .build()

    private fun build(s: JSONObject): LayoutElementBuilders.LayoutElement {
        val count = s.optInt("suggestion_count")
        val top = s.optJSONArray("suggestions")?.optJSONObject(0)
        val col = DanaTile.column()
        col.addContent(DanaTile.text(if (count > 0) "WAITING ON YOU · $count" else "WAITING ON YOU", COLOR_ORANGE, 11f, bold = true))
        col.addContent(DanaTile.gap(4f))
        if (top == null) {
            col.addContent(DanaTile.text("Nothing waiting ✓", COLOR_LIGHT_GRAY, 13f))
            return DanaTile.screen(col.build(), null)
        }
        val id = top.optString("id")
        col.addContent(DanaTile.text(top.optString("title"), COLOR_WHITE, 13f, bold = true, lines = 3))
        col.addContent(DanaTile.gap(2f))
        col.addContent(DanaTile.text(if (top.optString("source") == "notification") "from a notification" else "from a call", COLOR_GRAY, 10f))
        col.addContent(DanaTile.gap(8f))
        val cls = "io.github.ronganam.dana.wear.cards.SuggestionActionActivity"
        col.addContent(
            LayoutElementBuilders.Row.Builder()
                .addContent(button("✓", COLOR_GREEN, DanaTile.launch(cls, "accept_$id", mapOf("id" to id, "action" to "accept"))))
                .addContent(LayoutElementBuilders.Spacer.Builder().setWidth(DimensionBuilders.dp(10f)).build())
                .addContent(button("✕", COLOR_LIGHT_GRAY, DanaTile.launch(cls, "dismiss_$id", mapOf("id" to id, "action" to "dismiss"))))
                .build()
        )
        return DanaTile.screen(col.build(), null)
    }
}
