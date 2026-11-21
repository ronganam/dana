package io.github.ronganam.dana.wear.complications

import android.util.Log
import androidx.wear.watchface.complications.data.ComplicationData
import androidx.wear.watchface.complications.data.ComplicationType
import androidx.wear.watchface.complications.data.NoDataComplicationData
import androidx.wear.watchface.complications.data.PlainComplicationText
import androidx.wear.watchface.complications.data.ShortTextComplicationData
import androidx.wear.watchface.complications.datasource.ComplicationRequest
import androidx.wear.watchface.complications.datasource.SuspendingComplicationDataSourceService
import io.github.ronganam.dana.wear.data.WearPrefs

class Task3ComplicationService : SuspendingComplicationDataSourceService() {
    override suspend fun onComplicationRequest(request: ComplicationRequest): ComplicationData {
        if (request.complicationType != ComplicationType.SHORT_TEXT) return NoDataComplicationData()
        return try {
            val prefs = WearPrefs(applicationContext)
            val title = prefs.getTaskTitle(2)
            val (emoji, cleanTitle) = extractLeadingEmoji(title)
            val display = if (emoji != null) "$cleanTitle $emoji" else cleanTitle
            ShortTextComplicationData.Builder(
                text = PlainComplicationText.Builder(display).build(),
                contentDescription = PlainComplicationText.Builder(display).build()
            ).setTitle(PlainComplicationText.Builder(display).build()).build()
        } catch (e: Exception) {
            ShortTextComplicationData.Builder(
                text = PlainComplicationText.Builder("--").build(),
                contentDescription = PlainComplicationText.Builder("--").build()
            ).setTitle(PlainComplicationText.Builder("--").build()).build()
        }
    }
    override fun getPreviewData(type: ComplicationType) = ShortTextComplicationData.Builder(
        text = PlainComplicationText.Builder("Report \uD83D\uDCCB").build(),
        contentDescription = PlainComplicationText.Builder("Report").build()
    ).setTitle(PlainComplicationText.Builder("Report").build()).build()

    private fun extractLeadingEmoji(text: String): Pair<String?, String> {
        if (text.isEmpty()) return Pair(null, text)
        val cp = text.codePointAt(0)
        if (cp in 0x2300..0x23FF || cp in 0x2600..0x27BF || cp in 0x2B05..0x2B55 ||
            cp in 0x3030..0x303D || cp in 0x1F1E6..0x1F1FF ||
            cp in 0x1F300..0x1F9FF || cp in 0x1FA00..0x1FAFF) {
            val emojiStr = String(Character.toChars(cp))
            val rest = text.substring(emojiStr.length).trimStart { it == ' ' || it == '-' || it == ':' || it == '\u2013' }
            return Pair(emojiStr, rest)
        }
        return Pair(null, text)
    }
}
