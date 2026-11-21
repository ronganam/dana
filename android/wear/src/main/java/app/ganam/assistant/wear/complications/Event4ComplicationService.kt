package app.ganam.assistant.wear.complications

import android.util.Log
import androidx.wear.watchface.complications.data.ComplicationData
import androidx.wear.watchface.complications.data.ComplicationType
import androidx.wear.watchface.complications.data.NoDataComplicationData
import androidx.wear.watchface.complications.data.PlainComplicationText
import androidx.wear.watchface.complications.data.ShortTextComplicationData
import androidx.wear.watchface.complications.data.RangedValueComplicationData
import androidx.wear.watchface.complications.datasource.ComplicationRequest
import androidx.wear.watchface.complications.datasource.SuspendingComplicationDataSourceService
import app.ganam.assistant.wear.data.WearPrefs

class Event4ComplicationService : SuspendingComplicationDataSourceService() {
    override suspend fun onComplicationRequest(request: ComplicationRequest): ComplicationData {
        val prefs = WearPrefs(applicationContext)
        val title = prefs.getEventTitle(3)
        val time = prefs.getEventTime(3)
        val dayDiff = prefs.getEventDayDiff(3)

        if (request.complicationType == ComplicationType.RANGED_VALUE) {
            return try {
                Log.d("Event4Comp", "RANGED_VALUE: $title / $time / diff=$dayDiff")
                RangedValueComplicationData.Builder(
                    value = dayDiff.toFloat(),
                    min = 0f,
                    max = 7f,
                    contentDescription = PlainComplicationText.Builder(title).build()
                ).setText(PlainComplicationText.Builder(time).build())
                 .setTitle(PlainComplicationText.Builder(title).build())
                 .build()
            } catch (e: Exception) {
                RangedValueComplicationData.Builder(
                    value = 0f,
                    min = 0f,
                    max = 7f,
                    contentDescription = PlainComplicationText.Builder("--").build()
                ).setText(PlainComplicationText.Builder("--").build())
                 .setTitle(PlainComplicationText.Builder("--").build())
                 .build()
            }
        }

        if (request.complicationType == ComplicationType.SHORT_TEXT) {
            return try {
                Log.d("Event4Comp", "SHORT_TEXT: $title / $time")
                ShortTextComplicationData.Builder(
                    text = PlainComplicationText.Builder(time).build(),
                    contentDescription = PlainComplicationText.Builder(title).build()
                ).setTitle(PlainComplicationText.Builder(title).build()).build()
            } catch (e: Exception) {
                ShortTextComplicationData.Builder(
                    text = PlainComplicationText.Builder("--").build(),
                    contentDescription = PlainComplicationText.Builder("--").build()
                ).setTitle(PlainComplicationText.Builder("--").build()).build()
            }
        }

        return NoDataComplicationData()
    }

    override fun getPreviewData(type: ComplicationType): ComplicationData {
        if (type == ComplicationType.RANGED_VALUE) {
            return RangedValueComplicationData.Builder(
                value = 0f,
                min = 0f,
                max = 7f,
                contentDescription = PlainComplicationText.Builder("Dinner").build()
            ).setText(PlainComplicationText.Builder("18:00").build())
             .setTitle(PlainComplicationText.Builder("Dinner").build())
             .build()
        }
        return ShortTextComplicationData.Builder(
            text = PlainComplicationText.Builder("18:00").build(),
            contentDescription = PlainComplicationText.Builder("Dinner").build()
        ).setTitle(PlainComplicationText.Builder("Dinner").build()).build()
    }
}
