package app.ganam.assistant.projects

import org.json.JSONObject

/**
 * Server payloads: `test_fixtures/projects_web.json` of the server repo (written by
 * `WRITE_WEB_FIXTURE=1 python test_integrations_api.py`), copied with the sharer's name
 * replaced. Keys: `list_owner` (GET /api/projects), `items_owner` / `items_read`
 * (GET /api/projects/{id}/items as the owner and through a read share).
 */
object ProjectsFixture {
    val json: JSONObject by lazy {
        JSONObject(ProjectsFixture::class.java.classLoader!!.getResource("projects_web.json")!!.readText())
    }
}
