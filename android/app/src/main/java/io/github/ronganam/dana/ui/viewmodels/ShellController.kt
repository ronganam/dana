package io.github.ronganam.dana.ui.viewmodels

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** The places of the main screen. PROJECTS only for a user with the `knowledge` addon. */
enum class Place(val label: String) {
    NOW("Now"), REMINDERS("Reminders"), NOTIFICATIONS("Notifications"), PROJECTS("Projects"), MORE("More")
}

/**
 * What More can open. SHOPPING (the `shopping` addon) and MONEY (`money`)
 * take over the whole place with their own list and stage, with a way back to
 * More. SETTINGS opens from About you, SIGNALS from Settings, MEMORY only
 * from the old Settings link (previous layout), REVIEW from its notification;
 * none of them are rows of the list.
 */
enum class MoreItem(val label: String) {
    SHOPPING("Shopping"), MONEY("Money"), CALLS("Calls"), PEOPLE("People"), ABOUT("About you"),
    SETTINGS("Settings"), SIGNALS("Signals"), MEMORY("Memory"), REVIEW("Weekly review");

    /** Items with their own list + stage, shown in place of More's list. */
    val takesOver: Boolean get() = this == SHOPPING || this == MONEY
}

/**
 * Something the thread is opened about ("Ask Dana about this"): it goes with
 * the next messages as on-screen context (`screen: {kind, id}`; kinds: task,
 * call, suggestion, notification, person, review). [title] is for the
 * "On screen · …" line; empty = looked up from what the app has loaded.
 */
data class ThreadContext(val kind: String, val id: String, val title: String = "")

/**
 * Navigation state of the main screen ([io.github.ronganam.dana.ui.shell.DanaShell]):
 * which place is shown, what is picked in each place, and whether the thread
 * sheet is open. It lives in the ViewModel, so a fold/unfold (which swaps the
 * layout tree) keeps all of it.
 *
 * Selection rule: a non-null pick means the stage shows it. On the cover
 * screen that is a drill-down (Back clears it); unfolded, the list and the
 * stage are side by side. Picks made by the layout itself (unfolded shows the
 * first reminder when nothing is picked) are marked `auto` and don't drill
 * down when the phone is folded again.
 */
class ShellController(
    /** A title for a pinned item that came without one (from the lists the app has loaded). */
    private val titleFor: (kind: String, id: String) -> String = { _, _ -> "" },
    /** The thread was opened (re-read it: the watch or the Mac may have added to it). */
    private val onThreadOpened: () -> Unit = {}
) {
    private val _place = MutableStateFlow(Place.NOW)
    val place: StateFlow<Place> = _place.asStateFlow()

    private val _threadOpen = MutableStateFlow(false)
    /** The thread sheet (cover screen / unfolded portrait). Unfolded landscape always shows the thread. */
    val threadOpen: StateFlow<Boolean> = _threadOpen.asStateFlow()

    /** A picked item: [id] plus whether the layout picked it rather than the user. */
    data class Pick(val id: String, val auto: Boolean = false)

    private val _reminder = MutableStateFlow<Pick?>(null)
    val reminder: StateFlow<Pick?> = _reminder.asStateFlow()

    private val _more = MutableStateFlow<MoreItem?>(null)
    val more: StateFlow<MoreItem?> = _more.asStateFlow()

    private val _now = MutableStateFlow<Pick?>(null)
    /**
     * The Now row that is open: expanded in place on the cover screen (user
     * picks only), shown in the stage unfolded.
     */
    val now: StateFlow<Pick?> = _now.asStateFlow()

    private val _nowDay = MutableStateFlow(false)
    /** Unfolded Now shows "the rest of today" instead of auto-picking the first row (the strip's Now cell). */
    val nowDay: StateFlow<Boolean> = _nowDay.asStateFlow()

    private val _notifApp = MutableStateFlow<Pick?>(null)
    /** The app open on the Notifications screen (package name). */
    val notifApp: StateFlow<Pick?> = _notifApp.asStateFlow()

    private val _quietOpen = MutableStateFlow(false)
    /** Now: the "quieter suggestions" fold is open. */
    val quietOpen: StateFlow<Boolean> = _quietOpen.asStateFlow()

    private val _quietAppsOpen = MutableStateFlow(false)
    /** Notifications: the "Quiet apps" fold is open. */
    val quietAppsOpen: StateFlow<Boolean> = _quietAppsOpen.asStateFlow()

    /** Reminders list grouping: true = "By list", false = "By when". */
    private val _remindersByList = MutableStateFlow(false)
    val remindersByList: StateFlow<Boolean> = _remindersByList.asStateFlow()

    private val _doneOpen = MutableStateFlow(false)
    val doneOpen: StateFlow<Boolean> = _doneOpen.asStateFlow()

    /** What is typed in the ask bar; kept here so it survives fold/unfold. */
    var askDraft by mutableStateOf(TextFieldValue(""))

    /**
     * A place's own text field has the keyboard (Shopping's add field): the
     * ask bar steps aside meanwhile, so there is one field and one voice input.
     */
    var askHidden by mutableStateOf(false)

    /** Space a place keeps at the bottom of its content (Shopping's add bar): snackbars show above it. */
    var snackbarLift by mutableStateOf(0.dp)

    // --- On-screen context (what Dana sees with the next message) ---

    /** The cover screen arrangement (set by the shell): only a pinned item counts there. */
    @Volatile var stacked: Boolean = true

    private val _live = MutableStateFlow<ScreenRef?>(null)
    /** What the open place shows right now (set by the shell from its state). */
    val live: StateFlow<ScreenRef?> = _live.asStateFlow()

    private val _pinned = MutableStateFlow<ScreenRef?>(null)
    /** What the thread was opened about ("Ask Dana about this", a deep link, sending from an open item on the cover). */
    val pinned: StateFlow<ScreenRef?> = _pinned.asStateFlow()
    private var liveAtPin: ScreenRef? = null

    private val _dropped = MutableStateFlow<ScreenRef?>(null)

    private val _placeRef = MutableStateFlow<ScreenRef?>(null)
    /**
     * What a place that keeps its own picks (Projects, Money) has open, as
     * on-screen context; set by its destination, null when nothing is open.
     */
    val placeRef: StateFlow<ScreenRef?> = _placeRef.asStateFlow()
    fun setPlaceRef(ref: ScreenRef?) { _placeRef.value = ref }

    private val _screen = MutableStateFlow<ScreenRef?>(null)
    /** The context the next message carries ([ScreenContext.effective]); shown as "On screen · …". */
    val screen: StateFlow<ScreenRef?> = _screen.asStateFlow()

    private fun recompute() {
        _screen.value = ScreenContext.effective(stacked, _live.value, _pinned.value, liveAtPin, _dropped.value)
    }

    fun setLive(ref: ScreenRef?) {
        if (ref == _live.value) return
        _live.value = ref
        if (_dropped.value != null && _dropped.value != ref) _dropped.value = null
        recompute()
    }

    fun setStackedLayout(isStacked: Boolean) {
        if (stacked == isStacked) return
        stacked = isStacked
        recompute()
    }

    private fun pin(ref: ScreenRef?) {
        _pinned.value = ref?.let { if (it.title.isBlank()) it.copy(title = titleFor(it.kind, it.id)) else it }
        liveAtPin = _live.value
        _dropped.value = null
        recompute()
    }

    /** The × on "On screen · …": the next messages go without it. */
    fun dropScreen() {
        val shown = _screen.value
        _pinned.value = null
        liveAtPin = null
        _dropped.value = shown
        recompute()
    }

    fun go(place: Place) {
        closeThread()
        if (place == _place.value) {
            // Tapping the current place again goes back to its list.
            clearPick(place)
        }
        _place.value = place
    }

    /**
     * Open the thread. With [context] it is about that item. On the cover
     * screen, opening it while an item is open (Ask, the peek, sending from
     * the ask bar) pins that item, shown at the top of the sheet.
     */
    fun openThread(context: ThreadContext? = null) {
        when {
            context != null -> pin(ScreenRef(context.kind, context.id, context.title))
            stacked && !_threadOpen.value -> pin(_live.value)
        }
        if (!_threadOpen.value) {
            _threadOpen.value = true
            onThreadOpened()
        }
    }

    fun closeThread() {
        _threadOpen.value = false
        // On the cover the pin belongs to the open sheet.
        if (stacked && _pinned.value != null) pin(null)
    }

    fun toggleThread() { if (_threadOpen.value) closeThread() else openThread() }

    fun selectReminder(id: String?, auto: Boolean = false) {
        _reminder.value = id?.let { Pick(it, auto) }
    }

    fun selectMore(item: MoreItem?) { _more.value = item }

    fun selectNow(id: String?, auto: Boolean = false) {
        _now.value = id?.let { Pick(it, auto) }
        if (id != null) _nowDay.value = false
    }

    /** The day strip's Now cell: Now, nothing picked, the rest of today in the stage. */
    fun showToday() {
        closeThread()
        _place.value = Place.NOW
        _now.value = null
        _nowDay.value = true
    }

    fun selectNotifApp(packageName: String?, auto: Boolean = false) {
        _notifApp.value = packageName?.let { Pick(it, auto) }
    }

    /** Open [packageName] on the Notifications screen (from search, Now). */
    fun openNotifApp(packageName: String) {
        closeThread()
        _place.value = Place.NOTIFICATIONS
        _notifApp.value = Pick(packageName)
    }

    /** Open reminder [id] in the Reminders editor (from search). */
    fun openReminder(id: String) {
        closeThread()
        _place.value = Place.REMINDERS
        _reminder.value = Pick(id)
    }

    fun toggleQuiet() { _quietOpen.value = !_quietOpen.value }
    fun toggleQuietApps() { _quietAppsOpen.value = !_quietAppsOpen.value }

    fun setRemindersByList(byList: Boolean) { _remindersByList.value = byList }
    fun toggleDone() { _doneOpen.value = !_doneOpen.value }

    fun clearPick(place: Place = _place.value) {
        when (place) {
            Place.NOW -> _now.value = null
            Place.REMINDERS -> _reminder.value = null
            Place.NOTIFICATIONS -> _notifApp.value = null
            // Projects keeps its own picks (ProjectsController); its destination's onBack clears them.
            Place.PROJECTS -> Unit
            Place.MORE -> _more.value = when (_more.value) {
                MoreItem.SETTINGS -> MoreItem.ABOUT
                MoreItem.SIGNALS -> MoreItem.SETTINGS
                MoreItem.MEMORY -> MoreItem.ABOUT
                else -> null
            }
        }
    }

    /**
     * Routes used by deep links, notifications and older code
     * (`MainViewModel.navigateTo`): "chat", "tasks", "inbox", "shopping",
     * "money", "calls", "people", "projects", "memory", "settings", "signals", "review".
     */
    fun openRoute(route: String) {
        when (route) {
            "chat" -> openThread()
            "tasks" -> { closeThread(); _place.value = Place.REMINDERS }
            "inbox", "notifications" -> { closeThread(); _place.value = Place.NOTIFICATIONS }
            "now" -> { closeThread(); _place.value = Place.NOW }
            "projects" -> { closeThread(); _place.value = Place.PROJECTS }
            else -> {
                val item = when (route) {
                    "calls" -> MoreItem.CALLS
                    "people" -> MoreItem.PEOPLE
                    "shopping" -> MoreItem.SHOPPING
                    "money" -> MoreItem.MONEY
                    "memory" -> MoreItem.MEMORY
                    "about" -> MoreItem.ABOUT
                    "settings" -> MoreItem.SETTINGS
                    "signals" -> MoreItem.SIGNALS
                    "review" -> MoreItem.REVIEW
                    else -> return
                }
                closeThread()
                _place.value = Place.MORE
                _more.value = item
            }
        }
    }
}
