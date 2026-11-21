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
import io.github.ronganam.dana.protocol.DanaSnapshot
import io.github.ronganam.dana.wear.cards.SnapshotStore
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import io.github.ronganam.dana.wear.R

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
        Futures.immediateFuture(DanaTile.tile(build(SnapshotStore.load(applicationContext))))

    override fun onTileResourcesRequest(requestParams: RequestBuilders.ResourcesRequest): ListenableFuture<ResourceBuilders.Resources> =
        DanaTile.resources()

    /** [snapshot]: null before the phone sent one. */
    private fun build(snapshot: DanaSnapshot?): LayoutElementBuilders.LayoutElement {
        val s = snapshot ?: DanaSnapshot()
        val today = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
        val col = DanaTile.column()
        col.addContent(DanaTile.text(getString(R.string.tile_today), COLOR_AMBER, 11f, bold = true))
        col.addContent(DanaTile.gap(2f))

        val headline = s.briefingHeadline?.takeIf { it.isNotBlank() && s.briefingDate == today }
        if (headline != null) {
            col.addContent(DanaTile.text(headline, COLOR_INK, 13f, bold = true, lines = 2))
            col.addContent(DanaTile.gap(4f))
        }

        var shown = 0
        for (e in s.events.take(2)) {
            col.addContent(DanaTile.text("${e.time}  ${e.title}", COLOR_SKY, 12f))
            shown++
        }
        for (t in s.tasks.take(if (headline != null) 2 else 3)) {
            val color = if (t.due == "overdue") COLOR_EMBER else COLOR_INK_SOFT
            col.addContent(DanaTile.text("• ${t.title}", color, 12f))
            shown++
        }
        if (headline == null && shown == 0) {
            col.addContent(DanaTile.text(getString(if (snapshot == null) R.string.open_dana_on_phone else R.string.tile_nothing_due), COLOR_INK_SOFT, 12f, lines = 2))
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
        Futures.immediateFuture(DanaTile.tile(build(SnapshotStore.load(applicationContext))))

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

    private fun build(s: DanaSnapshot?): LayoutElementBuilders.LayoutElement {
        val count = s?.suggestionCount ?: 0
        val top = s?.suggestions?.firstOrNull()
        val col = DanaTile.column()
        col.addContent(DanaTile.text(if (count > 0) getString(R.string.tile_waiting_count, count) else getString(R.string.tile_waiting), COLOR_EMBER, 11f, bold = true))
        col.addContent(DanaTile.gap(4f))
        if (top == null) {
            col.addContent(DanaTile.text(getString(R.string.tile_nothing_waiting), COLOR_INK_SOFT, 13f))
            return DanaTile.screen(col.build(), null)
        }
        val id = top.id
        col.addContent(DanaTile.text(top.title, COLOR_INK, 13f, bold = true, lines = 3))
        col.addContent(DanaTile.gap(2f))
        col.addContent(DanaTile.text(getString(if (top.source == "notification") R.string.tile_from_notification else R.string.tile_from_call), COLOR_MUTE, 10f))
        col.addContent(DanaTile.gap(8f))
        val cls = "io.github.ronganam.dana.wear.cards.SuggestionActionActivity"
        col.addContent(
            LayoutElementBuilders.Row.Builder()
                .addContent(button("✓", COLOR_SAGE, DanaTile.launch(cls, "accept_$id", mapOf("id" to id, "action" to "accept"))))
                .addContent(LayoutElementBuilders.Spacer.Builder().setWidth(DimensionBuilders.dp(10f)).build())
                .addContent(button("✕", COLOR_INK_SOFT, DanaTile.launch(cls, "dismiss_$id", mapOf("id" to id, "action" to "dismiss"))))
                .build()
        )
        return DanaTile.screen(col.build(), null)
    }
}
