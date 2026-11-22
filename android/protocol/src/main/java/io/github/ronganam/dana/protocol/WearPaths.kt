package io.github.ronganam.dana.protocol

/**
 * Every Wearable Data Layer path the phone (:app) and the watch (:wear) use.
 * Both apps share one applicationId and signing key, so messages and data
 * items on these paths reach the other side (docs/wear.md, "Phone bridge").
 */
object WearPaths {
    // --- Data items ---

    /** Phone → watch: server, thread and cached tile data ([WearConfig]). */
    const val CONFIG = "/dana_config"
    /** Watch → phone: the watch app's version ([WearVersion]). */
    const val VERSION = "/dana/version"

    // --- Watch → phone requests; the phone answers on [response] of the same path ---

    /** The conversation list (always just the shared thread). */
    const val GET_SESSIONS = "/get_sessions"
    /** Older watch builds' name for [GET_SESSIONS]. */
    const val GET_RECENT_SESSIONS = "/get_recent_sessions"
    /** The thread's recent messages (payload: the session id). */
    const val GET_SESSION_DETAIL = "/get_session_detail"
    const val CREATE_SESSION = "/create_session"
    /** A typed or dictated message (JSON: message, session_id). */
    const val SEND_MESSAGE = "/send_message"
    /** Approve / deny the pending action. */
    const val APPROVE_ACTION = "/approve_action"
    /** Open reminders. */
    const val GET_TASKS = "/get_tasks"
    /** [TaskStatusRequest]; answered with [TaskActionReply]. */
    const val UPDATE_TASK_STATUS = "/update_task_status"
    /** [SnoozeRequest]; answered with [TaskActionReply]. */
    const val SNOOZE_TASK = "/snooze_task"
    /** Accept / dismiss a suggestion from the watch (JSON: id, action). */
    const val SUGGESTION_ACTION = "/suggestion_action"
    /** Tick / untick / add on the shopping list (JSON: action, id | text, list_id, ref). */
    const val SHOPPING_ACTION = "/shopping_action"
    /** A pairing code of kind `watch` for the watch to redeem itself ([WearPairRequest]; answered with [WearPairOffer]). */
    const val PAIR_WATCH = "/pair_watch"

    // --- Watch → phone push-to-talk audio, in arrival order ---

    const val VOICE_START = "/voice_start"
    const val VOICE_CHUNK = "/voice_chunk"
    const val VOICE_STOP = "/voice_stop"
    const val VOICE_CANCEL = "/voice_cancel"

    // --- Phone → watch replies ---

    private const val RESPONSE_PREFIX = "/response"

    /** Where the phone answers a request sent on [request]. */
    fun response(request: String): String = RESPONSE_PREFIX + request

    /** Replies (and live updates) to a voice turn. */
    const val RESPONSE_VOICE_STREAM = "$RESPONSE_PREFIX/voice_stream"
    /** A request failed on the phone (JSON: error). */
    const val RESPONSE_ERROR = "$RESPONSE_PREFIX/error"

    // --- Phone → watch pushes ---

    /** A card for the watch (JSON: kind, title, subtitle, lines, ref). */
    const val CARD = "/wear/dana_card"
    /** Drop the watch's card of a kind (JSON: kind). */
    const val CARD_CLEAR = "/wear/dana_card_clear"
    /** The phone's widget snapshot, trimmed for the tiles and complications. */
    const val SNAPSHOT = "/wear/dana_snapshot"
    /** A reminder rings: ring on the watch too ([ReminderAlarm]). */
    const val REMINDER_ALARM = "/wear/reminder_alarm"
    /** The reminder was handled on the phone: stop ringing ([ReminderHandled]). */
    const val REMINDER_HANDLED_ON_PHONE = "/wear/reminder_dismiss"

    // --- Watch → phone notices ---

    /** The reminder was handled on the watch: stop ringing ([ReminderHandled]). */
    const val REMINDER_HANDLED_ON_WATCH = "/reminder_dismiss"
}
