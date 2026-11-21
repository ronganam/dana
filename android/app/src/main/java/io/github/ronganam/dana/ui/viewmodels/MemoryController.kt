package io.github.ronganam.dana.ui.viewmodels

import io.github.ronganam.dana.data.models.Memory
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** Memory Center: the assistant's stored facts. */
class MemoryController(private val ctx: ControllerContext) {

    private val _memories = MutableStateFlow<List<Memory>>(emptyList())
    val memories: StateFlow<List<Memory>> = _memories.asStateFlow()

    private val _isLoadingMemories = MutableStateFlow(false)
    val isLoadingMemories: StateFlow<Boolean> = _isLoadingMemories.asStateFlow()

    fun fetchMemories() {
        ctx.scope.launch {
            _isLoadingMemories.value = true
            try {
                ctx.load("Memories") { ctx.api.tasks.memories() }?.let { _memories.value = it }
            } finally {
                _isLoadingMemories.value = false
            }
        }
    }

    fun createMemory(key: String, value: String, category: String) {
        ctx.action("Couldn't save memory") {
            ctx.api.tasks.createMemory(key, value, category).also { if (it) fetchMemories() }
        }
    }

    fun updateMemory(oldKey: String, newKey: String, value: String, category: String) {
        ctx.action("Couldn't update memory") {
            // The API has no rename: write the new key first, then drop the old
            // one, so a failure can't lose the memory.
            val ok = ctx.api.tasks.createMemory(newKey, value, category)
            if (ok && oldKey != newKey) {
                ctx.api.tasks.deleteMemory(oldKey)
            }
            if (ok) fetchMemories()
            ok
        }
    }

    fun deleteMemory(key: String) {
        ctx.action("Couldn't delete memory") {
            ctx.api.tasks.deleteMemory(key).also { if (it) fetchMemories() }
        }
    }
}
