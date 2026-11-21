package io.github.ronganam.dana.ui.components

import android.content.Context
import coil.request.ImageRequest

/**
 * Coil request for an image attached to a chat message. Server-relative paths
 * (e.g. `/api/images/...`) are resolved against [serverUrl] and carry the API
 * token; absolute URLs point elsewhere and are fetched without it, and
 * local ones (`file:`, `content:` — a picked image or the sheet's sent
 * screenshot before the stored copy replaces it) load from the phone.
 */
fun serverImageRequest(context: Context, imageUrl: String, serverUrl: String, apiToken: String): ImageRequest {
    val isServerImage = !Regex("^[a-zA-Z][a-zA-Z0-9+.-]*:").containsMatchIn(imageUrl)
    return ImageRequest.Builder(context)
        .data(if (isServerImage) "$serverUrl$imageUrl" else imageUrl)
        .apply { if (isServerImage && apiToken.isNotBlank()) addHeader("Authorization", "Bearer $apiToken") }
        .crossfade(true)
        .build()
}
