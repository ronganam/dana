package io.github.ronganam.dana.ui.screens.people

import android.content.Intent
import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.PushPin
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material.icons.outlined.PushPin
import androidx.compose.material3.*
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.pulltorefresh.PullToRefreshDefaults
import androidx.compose.material3.pulltorefresh.rememberPullToRefreshState
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.models.Person
import io.github.ronganam.dana.ui.adaptive.Pane
import io.github.ronganam.dana.ui.adaptive.PaneRow
import io.github.ronganam.dana.ui.adaptive.WindowTier
import io.github.ronganam.dana.ui.adaptive.sidePaneWidth
import io.github.ronganam.dana.ui.adaptive.windowTier
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.ScreenTopBar
import io.github.ronganam.dana.ui.screens.common.QuietEmptyState
import io.github.ronganam.dana.ui.theme.Space
import io.github.ronganam.dana.ui.theme.Warning
import io.github.ronganam.dana.ui.viewmodels.MainViewModel

private val SORTS = listOf("recent" to "Recent", "drifting" to "Been a while", "name" to "A–Z")

/**
 * People Dana knows from calls and messages.
 * Cover screen: the list, a person opens full screen. Inner screen: list | person.
 */
@Composable
fun PeopleScreen(viewModel: MainViewModel, onBack: (() -> Unit)? = null) {
    val c = viewModel.peopleController
    val people by c.people.collectAsState()
    val loading by c.loading.collectAsState()
    val sort by c.sort.collectAsState()
    val query by c.query.collectAsState()
    val selectedId by c.selectedId.collectAsState()
    val detail by c.detail.collectAsState()
    val detailLoading by c.detailLoading.collectAsState()
    val briefRefreshing by c.briefRefreshing.collectAsState()
    val compact = windowTier() == WindowTier.COMPACT
    val context = LocalContext.current

    LaunchedEffect(Unit) { c.fetch() }
    // Opening the screen fresh on the inner display: show the first person instead of an empty half.
    LaunchedEffect(compact, people.isNotEmpty()) {
        if (!compact && selectedId == null) people.firstOrNull()?.let { c.select(it.id) }
    }

    val call: (String) -> Unit = { phone ->
        context.startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:+$phone")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
    val needCount = people.count { it.needsYou() }
    val kicker = buildList {
        if (people.isNotEmpty() && query.isBlank()) add("${people.size} people")
        if (needCount > 0 && query.isBlank()) add("$needCount need you")
    }.joinToString(" · ").ifBlank { null }

    val list: @Composable () -> Unit = {
        PeopleList(
            people = people, loading = loading, sort = sort, query = query,
            selectedId = if (compact) null else selectedId,
            onQuery = c::setQuery, onSort = c::setSort, onRefresh = c::fetch,
            onOpen = { c.select(it.id) },
            onPin = { c.setPinned(it, !it.pinned) },
            onCall = call,
            onHide = { c.hide(it.id) },
        )
    }
    val page: @Composable (Boolean) -> Unit = { withBack ->
        PersonPage(
            detail = detail, loading = detailLoading, briefRefreshing = briefRefreshing,
            onBack = if (withBack) ({ c.select(null) }) else null,
            onPin = { p -> c.setPinned(p, !p.pinned) },
            onRefreshBrief = c::refreshBrief,
            onAsk = { id -> viewModel.openContextChat("person", id) },
            onOpenCall = viewModel::openCall,
            onCall = call,
            onCompleteTask = { id -> viewModel.tasksController.updateTaskStatus(id, "completed"); selectedId?.let { c.reload(it) } },
            onSave = c::save,
            onHide = c::hide,
        )
    }

    if (compact) {
        if (selectedId != null) {
            BackHandler { c.select(null) }
            page(true)
        } else Column(Modifier.fillMaxSize()) {
            ScreenTopBar(
                title = "People", kicker = kicker,
                navigationIcon = onBack?.let { back -> { IconButton(onClick = back) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") } } },
            )
            list()
        }
    } else Column(Modifier.fillMaxSize()) {
        ScreenTopBar(
            title = "People", kicker = kicker,
            navigationIcon = onBack?.let { back -> { IconButton(onClick = back) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") } } },
        )
        PaneRow(Modifier.weight(1f)) {
            Pane(width = sidePaneWidth(), tinted = true) { list() }
            Pane(divider = false) {
                if (selectedId == null) QuietEmptyState(title = "Pick someone.", message = "What's going on with them, what's open between you, and your history.")
                else page(false)
            }
        }
    }
}

// ---------------------------------------------------------------- list

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun PeopleList(
    people: List<Person>, loading: Boolean, sort: String, query: String, selectedId: String?,
    onQuery: (String) -> Unit, onSort: (String) -> Unit, onRefresh: () -> Unit,
    onOpen: (Person) -> Unit, onPin: (Person) -> Unit, onCall: (String) -> Unit, onHide: (Person) -> Unit,
) {
    val searching = query.isNotBlank()
    val sections = remember(people, sort, searching) { sectionsFor(people, sort, searching) }
    val pinned = remember(people) { people.filter { it.pinned } }
    val showPinned = !searching && sort == "recent" && pinned.isNotEmpty()

    val pull = rememberPullToRefreshState()
    var pullRefreshing by remember { mutableStateOf(false) }
    LaunchedEffect(loading) { if (!loading) pullRefreshing = false }

    Column(Modifier.fillMaxSize()) {
        SearchField(query, onQuery, placeholder = if (people.isNotEmpty() && !searching) "Search ${people.size} people" else "Search people",
            modifier = Modifier.padding(horizontal = Space.screen).padding(top = Space.xs))
        TextTabs(SORTS, sort, onSort, Modifier.padding(horizontal = Space.sm, vertical = Space.xs))

        PullToRefreshBox(
        isRefreshing = pullRefreshing, onRefresh = { pullRefreshing = true; onRefresh() }, state = pull, modifier = Modifier.fillMaxSize(),
        indicator = {
            PullToRefreshDefaults.Indicator(
                state = pull, isRefreshing = pullRefreshing, modifier = Modifier.align(Alignment.TopCenter),
                containerColor = MaterialTheme.colorScheme.surfaceContainerHigh, color = MaterialTheme.colorScheme.primary
            )
        }
    ) {
            LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 24.dp)) {
                if (showPinned) {
                    item(key = "pinned") {
                        LazyRow(
                            contentPadding = PaddingValues(horizontal = Space.screen),
                            horizontalArrangement = Arrangement.spacedBy(Space.md),
                            modifier = Modifier.padding(top = Space.sm, bottom = Space.xs)
                        ) {
                            items(pinned, key = { "p" + it.id }) { p -> PinnedFace(p, selected = p.id == selectedId) { onOpen(p) } }
                        }
                    }
                }
                sections.forEach { s ->
                    item(key = "h-" + s.key) { SectionHeader(s.title, s.people.size, s.accent) }
                    items(s.people, key = { s.key + it.id }) { p ->
                        PersonRow(
                            p, selected = p.id == selectedId, showTags = s.accent || sort == "drifting",
                            onClick = { onOpen(p) }, onPin = { onPin(p) }, onCall = onCall, onHide = { onHide(p) },
                        )
                    }
                }
                if (people.isEmpty() && !loading) item {
                    QuietEmptyState(
                        title = if (searching) "No one by that name." else "No one yet.",
                        message = if (searching) "Try part of a name or a phone number." else "People show up here from your calls and WhatsApp messages."
                    )
                }
                if (people.isEmpty() && loading) item {
                    Box(Modifier.fillMaxWidth().padding(top = 48.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator(Modifier.size(28.dp), strokeWidth = 2.5.dp) }
                }
            }
        }
    }
}

@Composable
private fun SearchField(query: String, onQuery: (String) -> Unit, placeholder: String, modifier: Modifier = Modifier) {
    val focus = LocalFocusManager.current
    Row(
        modifier.fillMaxWidth().height(44.dp).clip(RoundedCornerShape(12.dp))
            .background(MaterialTheme.colorScheme.surfaceContainerHigh).padding(start = 12.dp, end = 4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(Icons.Filled.Search, null, Modifier.size(20.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.width(10.dp))
        Box(Modifier.weight(1f)) {
            if (query.isEmpty()) Text(placeholder, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
            BasicTextField(
                value = query, onValueChange = onQuery, singleLine = true,
                textStyle = MaterialTheme.typography.bodyMedium.copy(color = MaterialTheme.colorScheme.onSurface),
                cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                keyboardActions = KeyboardActions(onSearch = { focus.clearFocus() }),
                modifier = Modifier.fillMaxWidth()
            )
        }
        if (query.isNotEmpty()) IconButton(onClick = { onQuery("") }, modifier = Modifier.size(36.dp)) {
            Icon(Icons.Filled.Close, "Clear search", Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** Text tabs with an accent underline, like Reminders' Due · Priority · Group. */
@Composable
fun TextTabs(options: List<Pair<String, String>>, selected: String, onSelect: (String) -> Unit, modifier: Modifier = Modifier) {
    Row(modifier) {
        options.forEach { (id, label) ->
            val on = id == selected
            Column(
                Modifier.heightIn(min = 44.dp).clip(RoundedCornerShape(8.dp)).clickable { onSelect(id) }.padding(horizontal = 8.dp),
                horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center
            ) {
                Text(label, style = MaterialTheme.typography.titleSmall, maxLines = 1,
                    color = if (on) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant)
                Box(Modifier.padding(top = 4.dp).height(2.dp).width(18.dp).background(if (on) MaterialTheme.colorScheme.primary else Color.Transparent))
            }
        }
    }
}

@Composable
private fun SectionHeader(title: String, count: Int, accent: Boolean) {
    Row(
        Modifier.fillMaxWidth().padding(start = Space.screen, end = Space.screen, top = Space.lg, bottom = Space.xs),
        verticalAlignment = Alignment.CenterVertically
    ) {
        MonoLabel(title, Modifier.weight(1f), color = if (accent) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
        MonoLabel(count.toString())
    }
}

@Composable
private fun PinnedFace(p: Person, selected: Boolean, onClick: () -> Unit) {
    Column(
        Modifier.width(64.dp).clip(RoundedCornerShape(12.dp)).clickable(onClick = onClick).padding(vertical = 6.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        PersonAvatar(p, size = 52.dp, ring = selected)
        Spacer(Modifier.height(6.dp))
        Text(firstName(p.name), style = MaterialTheme.typography.labelLarge, maxLines = 1, overflow = TextOverflow.Ellipsis,
            textAlign = TextAlign.Center, color = MaterialTheme.colorScheme.onSurface)
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun PersonRow(
    p: Person, selected: Boolean, showTags: Boolean,
    onClick: () -> Unit, onPin: () -> Unit, onCall: (String) -> Unit, onHide: () -> Unit,
) {
    var menu by remember { mutableStateOf(false) }
    val selBg = MaterialTheme.colorScheme.surfaceContainerHighest
    Box {
        Row(
            Modifier.fillMaxWidth()
                .padding(horizontal = Space.sm)
                .clip(RoundedCornerShape(12.dp))
                .background(if (selected) selBg else Color.Transparent)
                .combinedClickable(onClick = onClick, onLongClick = { menu = true })
                .padding(horizontal = Space.sm, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            PersonAvatar(p, size = 44.dp)
            Spacer(Modifier.width(Space.md))
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Row(Modifier.weight(1f), verticalAlignment = Alignment.CenterVertically) {
                        Text(p.name, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.onSurface,
                            maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
                        if (p.pinned) {
                            Spacer(Modifier.width(5.dp))
                            Icon(Icons.Filled.PushPin, "Pinned", Modifier.size(12.dp), tint = MaterialTheme.colorScheme.primary)
                        }
                        if (p.relation.isNotBlank()) {
                            Spacer(Modifier.width(6.dp))
                            Text(p.relation, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
                        }
                    }
                    Spacer(Modifier.width(Space.sm))
                    Text(shortWhen(p.stats.lastContactAt), style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
                }
                val line = p.brief?.points?.firstOrNull()
                if (!line.isNullOrBlank()) {
                    Text(line, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 2.dp))
                }
                val tags = if (showTags) p.attentionTags() else emptyList()
                if (tags.isNotEmpty()) {
                    Row(Modifier.padding(top = 4.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        tags.forEach { t -> Tag(t, if (t.startsWith("Quiet") || t == "Been a while") Warning else MaterialTheme.colorScheme.primary) }
                    }
                }
            }
        }
        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
            DropdownMenuItem(
                text = { Text(if (p.pinned) "Unpin" else "Pin to top") },
                leadingIcon = { Icon(if (p.pinned) Icons.Outlined.PushPin else Icons.Filled.PushPin, null) },
                onClick = { menu = false; onPin() })
            p.phones.firstOrNull()?.let { ph ->
                DropdownMenuItem(text = { Text("Call") }, leadingIcon = { Icon(Icons.Filled.Call, null) }, onClick = { menu = false; onCall(ph) })
            }
            DropdownMenuItem(text = { Text("Hide from People") }, leadingIcon = { Icon(Icons.Filled.VisibilityOff, null) }, onClick = { menu = false; onHide() })
        }
    }
}

@Composable
fun Tag(text: String, color: Color) {
    Text(
        text.uppercase(), style = MaterialTheme.typography.labelSmall, color = color, maxLines = 1,
        modifier = Modifier.clip(RoundedCornerShape(6.dp)).background(color.copy(alpha = 0.14f)).padding(horizontal = 6.dp, vertical = 2.dp)
    )
}
