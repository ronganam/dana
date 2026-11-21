package io.github.ronganam.dana.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.ui.adaptive.Pane
import io.github.ronganam.dana.ui.adaptive.PaneRow
import io.github.ronganam.dana.ui.adaptive.sidePaneWidth
import io.github.ronganam.dana.ui.adaptive.WindowTier
import io.github.ronganam.dana.ui.adaptive.windowTier
import io.github.ronganam.dana.ui.components.ScreenTopBar
import io.github.ronganam.dana.ui.screens.common.QuietEmptyState
import io.github.ronganam.dana.ui.screens.memory.LetterHeader
import io.github.ronganam.dana.ui.screens.memory.MemoryCategoryPane
import io.github.ronganam.dana.ui.screens.memory.MemoryDetailPane
import io.github.ronganam.dana.ui.screens.memory.MemoryEditorForm
import io.github.ronganam.dana.ui.screens.memory.MemoryFilterHeader
import io.github.ronganam.dana.ui.screens.memory.MemoryRow
import io.github.ronganam.dana.ui.screens.memory.MemorySheet
import io.github.ronganam.dana.ui.screens.memory.letterSections
import io.github.ronganam.dana.ui.viewmodels.MainViewModel

/**
 * Memory — an A–Z index of everything the assistant knows.
 *
 *  COMPACT : search + type chips + index; read/edit in a bottom sheet
 *  MEDIUM  : index | detail / editor
 *  EXPANDED: types | index | detail / editor
 */
@Composable
fun MemoryScreen(viewModel: MainViewModel) {
    LaunchedEffect(Unit) { viewModel.fetchMemories() }

    val tier = windowTier()
    val wide = tier != WindowTier.COMPACT
    val memories by viewModel.memories.collectAsState()
    val isLoading by viewModel.isLoadingMemories.collectAsState()

    var searchQuery by rememberSaveable { mutableStateOf("") }
    var categoryFilter by rememberSaveable { mutableStateOf("") } // "" = all types

    val categories = remember(memories) { memories.map { it.category }.distinct().sortedBy { it.lowercase() } }
    val categoryCounts = remember(memories) { memories.groupingBy { it.category }.eachCount() }
    val filtered = remember(memories, searchQuery, categoryFilter) {
        val query = searchQuery.trim()
        memories.filter { m ->
            (categoryFilter.isEmpty() || m.category.equals(categoryFilter, ignoreCase = true)) &&
                (query.isEmpty() || m.key.contains(query, ignoreCase = true) || m.value.contains(query, ignoreCase = true))
        }
    }
    val sections = remember(filtered) { letterSections(filtered) }

    // Selection / editing survive fold, unfold and rotation.
    var selectedKey by rememberSaveable { mutableStateOf<String?>(null) }
    var editing by rememberSaveable { mutableStateOf(false) }
    var creating by rememberSaveable { mutableStateOf(false) }
    var sheetOpen by rememberSaveable { mutableStateOf(false) }

    // In multi-pane layouts keep something selected: fall back to the first visible memory.
    LaunchedEffect(filtered.map { it.key }, wide) {
        if (!wide || creating) return@LaunchedEffect
        if (selectedKey == null || filtered.none { it.key == selectedKey }) {
            selectedKey = filtered.firstOrNull()?.key
            editing = false
        }
    }

    val selected = memories.find { it.key == selectedKey }

    val categoryHeader: @Composable () -> Unit = {
        MemoryFilterHeader(
            searchQuery = searchQuery,
            onSearchChange = { searchQuery = it },
            categories = categories,
            categoryCounts = categoryCounts,
            total = memories.size,
            categoryFilter = categoryFilter,
            onSelectCategory = { categoryFilter = it },
            showChips = tier != WindowTier.EXPANDED
        )
    }

    val list: @Composable () -> Unit = {
        Box(Modifier.fillMaxSize()) {
            when {
                isLoading && memories.isEmpty() ->
                    CircularProgressIndicator(Modifier.align(Alignment.Center))

                memories.isEmpty() -> QuietEmptyState(
                    title = "Nothing remembered yet.",
                    message = "Things you ask Dana to remember are filed here, A to Z.",
                    modifier = Modifier.align(Alignment.TopStart).padding(top = 24.dp),
                    actionLabel = "Add a memory",
                    onAction = { creating = true; editing = false }
                )

                filtered.isEmpty() -> QuietEmptyState(
                    title = "No match.",
                    message = "Nothing filed under that search.",
                    modifier = Modifier.align(Alignment.TopStart).padding(top = 24.dp),
                    actionLabel = "Clear filters",
                    onAction = { searchQuery = ""; categoryFilter = "" }
                )

                else -> LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(bottom = if (wide) 24.dp else 96.dp)
                ) {
                    sections.forEach { section ->
                        item(key = "l_${section.letter}") { LetterHeader(section.letter) }
                        items(section.items, key = { "m_${it.key}" }) { memory ->
                            MemoryRow(
                                memory = memory,
                                selected = wide && memory.key == selectedKey,
                                onClick = {
                                    selectedKey = memory.key
                                    editing = false
                                    creating = false
                                    sheetOpen = !wide
                                }
                            )
                        }
                    }
                }
            }
        }
    }

    val detail: @Composable () -> Unit = {
        Box(Modifier.fillMaxSize()) {
            when {
                creating -> MemoryEditorForm(
                    memory = null,
                    availableCategories = categories,
                    onSave = { key, value, category ->
                        viewModel.createMemory(key, value, category)
                        creating = false
                        selectedKey = key
                    },
                    onCancel = { creating = false },
                    modifier = Modifier.padding(top = 8.dp).widthIn(max = 640.dp)
                )

                selected != null && editing -> MemoryEditorForm(
                    memory = selected,
                    availableCategories = categories,
                    onSave = { key, value, category ->
                        viewModel.updateMemory(selected.key, key, value, category)
                        editing = false
                        selectedKey = key
                    },
                    onDelete = {
                        viewModel.deleteMemory(selected.key)
                        editing = false
                        selectedKey = null
                    },
                    onCancel = { editing = false },
                    modifier = Modifier.padding(top = 8.dp).widthIn(max = 640.dp)
                )

                selected != null -> MemoryDetailPane(
                    memory = selected,
                    onEdit = { editing = true },
                    onDelete = {
                        viewModel.deleteMemory(selected.key)
                        selectedKey = null
                    }
                )

                else -> QuietEmptyState(
                    title = "Select a memory.",
                    message = "It opens here.",
                    modifier = Modifier.align(Alignment.TopStart).padding(top = 24.dp)
                )
            }
        }
    }

    Scaffold(
        containerColor = Color.Transparent,
        topBar = {
            ScreenTopBar(
                title = "Memory",
                kicker = "${memories.size} ENTRIES · ${categories.size} TYPES",
                actions = {
                    if (wide) {
                        TextButton(
                            onClick = { creating = true; editing = false },
                            modifier = Modifier.heightIn(min = 48.dp)
                        ) {
                            Icon(Icons.Filled.Add, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("New")
                        }
                    }
                }
            )
        },
        floatingActionButton = {
            if (!wide) {
                FloatingActionButton(
                    onClick = { creating = true },
                    containerColor = MaterialTheme.colorScheme.primary,
                    contentColor = MaterialTheme.colorScheme.onPrimary
                ) { Icon(Icons.Filled.Add, "Add memory") }
            }
        }
    ) { innerPadding ->
        BoxWithConstraints(Modifier.padding(innerPadding).fillMaxSize()) {
            val sideWidth = sidePaneWidth()

            when (tier) {
                WindowTier.COMPACT -> Column(Modifier.fillMaxSize()) {
                    categoryHeader()
                    Box(Modifier.weight(1f)) { list() }
                }

                WindowTier.MEDIUM -> PaneRow {
                    Pane(width = sideWidth, tinted = true) {
                        Column(Modifier.fillMaxSize()) {
                            categoryHeader()
                            Box(Modifier.weight(1f)) { list() }
                        }
                    }
                    Pane(divider = false) { detail() }
                }

                WindowTier.EXPANDED -> PaneRow {
                    Pane(width = sideWidth, tinted = true) {
                        MemoryCategoryPane(
                            categories = categories,
                            categoryCounts = categoryCounts,
                            total = memories.size,
                            categoryFilter = categoryFilter,
                            onSelectCategory = { categoryFilter = it }
                        )
                    }
                    Pane {
                        Column(Modifier.fillMaxSize()) {
                            categoryHeader()
                            Box(Modifier.weight(1f)) { list() }
                        }
                    }
                    Pane(divider = false) { detail() }
                }
            }
        }

        // Phone presentation: one bottom sheet that reads first and flips to the form on Edit / New.
        if (!wide && (creating || (sheetOpen && selected != null))) {
            key(creating, selectedKey) {
                MemorySheet(
                    memory = if (creating) null else selected,
                    startEditing = creating,
                    availableCategories = categories,
                    onDismiss = {
                        creating = false
                        sheetOpen = false
                    },
                    onSave = { newKey, value, category ->
                        if (creating) viewModel.createMemory(newKey, value, category)
                        else selected?.let { viewModel.updateMemory(it.key, newKey, value, category) }
                        creating = false
                        sheetOpen = false
                    },
                    onDelete = {
                        selected?.let { viewModel.deleteMemory(it.key) }
                        creating = false
                        sheetOpen = false
                        selectedKey = null
                    }
                )
            }
        }
    }
}
