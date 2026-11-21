package app.ganam.assistant.data.notifications

import android.content.Context
import android.content.Intent

/**
 * Doing things to a phone notification the way the shade would, for the new
 * Now and Notifications screens (the old Inbox has its own copy in
 * `OnPhoneActions`). Everything reads the live notification each time, so it
 * only offers what still works.
 */
object PhoneNotificationActions {

    /** Open it in its app (its tap action while in the shade, else the app's launcher). False when neither worked. */
    fun open(context: Context, key: String?, packageName: String): Boolean {
        if (DanaNotificationListener.inShade(key) && DanaNotificationListener.open(context, key)) return true
        val launch = context.packageManager.getLaunchIntentForPackage(packageName) ?: return false
        return runCatching { context.startActivity(launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }.isSuccess
    }

    /** The title of its reply button (one that takes typed text), while it is in the shade. */
    fun replyButton(key: String?): String? = DanaNotificationListener.liveActions(key).firstOrNull { it.second }?.first

    /** The app's own buttons that don't take typed text (Mark as read, Archive, Delete …), while in the shade. */
    fun buttons(key: String?): List<String> =
        DanaNotificationListener.liveActions(key).filter { !it.second }.map { it.first }.distinct().take(4)

    /** Press the button titled [title]. Null when done, else why not. */
    fun press(context: Context, key: String?, title: String): String? =
        DanaNotificationListener.perform(context, key, title)

    /** Its suggested quick replies, if the app offers any. */
    fun quickReplies(key: String?): List<String> = DanaNotificationListener.replyChoices(key)

    /** Send [text] through its reply button. Null when sent, else why not. */
    fun reply(context: Context, key: String?, text: String): String? {
        val button = replyButton(key) ?: return "It can't be answered from here any more"
        return DanaNotificationListener.perform(context, key, button, text)
    }
}
