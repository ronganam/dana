package io.github.ronganam.dana.ui.viewmodels

/**
 * Something on screen that Dana should see with the next message
 * (`screen: {kind, id}` on `POST /api/assistant/message`, server
 * `docs/agent-actions.md` → On-screen context). [title] is only for the
 * "On screen · …" line.
 */
data class ScreenRef(val kind: String, val id: String, val title: String = "")

/**
 * What the shell has open, flattened so the mapping to a [ScreenRef] stays a
 * pure function (tested in `ScreenContextTest`).
 */
data class ShellSnapshot(
    val place: Place,
    /** The picked Now row: its id (`sug:12`, `task:…`), title and `ref`. */
    val nowItem: NowPick? = null,
    /** The reminder open in the editor. */
    val reminder: Pair<String, String>? = null,
    /** The open notification app: package, app name, newest waiting notification id. */
    val notifApp: NotifPick? = null,
    val more: MoreItem? = null,
    /** The call open in Calls (id, title). */
    val call: Pair<String, String>? = null,
    /** The person open in People (id, name). */
    val person: Pair<String, String>? = null,
    /** The weekly review shown (id). */
    val reviewId: String? = null,
    /** What Projects or Money has open ([ShellController.placeRef]). */
    val placeRef: ScreenRef? = null
) {
    data class NowPick(val id: String, val title: String, val refType: String?, val refId: String?)
    data class NotifPick(val packageName: String, val appName: String, val newestId: String?)
}

object ScreenContext {
    /** Kinds the server accepts in `screen`. */
    val KINDS = setOf("suggestion", "call", "task", "person", "notification", "review", "transaction", "saving")

    /** A Now card → kind + id; approvals (their thread) and checks have none. */
    fun fromNowItem(itemId: String, refType: String?, refId: String?): Pair<String, String>? {
        val (prefix, rest) = itemId.substringBefore(':', "") to itemId.substringAfter(':', "")
        val byPrefix = when (prefix) {
            "sug" -> "suggestion"
            "task" -> "task"
            "notif" -> "notification"
            "review" -> "review"
            else -> null
        }
        if (byPrefix != null && rest.isNotBlank()) return byPrefix to rest
        if (refType in KINDS && !refId.isNullOrBlank()) return refType!! to refId
        return null
    }

    /** What is open in the shell right now, as Dana should see it. */
    fun forShell(s: ShellSnapshot): ScreenRef? = when (s.place) {
        Place.NOW -> s.nowItem?.let { n ->
            fromNowItem(n.id, n.refType, n.refId)?.let { (k, id) -> ScreenRef(k, id, n.title) }
        }
        Place.REMINDERS -> s.reminder?.let { (id, title) -> ScreenRef("task", id, title) }
        // An app page has no kind of its own: its newest waiting notification stands for it.
        Place.NOTIFICATIONS -> s.notifApp?.let { a -> a.newestId?.let { ScreenRef("notification", it, a.appName) } }
        Place.PROJECTS -> s.placeRef
        Place.MORE -> when (s.more) {
            // Dana reads the shopping list with her own tool; it adds no context.
            MoreItem.SHOPPING -> null
            MoreItem.MONEY -> s.placeRef
            MoreItem.CALLS -> s.call?.let { (id, title) -> ScreenRef("call", id, title) }
            MoreItem.PEOPLE -> s.person?.let { (id, name) -> ScreenRef("person", id, name) }
            MoreItem.REVIEW -> s.reviewId?.let { ScreenRef("review", it, "Weekly review") }
            else -> null
        }
    }

    /**
     * The context a message carries. On the cover screen only what the
     * thread was opened from ([pinned]) counts; unfolded the pin wins while
     * the screen still shows what it was pinned over ([liveAtPin]), else what
     * is on screen ([live]) unless the owner dropped exactly that ([dropped]).
     */
    fun effective(stacked: Boolean, live: ScreenRef?, pinned: ScreenRef?, liveAtPin: ScreenRef?, dropped: ScreenRef?): ScreenRef? {
        if (stacked) return pinned
        if (pinned != null && live == liveAtPin) return pinned
        return live?.takeIf { it != dropped }
    }

    /** "a call", "a reminder"… for a pin whose title isn't known. */
    fun kindLabel(kind: String): String = when (kind) {
        "task" -> "A reminder"
        "call" -> "A call"
        "person" -> "A person"
        "notification" -> "A notification"
        "suggestion" -> "A suggestion"
        "review" -> "Weekly review"
        else -> kind.replaceFirstChar { it.uppercase() }
    }
}
