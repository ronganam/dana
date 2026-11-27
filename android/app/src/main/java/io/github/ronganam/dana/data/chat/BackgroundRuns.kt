package io.github.ronganam.dana.data.chat

import io.github.ronganam.dana.data.network.SessionItem

/**
 * Conversations Dana has on its own, apart from the shared thread: a watch's
 * AI agent run gets its own session `watch-<watch id>`, titled
 * "👀 <watch title>" (server `addons/watches`). The thread pane lists them
 * ("Background runs") and opens one as a visit, where a waiting approval can
 * be answered.
 */
object BackgroundRuns {
    const val PREFIX = "watch-"

    fun isBackgroundRun(sessionId: String): Boolean = sessionId.startsWith(PREFIX)

    /** The background runs among `GET /api/sessions`' [sessions], newest first. */
    fun of(sessions: List<SessionItem>): List<SessionItem> =
        sessions.filter { isBackgroundRun(it.id) }.sortedByDescending { it.timestamp }
}
