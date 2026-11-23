package io.github.ronganam.dana.ui.screens.now

import io.github.ronganam.dana.data.models.NowAction
import kotlinx.serialization.json.JsonPrimitive

/**
 * Where a Now card's web-only client action opens (a self-check's fix: server docs/now.md, "Fixes"):
 * `connect` → the web app's Addons page on that addon (`#addons/<name>`), `screen` → that screen of the
 * web app (`/<screen>`), `url` → the https address itself. Null for any other action or a bad argument.
 */
object NowWebLink {
    private val NAME = Regex("^[a-z][a-z0-9_-]*$")

    fun target(serverUrl: String, action: NowAction): String? {
        val base = serverUrl.trimEnd('/')
        fun arg(key: String): String? = (action.args[key] as? JsonPrimitive)?.takeIf { it.isString }?.content
        return when (action.action) {
            "connect" -> arg("addon")?.takeIf { NAME.matches(it) }?.let { "$base/#addons/$it" }
            "screen" -> arg("screen")?.takeIf { NAME.matches(it) }?.let { "$base/$it" }
            "url" -> arg("url")?.takeIf { it.startsWith("https://") }
            else -> null
        }
    }
}
