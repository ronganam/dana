package app.ganam.assistant.ui.screens.inbox

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Apps
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Deselect
import androidx.compose.material.icons.filled.DoneAll
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.SelectAll
import androidx.compose.material.icons.filled.Snooze
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.compositeOver
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.state.ToggleableState
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import app.ganam.assistant.data.models.CallSuggestion
import app.ganam.assistant.data.models.InboxSummary
import app.ganam.assistant.data.models.PhoneNotification
import app.ganam.assistant.data.notifications.AppIcons
import app.ganam.assistant.data.notifications.DanaNotificationListener
import app.ganam.assistant.data.notifications.NotificationSettings
import app.ganam.assistant.data.notifications.Snoozer
import app.ganam.assistant.ui.components.GlassCard
import app.ganam.assistant.ui.components.InkChip
import app.ganam.assistant.ui.components.MonoLabel
import app.ganam.assistant.ui.components.Rule
import app.ganam.assistant.ui.components.ScreenTopBar
import app.ganam.assistant.ui.components.SuggestionEditSheet
import app.ganam.assistant.ui.components.SuggestionRow
import app.ganam.assistant.ui.components.SuggestionSourceBlock
import app.ganam.assistant.ui.components.relativeAge
import app.ganam.assistant.ui.screens.common.QuietEmptyState
import app.ganam.assistant.ui.theme.Danger
import app.ganam.assistant.ui.theme.Space
import app.ganam.assistant.ui.theme.Warning
import app.ganam.assistant.ui.viewmodels.InboxView
import app.ganam.assistant.ui.viewmodels.MainViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private const val SUGGESTIONS_COLLAPSED = 2
/** Unfolded Fold / tablet: list + detail side by side. */
private val TWO_PANE_MIN_WIDTH = 600.dp

/** One app's notifications in the current view. */
private data class AppGroup(val pkg: String, val appName: String, val items: List<PhoneNotification>) {
    val needsYou get() = items.count { it.bucket == "needs_you" }
    val latest get() = items.maxByOrNull { it.postedAt ?: "" }
}

private fun groupByApp(list: List<PhoneNotification>): List<AppGroup> =
    list.groupBy { it.packageName }
        .map { (pkg, l) -> AppGroup(pkg, l.first().appName, l.sortedByDescending { it.postedAt ?: "" }) }
        // Apps with something that needs you first, then the most recent.
        .sortedWith(compareByDescending<AppGroup> { it.needsYou > 0 }.thenByDescending { it.latest?.postedAt ?: "" })

private fun PhoneNotification.matches(q: String): Boolean {
    val needle = q.trim()
    if (needle.isEmpty()) return true
    return listOf(appName, title, text, sender, conversation, category?.replace('_', ' '))
        .any { it?.contains(needle, ignoreCase = true) == true }
}

/**
 * Inbox: phone notifications triaged by Dana, plus every pending suggestion
 * (from calls and notifications).
 *
 *  - Views: Needs you / Everything / Suggested to dismiss / Dismissed.
 *  - Quick search filters the current view; "Select all" / "Dismiss" act on the matches.
 *  - Group by app: one section per app with its icon, count and a one-tap
 *    "dismiss this app"; sections fold.
 *  - Long-press starts selection (select all / none, whole apps from their
 *    header, dismiss, per-app rules). Swipe dismisses one. Everything has Undo.
 *  - Unfolded (≥ 600dp): list and detail side by side; with nothing open the
 *    right pane shows the apps by count.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun InboxScreen(viewModel: MainViewModel) {
    val c = viewModel.notificationsController
    val items by c.items.collectAsState()
    val history by c.history.collectAsState()
    val snoozedList by c.snoozed.collectAsState()
    val counts by c.counts.collectAsState()
    val suggestions by c.suggestions.collectAsState()
    val busy by c.busySuggestionIds.collectAsState()
    val loading by c.loading.collectAsState()
    val loadedOnce by c.loadedOnce.collectAsState()
    val view by c.view.collectAsState()
    val accessGranted by c.accessGranted.collectAsState()
    val byApp by c.groupByApp.collectAsState()
    val summaries by c.summaries.collectAsState()

    val context = LocalContext.current
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    var selection by rememberSaveable { mutableStateOf(setOf<String>()) }
    var openId by rememberSaveable { mutableStateOf<String?>(null) }
    /** The suggestion being reviewed (source + edit before creating). */
    var reviewId by rememberSaveable { mutableStateOf<String?>(null) }
    val taskGroups by viewModel.taskGroups.collectAsState()
    var suggestionsExpanded by rememberSaveable { mutableStateOf(false) }
    /** null = the view's default (open under Needs you, folded elsewhere). */
    var suggestionsOpen by rememberSaveable { mutableStateOf<Boolean?>(null) }
    var confirmStopPkg by remember { mutableStateOf<PhoneNotification?>(null) }
    var query by rememberSaveable { mutableStateOf("") }
    /** Package picked from the apps pane: only that app's notifications. */
    var appFilter by rememberSaveable { mutableStateOf<String?>(null) }
    var expandedApps by rememberSaveable { mutableStateOf(setOf<String>()) }
    var menuOpen by remember { mutableStateOf(false) }
    var snoozeMenuOpen by remember { mutableStateOf(false) }

    // Poll while visible; re-check notification access when returning from Settings.
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val obs = LifecycleEventObserver { _, e ->
            when (e) {
                Lifecycle.Event.ON_RESUME -> c.startPolling()
                Lifecycle.Event.ON_PAUSE -> c.stopPolling()
                else -> {}
            }
        }
        lifecycle.addObserver(obs)
        if (lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) c.startPolling()
        onDispose { lifecycle.removeObserver(obs); c.stopPolling() }
    }

    fun undoable(message: String, undo: () -> Unit) {
        scope.launch {
            snackbar.currentSnackbarData?.dismiss()
            val r = snackbar.showSnackbar(message, actionLabel = "Undo", withDismissAction = true, duration = SnackbarDuration.Short)
            if (r == SnackbarResult.ActionPerformed) undo()
        }
    }

    fun dismissIds(ids: Collection<String>, label: String? = null) {
        if (ids.isEmpty()) return
        val undo = c.dismiss(ids)
        if (openId in ids) openId = null
        selection = selection - ids.toSet()
        undoable(label ?: if (ids.size == 1) "Dismissed" else "Dismissed ${ids.size}") { undo() }
    }

    fun snoozeList(list: List<PhoneNotification>, opt: Snoozer.Option) {
        if (list.isEmpty()) return
        val ids = list.map { it.id }.toSet()
        val undo = c.snooze(list, opt.untilMillis)
        if (openId in ids) openId = null
        selection = selection - ids
        undoable("Snoozed ${if (list.size == 1) "" else "${list.size} "}${Snoozer.untilLabel(opt.untilMillis)}") { undo() }
    }

    val base: List<PhoneNotification> = when (view) {
        InboxView.NEEDS_YOU -> items.filter { it.bucket == "needs_you" || it.pendingSuggestions.isNotEmpty() }
        InboxView.ALL -> items
        InboxView.SUGGESTED_DISMISS -> items.filter { it.bucket == "dismiss" }
        InboxView.SNOOZED -> snoozedList
        InboxView.HISTORY -> history
    }
    // Filtering = a search and/or one app picked; the list then shows only the matches.
    val searching = query.isNotBlank() || appFilter != null
    val appName = appFilter?.let { pkg -> (items + history + snoozedList).firstOrNull { it.packageName == pkg }?.appName ?: pkg }
    val shown = base
        .let { l -> appFilter?.let { pkg -> l.filter { it.packageName == pkg } } ?: l }
        .let { l -> if (query.isNotBlank()) l.filter { it.matches(query) } else l }
    val shownIds = shown.map { it.id }.toSet()
    // Selection only ever covers what is on screen.
    LaunchedEffect(shownIds) { if (selection.any { it !in shownIds }) selection = selection intersect shownIds }
    val selectionMode = selection.isNotEmpty()
    val isHistory = view == InboxView.HISTORY
    val isSnoozedView = view == InboxView.SNOOZED
    val allSelected = shown.isNotEmpty() && selection.size == shown.size
    /** Summaries cover the active notifications: not shown over snoozed / dismissed ones. */
    val summariesApply = !isHistory && !isSnoozedView

    fun toggle(id: String) { selection = selection.toggle(id) }

    var wideLayout by remember { mutableStateOf(false) }

    // Details: in a sheet on the phone layout, in the right pane when unfolded.
    val Detail: @Composable (PhoneNotification) -> Unit = { n ->
        NotificationDetail(
            n = n,
            busy = busy,
            onAccept = { c.acceptSuggestion(it) },
            onDismissSuggestion = { c.dismissSuggestion(it) },
            onAskSuggestion = { openId = null; viewModel.openContextChat("suggestion", it.id) },
            onReviewSuggestion = { s -> if (!wideLayout) openId = null; reviewId = s.id },
            onAsk = { openId = null; viewModel.openContextChat("notification", n.id) },
            onDismiss = { dismissIds(listOf(n.id)) },
            onRestore = { openId = null; c.restore(listOf(n)) },
            onRule = { action -> c.setRule(n, action) },
            onStopCapturing = { confirmStopPkg = n },
            onSnooze = { opt -> snoozeList(listOf(n), opt) },
            onWake = { openId = null; c.wake(listOf(n)) }
        )
    }

    BoxWithConstraints(Modifier.fillMaxSize()) {
        val wide = maxWidth >= TWO_PANE_MIN_WIDTH
        SideEffect { wideLayout = wide }

        val rowClick: (PhoneNotification) -> Unit = { n -> if (selectionMode) toggle(n.id) else openId = n.id }

        // ---- the list (both layouts) ----
        val listContent: LazyListScope.() -> Unit = {
            if (!accessGranted) {
                item("access") { AccessCard(onAllow = { NotificationSettings.openAccessSettings(context) }) }
            }
            item("views") {
                ViewChips(view, counts.needsYou, counts.active, counts.dismiss, counts.snoozed, onSelect = {
                    c.setView(it); selection = emptySet()
                })
            }
            item("tools") {
                Toolbar(
                    query = query,
                    onQuery = { query = it },
                    byApp = byApp,
                    onByApp = { c.setGroupByApp(it) }
                )
                appFilter?.let { pkg ->
                    InputChip(
                        selected = true,
                        onClick = { appFilter = null },
                        label = { Text("Only ${appName ?: pkg}") },
                        leadingIcon = { AppIcon(pkg, "🔔", 18.dp) },
                        trailingIcon = { Icon(Icons.Filled.Close, contentDescription = "Show all apps", modifier = Modifier.size(16.dp)) },
                        modifier = Modifier.padding(horizontal = Space.screen)
                    )
                    if (summariesApply) summaries.apps[pkg]?.let { sm ->
                        SummaryCard(sm, label = "Summary", modifier = Modifier.padding(horizontal = Space.screen, vertical = Space.xs))
                    }
                }
            }

            if (searching) {
                item("matches") {
                    SectionHeader(
                        if (shown.isEmpty()) "No matches" else "${shown.size} match${if (shown.size == 1) "" else "es"}",
                        actions = if (shown.isEmpty()) emptyList() else buildList {
                            add("Select all" to { selection = shownIds })
                            if (!isHistory) add("Dismiss ${shown.size}" to {
                                val what = listOfNotNull(appName, query.trim().takeIf { it.isNotEmpty() }?.let { "“$it”" }).joinToString(" · ")
                                dismissIds(shownIds, "Dismissed ${shown.size} · $what")
                            })
                        }
                    )
                }
            }

            // The Inbox at a glance (AI), above everything else.
            val briefing = summaries.overall?.takeIf { !it.summary.isNullOrBlank() }
            if (!searching && (view == InboxView.NEEDS_YOU || view == InboxView.ALL) && briefing != null) {
                item("briefing") {
                    SummaryCard(briefing, label = "At a glance", modifier = Modifier.padding(horizontal = Space.screen, vertical = Space.sm))
                }
            }

            // Suggestions: every pending one, from calls and notifications.
            if (!searching && (view == InboxView.NEEDS_YOU || view == InboxView.ALL) && suggestions.isNotEmpty()) {
                // Folded by default under Everything so the notifications stay on the first screen.
                val open = suggestionsOpen ?: (view == InboxView.NEEDS_YOU)
                item("sugg-head") {
                    SectionHeader(
                        "Suggestions · ${suggestions.size}",
                        expanded = open,
                        onToggle = { suggestionsOpen = !open },
                        actions = listOf("Dismiss all" to {
                            val all = suggestions
                            c.dismissSuggestions(all)
                            scope.launch { snackbar.showSnackbar("Dismissed ${all.size} suggestions", withDismissAction = true) }
                        })
                    )
                }
                val visible = when {
                    !open -> emptyList()
                    suggestionsExpanded -> suggestions
                    else -> suggestions.take(SUGGESTIONS_COLLAPSED)
                }
                items(visible, key = { "s-" + it.id }) { s ->
                    SuggestionRow(
                        suggestion = s,
                        busy = s.id in busy,
                        onAccept = { c.acceptSuggestion(s) },
                        onAsk = { viewModel.openContextChat("suggestion", s.id) },
                        onDismiss = { c.dismissSuggestion(s) },
                        onOpen = { reviewId = s.id },
                        horizontalPadding = Space.screen
                    )
                }
                if (open && suggestions.size > SUGGESTIONS_COLLAPSED) {
                    item("sugg-more") {
                        TextButton(onClick = { suggestionsExpanded = !suggestionsExpanded }, modifier = Modifier.padding(horizontal = Space.sm)) {
                            Text(if (suggestionsExpanded) "Show fewer" else "Show all ${suggestions.size}")
                        }
                    }
                }
                item("sugg-gap") { Spacer(Modifier.height(Space.sm)) }
            }

            if (!searching && view == InboxView.SUGGESTED_DISMISS && shown.isNotEmpty()) {
                item("dismiss-all") {
                    FilledTonalButton(
                        onClick = {
                            val (n, undo) = c.dismissAllSuggested()
                            selection = emptySet()
                            undoable("Dismissed $n") { undo() }
                        },
                        modifier = Modifier.fillMaxWidth().padding(horizontal = Space.screen, vertical = Space.sm).heightIn(min = 48.dp)
                    ) { Text("Dismiss all ${shown.size}") }
                }
            }

            if (shown.isEmpty()) {
                if (!searching) item("empty") {
                    when {
                        !loadedOnce && loading -> Box(Modifier.fillMaxWidth().padding(40.dp), contentAlignment = Alignment.Center) {
                            CircularProgressIndicator()
                        }
                        view == InboxView.NEEDS_YOU -> QuietEmptyState(
                            "Nothing needs you.",
                            if (counts.active > 0) "${counts.active} other notifications are under Everything." else "New notifications land here once Dana sorts them."
                        )
                        view == InboxView.SUGGESTED_DISMISS -> QuietEmptyState("All clear.", "Nothing to dismiss right now.")
                        isSnoozedView -> QuietEmptyState("Nothing snoozed.", "Snooze a notification and it comes back here, and on your phone, when it's due.")
                        isHistory -> QuietEmptyState("No history yet.", "Notifications you dismiss or open show up here.")
                        else -> QuietEmptyState(
                            "No notifications.",
                            if (accessGranted) "New ones appear here within a few seconds." else "Allow notification access to start."
                        )
                    }
                }
            } else if (byApp) {
                groupByApp(shown).forEach { g ->
                    // Searching opens every matching app; otherwise sections fold.
                    val expanded = searching || g.pkg in expandedApps
                    val ids = g.items.map { it.id }
                    val picked = ids.count { it in selection }
                    item("g-" + g.pkg) {
                        AppGroupHeader(
                            group = g,
                            summary = summaries.apps[g.pkg].takeIf { summariesApply },
                            expanded = expanded,
                            selectionMode = selectionMode,
                            selectState = when (picked) {
                                0 -> ToggleableState.Off
                                ids.size -> ToggleableState.On
                                else -> ToggleableState.Indeterminate
                            },
                            onToggleExpand = {
                                if (selectionMode) selection = if (picked == ids.size) selection - ids.toSet() else selection + ids
                                else expandedApps = expandedApps.toggle(g.pkg)
                            },
                            onSelectGroup = { selection = if (picked == ids.size) selection - ids.toSet() else selection + ids },
                            onDismissGroup = if (isHistory) null else ({ dismissIds(ids, "Dismissed ${ids.size} from ${g.appName}") })
                        )
                    }
                    if (expanded) {
                        items(g.items, key = { it.id }) { n ->
                            NotificationItem(
                                n, isHistory, selection, openId.takeIf { wide }, showApp = false, indent = true,
                                onClick = { rowClick(n) }, onLongClick = { toggle(n.id) },
                                onSwipe = { dismissIds(listOf(n.id)) }
                            )
                        }
                    }
                }
            } else if (view == InboxView.ALL && !searching) {
                listOf(
                    "Needs you" to shown.filter { it.bucket == "needs_you" },
                    "Other" to shown.filter { it.bucket == "normal" },
                    "Suggested to dismiss" to shown.filter { it.bucket == "dismiss" }
                ).filter { it.second.isNotEmpty() }.forEach { (label, list) ->
                    item("h-$label") {
                        SectionHeader("$label · ${list.size}", actions = listOf("Select" to { selection = selection + list.map { it.id } }))
                    }
                    items(list, key = { it.id }) { n ->
                        NotificationItem(
                            n, isHistory, selection, openId.takeIf { wide }, showApp = true, indent = false,
                            onClick = { rowClick(n) }, onLongClick = { toggle(n.id) },
                            onSwipe = { dismissIds(listOf(n.id)) }
                        )
                    }
                }
            } else {
                items(shown, key = { it.id }) { n ->
                    NotificationItem(
                        n, isHistory, selection, openId.takeIf { wide }, showApp = true, indent = false,
                        onClick = { rowClick(n) }, onLongClick = { toggle(n.id) },
                        onSwipe = { dismissIds(listOf(n.id)) }
                    )
                }
            }
        }

        Scaffold(
            containerColor = Color.Transparent,
            snackbarHost = { SnackbarHost(snackbar) { Snackbar(it, shape = RoundedCornerShape(12.dp)) } },
            topBar = {
                if (selectionMode) {
                    ScreenTopBar(
                        title = "${selection.size} selected",
                        showSettings = false,
                        navigationIcon = {
                            IconButton(onClick = { selection = emptySet() }) { Icon(Icons.Filled.Close, contentDescription = "Clear selection") }
                        },
                        actions = {
                            if (allSelected) {
                                IconButton(onClick = { selection = emptySet() }) { Icon(Icons.Filled.Deselect, contentDescription = "Deselect all") }
                            } else {
                                IconButton(onClick = { selection = shownIds }) { Icon(Icons.Filled.SelectAll, contentDescription = "Select all") }
                            }
                            if (isSnoozedView) {
                                TextButton(onClick = {
                                    c.wake(snoozedList.filter { it.id in selection }); selection = emptySet()
                                }) { Text("Unsnooze") }
                            } else if (!isHistory) {
                                Box {
                                    IconButton(onClick = { snoozeMenuOpen = true }) { Icon(Icons.Filled.Snooze, contentDescription = "Snooze selected") }
                                    DropdownMenu(expanded = snoozeMenuOpen, onDismissRequest = { snoozeMenuOpen = false }) {
                                        Snoozer.options().forEach { opt ->
                                            DropdownMenuItem(
                                                text = { Text(opt.label) },
                                                onClick = { snoozeMenuOpen = false; snoozeList(shown.filter { it.id in selection }, opt) }
                                            )
                                        }
                                    }
                                }
                            }
                            if (!isHistory) {
                                IconButton(onClick = { dismissIds(selection) }) {
                                    Icon(Icons.Filled.DoneAll, contentDescription = "Dismiss selected")
                                }
                                Box {
                                    IconButton(onClick = { menuOpen = true }) { Icon(Icons.Filled.MoreVert, contentDescription = "More") }
                                    val pkgs = shown.filter { it.id in selection }.map { it.packageName }.distinct()
                                    val apps = if (pkgs.size == 1) "this app" else "these ${pkgs.size} apps"
                                    DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                                        DropdownMenuItem(
                                            text = { Text("Always suggest dismissing $apps") },
                                            onClick = { menuOpen = false; c.setRuleForApps(pkgs, "mute"); selection = emptySet() }
                                        )
                                        DropdownMenuItem(
                                            text = { Text("$apps always need me".replaceFirstChar { it.uppercase() }) },
                                            onClick = { menuOpen = false; c.setRuleForApps(pkgs, "important"); selection = emptySet() }
                                        )
                                        DropdownMenuItem(
                                            text = { Text("Sort $apps normally") },
                                            onClick = { menuOpen = false; c.setRuleForApps(pkgs, "none"); selection = emptySet() }
                                        )
                                    }
                                }
                            } else {
                                TextButton(onClick = {
                                    c.restore(history.filter { it.id in selection }); selection = emptySet()
                                }) { Text("Restore") }
                            }
                        }
                    )
                } else {
                    ScreenTopBar(
                        title = "Inbox",
                        kicker = buildList {
                            if (counts.needsYou > 0) add("${counts.needsYou} need you")
                            if (suggestions.isNotEmpty()) add("${suggestions.size} suggested")
                            if (isEmpty() && counts.active > 0) add("${counts.active} notifications")
                        }.joinToString(" · ").ifEmpty { null },
                        actions = {
                            IconButton(onClick = { c.fetch() }) {
                                if (loading) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                                else Icon(Icons.Filled.Refresh, contentDescription = "Refresh")
                            }
                        }
                    )
                }
            }
        ) { padding ->
            if (wide) {
                Row(Modifier.padding(padding).fillMaxSize()) {
                    LazyColumn(
                        Modifier.weight(0.46f).fillMaxHeight(),
                        contentPadding = PaddingValues(bottom = 24.dp),
                        content = listContent
                    )
                    Box(Modifier.width(1.dp).fillMaxHeight().background(MaterialTheme.colorScheme.outlineVariant))
                    Box(Modifier.weight(0.54f).fillMaxHeight()) {
                        val open = openId?.let { id -> items.firstOrNull { it.id == id } ?: history.firstOrNull { it.id == id } }
                        if (open != null) {
                            Column(Modifier.fillMaxSize()) {
                                Row(Modifier.fillMaxWidth().padding(start = Space.screen, end = Space.xs), verticalAlignment = Alignment.CenterVertically) {
                                    MonoLabel("Details", modifier = Modifier.weight(1f))
                                    IconButton(onClick = { openId = null }) { Icon(Icons.Filled.Close, contentDescription = "Close details") }
                                }
                                Detail(open)
                            }
                        } else {
                            AppsOverview(
                                groups = groupByApp(base),
                                summaries = if (summariesApply) summaries.apps else emptyMap(),
                                isHistory = isHistory,
                                onShow = { g -> appFilter = g.pkg; query = "" },
                                onDismiss = { g -> dismissIds(g.items.map { it.id }, "Dismissed ${g.items.size} from ${g.appName}") }
                            )
                        }
                    }
                }
            } else {
                LazyColumn(
                    Modifier.padding(padding).fillMaxSize(),
                    contentPadding = PaddingValues(bottom = 24.dp),
                    content = listContent
                )
            }
        }

        // Phone layout: details in a sheet.
        if (!wide) {
            val open = openId?.let { id -> items.firstOrNull { it.id == id } ?: history.firstOrNull { it.id == id } }
            if (openId != null && open == null && loadedOnce) openId = null
            open?.let { n ->
                ModalBottomSheet(
                    onDismissRequest = { openId = null },
                    containerColor = MaterialTheme.colorScheme.surface,
                    shape = RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp)
                ) { Detail(n) }
            }
        }
    }

    // Review a suggestion: where it came from, and every field editable before creating it.
    val reviewing = reviewId?.let { id ->
        suggestions.firstOrNull { it.id == id } ?: (items + history).firstNotNullOfOrNull { n -> n.pendingSuggestions.firstOrNull { it.id == id } }
    }
    if (reviewId != null && reviewing == null && loadedOnce) reviewId = null
    reviewing?.let { s ->
        ModalBottomSheet(
            onDismissRequest = { reviewId = null },
            sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
            containerColor = MaterialTheme.colorScheme.surface,
            shape = RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp)
        ) {
            SuggestionEditSheet(
                suggestion = s,
                groups = taskGroups.map { it.name },
                busy = s.id in busy,
                onCreate = { overrides -> c.acceptSuggestion(s, overrides); reviewId = null },
                onAsk = { reviewId = null; viewModel.openContextChat("suggestion", s.id) },
                onCancel = { reviewId = null },
                onDismiss = { c.dismissSuggestion(s); reviewId = null },
                sourceBlock = {
                    SuggestionSourceBlock(
                        suggestion = s,
                        icon = {
                            val pkg = s.source?.packageName
                            if (s.fromNotification && pkg != null) AppIcon(pkg, "🔔", 36.dp)
                            else Box(Modifier.size(36.dp).clip(CircleShape).background(MaterialTheme.colorScheme.primary.copy(alpha = 0.14f)),
                                contentAlignment = Alignment.Center) { Text(if (s.fromNotification) "🔔" else "📞") }
                        },
                        onOpen = when {
                            s.fromNotification -> ({ reviewId = null; openId = s.sourceId })
                            s.callId.isNotEmpty() -> ({ reviewId = null; viewModel.openCall(s.callId) })
                            else -> null
                        }
                    )
                }
            )
        }
    }

    confirmStopPkg?.let { n ->
        AlertDialog(
            onDismissRequest = { confirmStopPkg = null },
            title = { Text("Stop capturing ${n.appName}?") },
            text = { Text("Dana stops reading ${n.appName} notifications on this phone and deletes the ones it has (except any a reminder was made from). You can allow it again in Settings.") },
            confirmButton = {
                TextButton(onClick = { c.stopCapturing(n.packageName); confirmStopPkg = null; openId = null }) {
                    Text("Stop and delete", color = Danger)
                }
            },
            dismissButton = { TextButton(onClick = { confirmStopPkg = null }) { Text("Cancel") } }
        )
    }
}

private fun Set<String>.toggle(id: String) = if (id in this) this - id else this + id

@Composable
private fun AccessCard(onAllow: () -> Unit) {
    GlassCard(Modifier.fillMaxWidth().padding(horizontal = Space.screen, vertical = Space.sm)) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Let Dana sort your notifications", style = MaterialTheme.typography.titleSmall)
            Text(
                "Allow notification access and Dana will flag what needs you, suggest reminders from them, and gather the noise for one-tap dismissal. " +
                    "Banking and authenticator apps are skipped, and one-time codes never leave the phone.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
            Button(onClick = onAllow, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) { Text("Allow notification access") }
        }
    }
}

@Composable
private fun ViewChips(view: InboxView, needsYou: Int, active: Int, dismiss: Int, snoozed: Int, onSelect: (InboxView) -> Unit) {
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = Space.screen, vertical = Space.xs),
        horizontalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        InboxView.values().forEach { v ->
            val n = when (v) {
                InboxView.NEEDS_YOU -> needsYou
                InboxView.ALL -> active
                InboxView.SUGGESTED_DISMISS -> dismiss
                InboxView.SNOOZED -> snoozed
                InboxView.HISTORY -> -1
            }
            FilterChip(
                selected = v == view,
                onClick = { onSelect(v) },
                label = { Text(if (n > 0) "${v.label} · $n" else v.label) }
            )
        }
    }
}

/** Quick search + "group by app" toggle. */
@Composable
private fun Toolbar(query: String, onQuery: (String) -> Unit, byApp: Boolean, onByApp: (Boolean) -> Unit) {
    val focus = LocalFocusManager.current
    Row(
        Modifier.fillMaxWidth().padding(start = Space.screen, end = Space.sm, top = 2.dp, bottom = Space.xs),
        verticalAlignment = Alignment.CenterVertically
    ) {
        OutlinedTextField(
            value = query,
            onValueChange = onQuery,
            modifier = Modifier.weight(1f).heightIn(min = 48.dp),
            placeholder = { Text("Search", maxLines = 1) },
            leadingIcon = { Icon(Icons.Filled.Search, contentDescription = null) },
            trailingIcon = if (query.isNotEmpty()) ({
                IconButton(onClick = { onQuery(""); focus.clearFocus() }) { Icon(Icons.Filled.Close, contentDescription = "Clear search") }
            }) else null,
            singleLine = true,
            shape = RoundedCornerShape(24.dp),
            textStyle = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            keyboardActions = KeyboardActions(onSearch = { focus.clearFocus() })
        )
        Spacer(Modifier.width(4.dp))
        FilledTonalIconToggleButton(checked = byApp, onCheckedChange = onByApp) {
            Icon(Icons.Filled.Apps, contentDescription = if (byApp) "Stop grouping by app" else "Group by app")
        }
    }
}

@Composable
private fun SectionHeader(
    text: String,
    actions: List<Pair<String, () -> Unit>> = emptyList(),
    expanded: Boolean? = null,
    onToggle: (() -> Unit)? = null
) {
    Row(
        Modifier.fillMaxWidth()
            .then(if (onToggle != null) Modifier.clickable(onClick = onToggle) else Modifier)
            .padding(start = Space.screen, end = Space.xs, top = Space.xs),
        verticalAlignment = Alignment.CenterVertically
    ) {
        MonoLabel(text)
        if (expanded != null) {
            Icon(
                if (expanded) Icons.Filled.ExpandLess else Icons.Filled.ExpandMore,
                contentDescription = if (expanded) "Fold" else "Unfold",
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(start = 4.dp).size(20.dp)
            )
        }
        Spacer(Modifier.weight(1f))
        actions.forEach { (label, onClick) -> TextButton(onClick = onClick) { Text(label) } }
    }
}

/** The app's real icon (see [AppIcons]); the category emoji when there is none. */
@Composable
private fun AppIcon(pkg: String, fallbackEmoji: String, size: Dp, badge: String? = null) {
    val ctx = LocalContext.current.applicationContext
    val icon by produceState<ImageBitmap?>(AppIcons.cached(pkg)?.asImageBitmap(), pkg) {
        if (value == null) value = withContext(Dispatchers.IO) { AppIcons.load(ctx, pkg) }?.asImageBitmap()
    }
    Box(Modifier.size(size)) {
        val bmp = icon
        if (bmp != null) {
            Image(bmp, contentDescription = null, modifier = Modifier.fillMaxSize().clip(RoundedCornerShape(size * 0.26f)))
        } else {
            Box(
                Modifier.fillMaxSize().clip(CircleShape).background(MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.14f)),
                contentAlignment = Alignment.Center
            ) { Text(fallbackEmoji, fontSize = (size.value * 0.45f).sp) }
        }
        if (badge != null && bmp != null) {
            Box(
                Modifier.align(Alignment.BottomEnd).offset(x = 3.dp, y = 3.dp).size(size * 0.48f)
                    .clip(CircleShape).background(MaterialTheme.colorScheme.surface),
                contentAlignment = Alignment.Center
            ) { Text(badge, fontSize = (size.value * 0.26f).sp) }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun AppGroupHeader(
    group: AppGroup,
    summary: InboxSummary?,
    expanded: Boolean,
    selectionMode: Boolean,
    selectState: ToggleableState,
    onToggleExpand: () -> Unit,
    onSelectGroup: () -> Unit,
    onDismissGroup: (() -> Unit)?
) {
    val latest = group.latest
    Column(
        Modifier.fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceContainerLow)
            .combinedClickable(onClick = onToggleExpand, onLongClick = onSelectGroup)
    ) {
        Row(Modifier.padding(start = Space.screen, end = 4.dp, top = 8.dp, bottom = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            if (selectionMode) {
                TriStateCheckbox(state = selectState, onClick = onSelectGroup, modifier = Modifier.size(40.dp))
            } else {
                AppIcon(group.pkg, latest?.categoryEmoji ?: "🔔", 36.dp)
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        group.appName, style = MaterialTheme.typography.titleSmall,
                        maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false)
                    )
                    Spacer(Modifier.width(8.dp))
                    CountPill(group.items.size, if (group.needsYou > 0) Warning else null)
                    if (group.needsYou > 0 && group.needsYou < group.items.size) {
                        Spacer(Modifier.width(6.dp))
                        Text("${group.needsYou} need you", style = MaterialTheme.typography.labelSmall, color = Warning)
                    }
                }
                if (summary != null && summary.points.isNotEmpty()) {
                    // Folded: the first two points on a line each; unfolded: all of them.
                    SummaryPoints(summary.points, stale = summary.stale,
                        maxPoints = if (expanded) 4 else 2, maxLinesEach = if (expanded) 2 else 1)
                } else if (!expanded && latest != null) {
                    Text(
                        listOfNotNull(latest.title, latest.text).joinToString(": ").replace('\n', ' '),
                        style = MaterialTheme.typography.bodySmall.copy(textDirection = TextDirection.Content),
                        color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis
                    )
                }
            }
            if (onDismissGroup != null && !selectionMode) {
                IconButton(onClick = onDismissGroup) {
                    Icon(Icons.Filled.DoneAll, contentDescription = "Dismiss all from ${group.appName}", tint = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            if (!selectionMode) {
                Icon(
                    if (expanded) Icons.Filled.ExpandLess else Icons.Filled.ExpandMore,
                    contentDescription = if (expanded) "Fold" else "Unfold",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(end = 8.dp)
                )
            }
        }
        Rule()
    }
}

/**
 * A point reads right to left when most of its letters are Hebrew / Arabic —
 * not just the first one: "אלייה needs a different date" is an English line.
 */
private fun startsRtl(s: String): Boolean {
    var rtl = 0
    var ltr = 0
    for (ch in s) {
        when (Character.getDirectionality(ch)) {
            Character.DIRECTIONALITY_RIGHT_TO_LEFT, Character.DIRECTIONALITY_RIGHT_TO_LEFT_ARABIC -> rtl++
            Character.DIRECTIONALITY_LEFT_TO_RIGHT -> ltr++
        }
    }
    return rtl > ltr
}

/**
 * AI summary points, one row each. A Hebrew point is laid out right to left
 * (bullet on the right), an English one left to right, so mixed lists read
 * naturally.
 */
@Composable
private fun SummaryPoints(
    points: List<String>,
    stale: Boolean,
    maxPoints: Int,
    maxLinesEach: Int,
    style: androidx.compose.ui.text.TextStyle = MaterialTheme.typography.bodySmall,
    modifier: Modifier = Modifier
) {
    Column(modifier.padding(top = 2.dp).alpha(if (stale) 0.6f else 1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
        points.take(maxPoints).forEach { p ->
            val rtl = startsRtl(p)
            CompositionLocalProvider(LocalLayoutDirection provides if (rtl) LayoutDirection.Rtl else LayoutDirection.Ltr) {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.Top) {
                    Text("•", style = style, color = MaterialTheme.colorScheme.primary)
                    Spacer(Modifier.width(6.dp))
                    Text(
                        p, style = style.copy(textDirection = if (rtl) TextDirection.Rtl else TextDirection.Ltr),
                        color = MaterialTheme.colorScheme.onSurface, maxLines = maxLinesEach, overflow = TextOverflow.Ellipsis
                    )
                }
            }
        }
    }
}

/** The briefing (or one app's summary) as a card of points; "updating" while the server rewrites it. */
@Composable
private fun SummaryCard(summary: InboxSummary, label: String, modifier: Modifier = Modifier) {
    var expanded by remember { mutableStateOf(false) }
    Column(
        modifier.fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(MaterialTheme.colorScheme.surfaceContainerLow)
            .clickable { expanded = !expanded }
            .padding(horizontal = 14.dp, vertical = 12.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("✦", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
            Spacer(Modifier.width(6.dp))
            MonoLabel(label + if (summary.stale) " · updating…" else "", modifier = Modifier.weight(1f))
        }
        Spacer(Modifier.height(6.dp))
        SummaryPoints(
            summary.points, stale = summary.stale, maxPoints = 6, maxLinesEach = if (expanded) 6 else 2,
            style = MaterialTheme.typography.bodyMedium
        )
    }
}

@Composable
private fun CountPill(n: Int, accent: Color?) {
    Box(
        Modifier.clip(RoundedCornerShape(10.dp))
            .background((accent ?: MaterialTheme.colorScheme.onSurfaceVariant).copy(alpha = 0.16f))
            .padding(horizontal = 7.dp, vertical = 1.dp)
    ) {
        Text("$n", style = MaterialTheme.typography.labelSmall, color = accent ?: MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun NotificationItem(
    n: PhoneNotification,
    isHistory: Boolean,
    selection: Set<String>,
    openId: String?,
    showApp: Boolean,
    indent: Boolean,
    onClick: () -> Unit,
    onLongClick: () -> Unit,
    onSwipe: () -> Unit
) {
    val row = @Composable {
        NotificationRow(
            n, selected = n.id in selection, selectionMode = selection.isNotEmpty(), highlighted = n.id == openId,
            showApp = showApp, indent = indent, onClick = onClick, onLongClick = onLongClick
        )
    }
    if (isHistory) row() else SwipeRow(selection, onDismiss = onSwipe, content = row)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SwipeRow(
    selection: Set<String>,
    onDismiss: () -> Unit,
    content: @Composable () -> Unit
) {
    val state = rememberSwipeToDismissBoxState(
        confirmValueChange = { v ->
            if (v != SwipeToDismissBoxValue.Settled && selection.isEmpty()) { onDismiss(); true } else false
        },
        positionalThreshold = { it * 0.35f }
    )
    SwipeToDismissBox(
        state = state,
        enableDismissFromStartToEnd = selection.isEmpty(),
        enableDismissFromEndToStart = selection.isEmpty(),
        backgroundContent = {
            Box(
                Modifier.fillMaxSize().background(MaterialTheme.colorScheme.surfaceContainerHigh).padding(horizontal = 24.dp),
                contentAlignment = if (state.dismissDirection == SwipeToDismissBoxValue.StartToEnd) Alignment.CenterStart else Alignment.CenterEnd
            ) {
                Icon(Icons.Outlined.Delete, contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    ) { content() }
}

private fun snoozedMillis(iso: String?): Long? =
    iso?.let { runCatching { java.time.OffsetDateTime.parse(it).toInstant().toEpochMilli() }.getOrNull() }

private fun priorityColor(p: String): Color? = when (p) {
    "urgent" -> Danger
    "important" -> Warning
    else -> null
}

/** Compact row: icon · title + time · text (2 lines) · suggestion chip. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun NotificationRow(
    n: PhoneNotification,
    selected: Boolean,
    selectionMode: Boolean,
    highlighted: Boolean,
    showApp: Boolean,
    indent: Boolean,
    onClick: () -> Unit,
    onLongClick: () -> Unit
) {
    val accent = priorityColor(n.priority)
    // Opaque, so the swipe background under the row never shows through.
    val base = MaterialTheme.colorScheme.background
    val bg = when {
        selected -> MaterialTheme.colorScheme.primary.copy(alpha = 0.12f).compositeOver(base)
        highlighted -> MaterialTheme.colorScheme.primary.copy(alpha = 0.07f).compositeOver(base)
        else -> base
    }
    Column(Modifier.fillMaxWidth().background(bg).combinedClickable(onClick = onClick, onLongClick = onLongClick)) {
        Row(Modifier.height(IntrinsicSize.Min), verticalAlignment = Alignment.Top) {
            // Priority bar.
            Box(Modifier.width(3.dp).fillMaxHeight().background(accent ?: Color.Transparent))
            Row(
                Modifier.weight(1f).padding(start = if (indent) Space.screen + 8.dp else Space.screen - 3.dp, end = Space.screen, top = 9.dp, bottom = 9.dp),
                verticalAlignment = Alignment.Top
            ) {
                if (selectionMode) {
                    Checkbox(checked = selected, onCheckedChange = { onClick() }, modifier = Modifier.size(32.dp))
                } else if (showApp) {
                    AppIcon(n.packageName, n.categoryEmoji, 32.dp, badge = n.categoryEmoji)
                } else {
                    Box(Modifier.size(32.dp), contentAlignment = Alignment.Center) { Text(n.categoryEmoji, fontSize = 15.sp) }
                }
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        val head = n.title?.takeIf { it.isNotBlank() } ?: n.appName
                        Text(
                            head,
                            style = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content),
                            fontWeight = FontWeight.SemiBold,
                            maxLines = 1, overflow = TextOverflow.Ellipsis,
                            color = MaterialTheme.colorScheme.onSurface,
                            modifier = Modifier.weight(1f)
                        )
                        Spacer(Modifier.width(8.dp))
                        Text(
                            relativeAge(n.postedAt).removeSuffix(" ago"),
                            style = MaterialTheme.typography.labelSmall,
                            color = accent ?: MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                    val meta = buildList {
                        if (showApp) add(n.appName)
                        when (n.priority) { "urgent" -> add("urgent"); "important" -> add("important") }
                        if (n.triageStatus == "pending") add("sorting…")
                        if (n.isGroup) n.conversation?.let { add("in $it") }
                        snoozedMillis(n.snoozedUntil)?.let { add("⏰ " + Snoozer.untilLabel(it)) }
                    }
                    if (meta.isNotEmpty()) {
                        Text(
                            meta.joinToString(" · "), style = MaterialTheme.typography.labelSmall,
                            color = accent ?: MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis
                        )
                    }
                    n.text?.takeIf { it.isNotBlank() }?.let {
                        Text(
                            it, style = MaterialTheme.typography.bodySmall.copy(textDirection = TextDirection.Content),
                            maxLines = 2, overflow = TextOverflow.Ellipsis, color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                    val pending = n.pendingSuggestions.size
                    if (pending > 0) {
                        Spacer(Modifier.height(4.dp))
                        InkChip(if (pending == 1) "✅ ${n.pendingSuggestions[0].title}" else "✅ $pending suggestions", selected = true)
                    }
                }
            }
        }
        Rule()
    }
}

/** Unfolded, nothing open: the current view's apps by count, each one tap from filtered or dismissed. */
@Composable
private fun AppsOverview(
    groups: List<AppGroup>,
    summaries: Map<String, InboxSummary>,
    isHistory: Boolean,
    onShow: (AppGroup) -> Unit,
    onDismiss: (AppGroup) -> Unit
) {
    if (groups.isEmpty()) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Text("Nothing here.", color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        return
    }
    val sorted = groups.sortedByDescending { it.items.size }
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 24.dp)) {
        item("apps-head") { SectionHeader("${sorted.size} apps · tap one to show only it") }
        items(sorted, key = { "o-" + it.pkg }) { g ->
            Row(
                Modifier.fillMaxWidth().clickable { onShow(g) }.padding(start = Space.screen, end = Space.xs, top = 6.dp, bottom = 6.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                AppIcon(g.pkg, g.latest?.categoryEmoji ?: "🔔", 36.dp)
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Text(g.appName, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    val meta = buildList {
                        add("${g.items.size} notification${if (g.items.size == 1) "" else "s"}")
                        if (g.needsYou > 0) add("${g.needsYou} need you")
                        g.latest?.postedAt?.let { relativeAge(it).takeIf { a -> a.isNotEmpty() }?.let { a -> add("latest $a") } }
                    }
                    Text(meta.joinToString(" · "), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    summaries[g.pkg]?.takeIf { it.points.isNotEmpty() }?.let { sm ->
                        SummaryPoints(sm.points, stale = sm.stale, maxPoints = 4, maxLinesEach = 2)
                    }
                }
                if (!isHistory) TextButton(onClick = { onDismiss(g) }) { Text("Dismiss all") }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun NotificationDetail(
    n: PhoneNotification,
    busy: Set<String>,
    onAccept: (CallSuggestion) -> Unit,
    onDismissSuggestion: (CallSuggestion) -> Unit,
    onAskSuggestion: (CallSuggestion) -> Unit,
    onReviewSuggestion: (CallSuggestion) -> Unit,
    onAsk: () -> Unit,
    onDismiss: () -> Unit,
    onRestore: () -> Unit,
    onRule: (String) -> Unit,
    onStopCapturing: () -> Unit,
    onSnooze: (Snoozer.Option) -> Unit,
    onWake: () -> Unit
) {
    Column(
        Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = Space.screen).padding(bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            AppIcon(n.packageName, n.categoryEmoji, 40.dp)
            Spacer(Modifier.width(12.dp))
            Column {
                Text(n.appName, style = MaterialTheme.typography.titleSmall)
                MonoLabel(
                    listOfNotNull(
                        relativeAge(n.postedAt).takeIf { it.isNotEmpty() },
                        n.category?.let { "${n.categoryEmoji} ${it.replace('_', ' ')}" }
                    ).joinToString(" · "),
                    color = priorityColor(n.priority) ?: MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
        n.title?.let { Text(it, style = MaterialTheme.typography.titleLarge.copy(textDirection = TextDirection.Content)) }
        if (n.isGroup && n.conversation != null) {
            Text("in ${n.conversation}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        n.text?.let {
            Text(it, style = MaterialTheme.typography.bodyLarge.copy(textDirection = TextDirection.Content), color = MaterialTheme.colorScheme.onSurface)
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            InkChip("Priority: ${n.priority}")
            when (n.bucket) {
                "needs_you" -> InkChip("Needs you")
                "dismiss" -> InkChip("Suggested to dismiss")
            }
            if (n.triageStatus == "failed") InkChip("Not sorted (triage failed)")
            if (n.escalated) InkChip("⚡ Re-announced")
            n.removedReason?.let { InkChip("Removed: $it") }
        }

        if (n.status == "active") OnPhoneActions(n)

        if (n.pendingSuggestions.isNotEmpty()) {
            Spacer(Modifier.height(4.dp))
            MonoLabel("Suggested from this notification")
            n.pendingSuggestions.forEach { s ->
                SuggestionRow(
                    suggestion = s, busy = s.id in busy, showSource = false,
                    onAccept = { onAccept(s) }, onAsk = { onAskSuggestion(s) }, onDismiss = { onDismissSuggestion(s) },
                    onOpen = { onReviewSuggestion(s) }
                )
            }
        }

        Spacer(Modifier.height(4.dp))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = onAsk) { Text("Ask Dana") }
            if (n.status == "active") OutlinedButton(onClick = onDismiss) { Text("Dismiss") }
            else OutlinedButton(onClick = onRestore) { Text("Restore") }
            if (n.status == "active" && n.snoozedUntil != null) OutlinedButton(onClick = onWake) { Text("Unsnooze") }
        }
        if (n.status == "active") {
            val until = snoozedMillis(n.snoozedUntil)
            MonoLabel(if (until != null) "Snoozed ${Snoozer.untilLabel(until)} · snooze again" else "Snooze")
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Snoozer.options().forEach { opt -> InkChip(opt.label, onClick = { onSnooze(opt) }) }
            }
        }
        Rule()
        MonoLabel("${n.appName} from now on")
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            InkChip("Always needs me", onClick = { onRule("important") })
            InkChip("⚡ Alert me (full screen)", onClick = { onRule("alert") })
            InkChip("Suggest dismissing", onClick = { onRule("mute") })
            InkChip("Sort normally", onClick = { onRule("none") })
            InkChip("Stop capturing", onClick = onStopCapturing, accent = Danger)
        }
    }
}


/**
 * Open the notification or press its buttons (Reply, Mark as read, …) right
 * from the Inbox, as in the shade. Read live from the shade each time, so it
 * only offers what still works; once it's gone, Open falls back to the app.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun OnPhoneActions(n: PhoneNotification) {
    val context = LocalContext.current
    var tick by remember(n.id) { mutableIntStateOf(0) }
    val inShade = remember(n.key, tick) { DanaNotificationListener.inShade(n.key) }
    val actions = remember(n.key, tick) { DanaNotificationListener.liveActions(n.key) }
    var status by remember(n.id) { mutableStateOf<Pair<Boolean, String>?>(null) }
    var replyTo by remember(n.id) { mutableStateOf<String?>(null) }
    var reply by remember(n.id) { mutableStateOf("") }

    fun openApp() {
        val ok = if (inShade && DanaNotificationListener.open(context, n.key)) true
        else context.packageManager.getLaunchIntentForPackage(n.packageName)?.let {
            runCatching { context.startActivity(it.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)) }.isSuccess
        } ?: false
        status = if (ok) null else false to "Couldn't open ${n.appName}"
    }

    fun press(title: String, text: String = "") {
        val err = DanaNotificationListener.perform(context, n.key, title, text)
        status = if (err == null) true to (if (text.isNotBlank()) "Sent ✓" else "“$title” done ✓") else false to err
        if (err == null) { replyTo = null; reply = "" }
        tick++
    }

    Spacer(Modifier.height(2.dp))
    MonoLabel(if (inShade) "On your phone" else "Open")
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedButton(onClick = ::openApp) { Text(if (inShade) "Open" else "Open ${n.appName}") }
        actions.forEach { (title, takesText) ->
            OutlinedButton(onClick = { if (takesText) replyTo = if (replyTo == title) null else title else press(title) }) {
                Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
    }
    replyTo?.let { title ->
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(
                value = reply, onValueChange = { reply = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text(title) },
                textStyle = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content),
                maxLines = 4,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { if (reply.isNotBlank()) press(title, reply.trim()) })
            )
            Button(onClick = { press(title, reply.trim()) }, enabled = reply.isNotBlank()) { Text("Send") }
        }
    }
    status?.let { (ok, msg) ->
        Text(msg, style = MaterialTheme.typography.bodySmall, color = if (ok) MaterialTheme.colorScheme.primary else Danger)
    }
}
