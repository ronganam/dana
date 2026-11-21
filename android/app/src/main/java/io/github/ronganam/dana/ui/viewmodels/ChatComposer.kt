package io.github.ronganam.dana.ui.viewmodels

import android.net.Uri
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** What waits in the input box: a picked image and text put there (a share, a message given back). */
internal class ChatComposer {
    private val _image = MutableStateFlow<Uri?>(null)
    val image: StateFlow<Uri?> = _image.asStateFlow()

    private val _draft = MutableStateFlow<String?>(null)
    /** Text to put in the input box; the screen consumes it. */
    val draft: StateFlow<String?> = _draft.asStateFlow()

    fun setImage(uri: Uri?) {
        _image.value = uri
    }

    /** The picked image goes with the message being sent. */
    fun takeImage(): Uri? = _image.value.also { _image.value = null }

    fun setDraft(text: String?) {
        _draft.value = text
    }

    fun consumeDraft() {
        _draft.value = null
    }
}
