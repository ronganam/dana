package io.github.ronganam.dana.wear.debug

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.util.Base64
import android.util.Log
import androidx.core.content.edit
import io.github.ronganam.dana.protocol.WearPaths
import io.github.ronganam.dana.protocol.WearTaskItem

/**
 * Debug builds only: sample data for the Tasks and assistant screens on a
 * watch with no phone (emulators can't pair), to look at and measure them.
 * Off until [DebugSampleReceiver] gives it something:
 *
 *   adb -s <watch> shell am broadcast -n io.github.ronganam.dana/.wear.debug.DebugSampleReceiver \
 *       --es tasks_b64 <base64 JSON list of tasks, or "off">  --es reply_b64 <base64 reply frame>
 *
 * `tasks_b64` stands in for the phone's [WearPaths.GET_TASKS] answer (Done
 * and Snooze then act on that list); `reply_b64` is handed to the assistant
 * screen as if the phone had sent it on the send-message answer path.
 */
object DebugSamples {
    private const val PREFS = "debug_samples"
    private val main = Handler(Looper.getMainLooper())
    private var threadFeed: ((path: String, json: String) -> Unit)? = null

    /** The sample tasks, or null when none are set (the phone is asked). */
    fun tasks(context: Context): List<WearTaskItem>? =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("tasks", null)?.let { WearTaskItem.decodeList(it) }

    /** Done / Snooze on a sample task: it leaves the sample list. */
    fun removeTask(context: Context, id: String) {
        val left = tasks(context)?.filter { it.id != id } ?: return
        setTasks(context, WearTaskItem.encodeList(left))
    }

    internal fun setTasks(context: Context, json: String?) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit { putString("tasks", json) }
    }

    /** The assistant screen's ViewModel takes sample frames here while it lives (null detaches). */
    fun attachThread(feed: ((path: String, json: String) -> Unit)?) {
        threadFeed = feed
    }

    internal fun sendReply(json: String) {
        main.post { threadFeed?.invoke(WearPaths.response(WearPaths.SEND_MESSAGE), json) ?: Log.w("DebugSamples", "No assistant screen") }
    }
}

/** Debug builds only: drives [DebugSamples] from adb (see there). */
class DebugSampleReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val app = context.applicationContext
        fun text(b64: String) = String(Base64.decode(b64, Base64.DEFAULT))
        intent.getStringExtra("tasks_b64")?.let { DebugSamples.setTasks(app, if (it == "off") null else text(it)) }
        intent.getStringExtra("reply_b64")?.let { DebugSamples.sendReply(text(it)) }
        Log.i("DebugSamples", "tasks=${DebugSamples.tasks(app)?.size ?: "off"}")
    }
}
