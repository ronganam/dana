package io.github.ronganam.dana.wear.debug

import android.content.Context
import io.github.ronganam.dana.protocol.WearTaskItem

/** Release builds always show the phone's data (the debug twin can stand in sample data). */
object DebugSamples {
    fun tasks(context: Context): List<WearTaskItem>? = null
    fun removeTask(context: Context, id: String) {}
    fun attachThread(feed: ((path: String, json: String) -> Unit)?) {}
}
