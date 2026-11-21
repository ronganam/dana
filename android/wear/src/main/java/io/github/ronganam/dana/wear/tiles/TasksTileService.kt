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
import io.github.ronganam.dana.protocol.WearTaskItem
import kotlin.math.sqrt
import io.github.ronganam.dana.wear.R

class TasksTileService : TileService() {

    companion object {
        private const val RESOURCES_VERSION = "1"
        private const val MAX_TASKS = 8
        private const val SCREEN_RADIUS_DP = 107.5f
        private const val ROW_HEIGHT_DP = 16f
        private const val MIN_PAD_DP = 6f
        private const val EMOJI_BOX_WIDTH_DP = 16f
    }

    override fun onTileRequest(
        requestParams: RequestBuilders.TileRequest
    ): ListenableFuture<Tile> {
        val prefs = WearPrefs(applicationContext)
        val tasks = parseTasks(prefs.cachedTasks)
        val layout = buildLayout(tasks)

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

    private fun buildLayout(tasks: List<TaskItem>): Box {
        val grouped = tasks
            .sortedWith(compareBy<TaskItem> { priorityWeight(it.priority) }.thenBy { it.dueDate ?: "9999" })
            .groupBy { it.groupName ?: "" }

        val visibleGroups = mutableListOf<Pair<String, List<TaskItem>>>()
        var slotCount = 0
        for ((group, groupTasks) in grouped) {
            if (slotCount >= MAX_TASKS) break
            val remaining = MAX_TASKS - slotCount
            val take = groupTasks.take(remaining)
            if (take.isNotEmpty()) {
                visibleGroups.add(group to take)
                slotCount += take.size
            }
        }

        var totalRows = 1
        if (tasks.isEmpty()) {
            totalRows += 1
        } else {
            for ((group, groupTasks) in visibleGroups) {
                if (group.isNotEmpty()) totalRows += 1
                totalRows += groupTasks.size
            }
            if (tasks.size > MAX_TASKS) totalRows += 1
        }

        val column = Column.Builder()
            .setWidth(DimensionBuilders.expand())
            .setHeight(DimensionBuilders.wrap())

        var row = 0

        column.addContent(buildPaddedText(getString(R.string.tile_tasks_count, tasks.size), COLOR_AMBER, 12f, true, row++, totalRows))
        column.addContent(Spacer.Builder().setHeight(DimensionBuilders.dp(3f)).build())

        if (tasks.isEmpty()) {
            column.addContent(buildPaddedText(getString(R.string.tile_all_clear), COLOR_INK_SOFT, 12f, false, row++, totalRows))
        } else {
            var displayed = 0
            for ((group, groupTasks) in visibleGroups) {
                if (group.isNotEmpty()) {
                    val emoji = groupTasks.first().groupEmoji?.let { "$it " } ?: ""
                    column.addContent(buildPaddedText("$emoji$group", COLOR_INK_SOFT, 10f, true, row++, totalRows))
                }
                for (task in groupTasks) {
                    column.addContent(buildTaskRow(task, row++, totalRows))
                    displayed++
                }
            }
            if (tasks.size > MAX_TASKS) {
                column.addContent(buildPaddedText(
                    getString(R.string.tile_more, tasks.size - MAX_TASKS),
                    COLOR_INK_SOFT, 10f, false, row++, totalRows
                ))
            }
        }

        val clickable = ModifiersBuilders.Clickable.Builder()
            .setOnClick(
                ActionBuilders.LaunchAction.Builder()
                    .setAndroidActivity(
                        ActionBuilders.AndroidActivity.Builder()
                            .setPackageName("io.github.ronganam.dana")
                            .setClassName("io.github.ronganam.dana.wear.tasks.TasksActivity")
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

    private fun buildPaddedText(text: String, color: Int, sizeSp: Float, bold: Boolean, row: Int, total: Int): LayoutElementBuilders.Text {
        val style = LayoutElementBuilders.FontStyle.Builder()
            .setSize(DimensionBuilders.sp(sizeSp))
            .setColor(ColorBuilders.argb(color))
        if (bold) style.setWeight(LayoutElementBuilders.FONT_WEIGHT_BOLD)
        return LayoutElementBuilders.Text.Builder()
            .setText(text)
            .setModifiers(rowPadding(row, total))
            .setFontStyle(style.build())
            .build()
    }

    private fun buildTaskRow(task: TaskItem, row: Int, total: Int): Row {
        val priorityColor = priorityColor(task.priority)
        val dueStr = formatDueDate(task.dueDate)
        val title = truncateTitle(task.title, 28)
        val titleWithDue = if (dueStr.isNotEmpty()) "$title  $dueStr" else title

        val (emoji, cleanTitle) = extractLeadingEmoji(titleWithDue)

        val dotBox = Box.Builder()
            .setWidth(DimensionBuilders.dp(EMOJI_BOX_WIDTH_DP))
            .setHeight(DimensionBuilders.wrap())
            .addContent(
                LayoutElementBuilders.Text.Builder()
                    .setText("\u200E\u25CF")
                    .setFontStyle(
                        LayoutElementBuilders.FontStyle.Builder()
                            .setSize(DimensionBuilders.sp(11f))
                            .setColor(ColorBuilders.argb(priorityColor))
                            .setWeight(LayoutElementBuilders.FONT_WEIGHT_BOLD)
                            .build()
                    )
                    .build()
            )
            .build()

        val titleBox = Box.Builder()
            .setWidth(DimensionBuilders.expand())
            .setHeight(DimensionBuilders.wrap())
            .setHorizontalAlignment(LayoutElementBuilders.HORIZONTAL_ALIGN_CENTER)
            .addContent(
                LayoutElementBuilders.Text.Builder()
                    .setText("\u2066$cleanTitle\u2069")
                    .setFontStyle(
                        LayoutElementBuilders.FontStyle.Builder()
                            .setSize(DimensionBuilders.sp(12f))
                            .setColor(ColorBuilders.argb(COLOR_INK))
                            .build()
                    )
                    .build()
            )
            .build()

        val emojiBox = Box.Builder()
            .setWidth(DimensionBuilders.dp(EMOJI_BOX_WIDTH_DP))
            .setHeight(DimensionBuilders.wrap())
            .addContent(
                LayoutElementBuilders.Text.Builder()
                    .setText(emoji ?: "")
                    .setFontStyle(
                        LayoutElementBuilders.FontStyle.Builder()
                            .setSize(DimensionBuilders.sp(11f))
                            .build()
                    )
                    .build()
            )
            .build()

        return Row.Builder()
            .setWidth(DimensionBuilders.expand())
            .setModifiers(rowPadding(row, total))
            .addContent(dotBox)
            .addContent(titleBox)
            .addContent(emojiBox)
            .build()
    }

    private fun extractLeadingEmoji(text: String): Pair<String?, String> {
        if (text.isEmpty()) return Pair(null, text)
        val codePoint = text.codePointAt(0)
        val isEmoji = (codePoint in 0x1F300..0x1F9FF) ||
                (codePoint in 0x1FA00..0x1FAFF) ||
                (codePoint in 0x2600..0x27BF) ||
                (codePoint in 0x1F1E6..0x1F1FF)
        if (isEmoji) {
            val emojiStr = String(Character.toChars(codePoint))
            var remaining = text.substring(emojiStr.length)
            remaining = remaining.trimStart { it == ' ' || it == '-' || it == ':' || it == '\u2013' }
            return Pair(emojiStr, remaining)
        }
        return Pair(null, text)
    }

    private fun truncateTitle(title: String, maxLen: Int): String {
        return if (title.length > maxLen) title.substring(0, maxLen - 1) + ".." else title
    }

    private fun formatDueDate(dueDate: String?): String {
        if (dueDate.isNullOrEmpty()) return ""
        return try {
            val parts = dueDate.split("-")
            if (parts.size == 3) "${parts[1]}/${parts[2]}" else ""
        } catch (_: Exception) { "" }
    }

    private fun priorityWeight(priority: String): Int {
        return when (priority.lowercase()) {
            "high", "urgent", "critical" -> 0
            "medium", "normal" -> 1
            "low" -> 2
            else -> 1
        }
    }

    private fun priorityColor(priority: String): Int {
        return when (priority.lowercase()) {
            "high", "urgent", "critical" -> COLOR_EMBER
            "medium", "normal" -> COLOR_AMBER
            "low" -> COLOR_SAGE
            else -> COLOR_AMBER
        }
    }

    private fun parseTasks(json: String): List<TaskItem> {
        fun given(v: String?) = v?.takeIf { it.isNotEmpty() && it != "null" }
        return WearTaskItem.decodeList(json).filter { it.status == "needs_action" }.map {
            TaskItem(title = it.title, dueDate = given(it.dueDate), priority = it.priority, groupName = given(it.groupName), groupEmoji = given(it.groupEmoji))
        }
    }

    private data class TaskItem(
        val title: String,
        val dueDate: String?,
        val priority: String,
        val groupName: String?,
        val groupEmoji: String?
    )
}
