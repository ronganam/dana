package io.github.ronganam.dana.projects

import io.github.ronganam.dana.data.network.ApiJson
import kotlinx.serialization.json.JsonObject

/**
 * Server payloads: `server/tests/fixtures/projects_web.json` (written by
 * `WRITE_WEB_FIXTURE=1 python test_integrations_api.py`), copied with the sharer's name
 * replaced. Keys: `list_owner` (GET /api/projects), `items_owner` / `items_read`
 * (GET /api/projects/{id}/items as the owner and through a read share).
 */
object ProjectsFixture {
    val json: JsonObject by lazy {
        ApiJson.parseToJsonElement(ProjectsFixture::class.java.classLoader!!.getResource("projects_web.json")!!.readText()) as JsonObject
    }

    /** One key's payload as the server sends it. */
    fun text(key: String): String = json.getValue(key).toString()
}
