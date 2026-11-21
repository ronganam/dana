package io.github.ronganam.dana.ui.viewmodels

import android.content.SharedPreferences
import androidx.core.content.edit
import io.github.ronganam.dana.data.models.ModelOption
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** The chat models the server offers (`GET /api/assistant/models`) and the one picked, kept in prefs. */
internal class ChatModels(private val ctx: ControllerContext, private val prefs: SharedPreferences) {
    private val _models = MutableStateFlow<List<ModelOption>>(emptyList())
    /** Chat models the server offers (empty until fetched). */
    val models: StateFlow<List<ModelOption>> = _models.asStateFlow()

    private val _selected = MutableStateFlow(prefs.getString(KEY_CHAT_MODEL, "").orEmpty())
    /** Selected chat model id; empty means "server default". */
    val selected: StateFlow<String> = _selected.asStateFlow()

    fun fetch() {
        ctx.scope.launch {
            val catalog = ctx.load("Models") { ctx.api.chat.models() } ?: return@launch
            val options = catalog.models
            _models.value = options
            if (options.none { it.id == _selected.value }) {
                _selected.value = catalog.default.ifEmpty { options.firstOrNull()?.id.orEmpty() }
            }
        }
    }

    fun select(id: String) {
        _selected.value = id
        prefs.edit { putString(KEY_CHAT_MODEL, id) }
    }

    /** The model to ask for; null = the server's default. */
    fun forRequest(): String? = _selected.value.ifEmpty { null }

    companion object {
        private const val KEY_CHAT_MODEL = "chat_model"
    }
}
