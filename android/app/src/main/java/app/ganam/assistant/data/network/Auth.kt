package app.ganam.assistant.data.network

import okhttp3.Request

/** Adds the server's API token as a Bearer header when one is configured. */
fun Request.Builder.bearerAuth(apiToken: String): Request.Builder =
    if (apiToken.isNotBlank()) header("Authorization", "Bearer $apiToken") else this
