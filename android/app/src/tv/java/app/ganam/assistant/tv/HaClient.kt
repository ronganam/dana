package app.ganam.assistant.tv

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import app.ganam.assistant.data.network.HttpClients
import app.ganam.assistant.wake.WakePrefs
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.URLEncoder

/** A Home Assistant entity as the server's compact `/api/ha/states` returns it. */
data class HaEntity(
    val entityId: String,
    val domain: String,
    val state: String,
    val name: String,
    val attrs: JSONObject,
) {
    val available: Boolean get() = state != "unavailable" && state != "unknown"

    /** On / open / running: drawn with the accent. */
    val active: Boolean
        get() = state in ACTIVE_STATES || (domain == "climate" && state != "off" && available)

    /** The service OK runs on the tile, or null when the tile only shows a value (or opens a dialog). */
    fun primaryService(): Pair<String, String>? = when (domain) {
        "light", "switch", "fan", "input_boolean", "automation", "cover", "siren", "humidifier" -> domain to "toggle"
        "scene", "script" -> domain to "turn_on"
        "button", "input_button" -> domain to "press"
        "vacuum" -> if (state in setOf("cleaning", "returning")) "vacuum" to "return_to_base" else "vacuum" to "start"
        "lock" -> if (state == "locked") "lock" to "unlock" else "lock" to "lock"
        "media_player" -> "media_player" to "media_play_pause"
        else -> null
    }

    /** The state as the tile writes it: "24°", "On", "Docked" … */
    fun displayState(): String {
        if (!available) return "—"
        return when (domain) {
            "climate" -> if (state == "off") "Off" else attrs.optDouble("temperature").takeIf { !it.isNaN() }
                ?.let { formatTemp(it) } ?: pretty(state)
            "sensor" -> state + (attrs.optString("unit_of_measurement").takeIf { it.isNotEmpty() }?.let { " $it" } ?: "")
            "scene", "script", "button", "input_button" -> "Run"
            "cover" -> attrs.optInt("current_position", -1).takeIf { it in 1..99 }?.let { "$it%" } ?: pretty(state)
            else -> pretty(state)
        }
    }

    /** A secondary line: room temperature for the AC, the running app for a player. */
    fun detail(): String? = when (domain) {
        "climate" -> attrs.optDouble("current_temperature").takeIf { !it.isNaN() }?.let { "Room ${formatTemp(it)}" }
            ?: attrs.optString("hvac_action").takeIf { it.isNotEmpty() }?.let(::pretty)
        "media_player" -> attrs.optString("media_title").ifEmpty { attrs.optString("app_name") }.ifEmpty { null }
        "light" -> attrs.optInt("brightness", -1).takeIf { it >= 0 && state == "on" }?.let { "${(it * 100 / 255)}%" }
        else -> null
    }

    companion object {
        private val ACTIVE_STATES = setOf("on", "open", "opening", "cleaning", "unlocked", "playing", "home", "returning")

        fun formatTemp(v: Double): String = if (v % 1.0 == 0.0) "${v.toInt()}°" else "%.1f°".format(v)

        fun pretty(state: String): String = state.replace('_', ' ').replaceFirstChar { it.uppercase() }

        fun from(o: JSONObject) = HaEntity(
            entityId = o.optString("entity_id"),
            domain = o.optString("domain"),
            state = o.optString("state"),
            name = o.optString("name"),
            attrs = o.optJSONObject("attributes") ?: JSONObject(),
        )
    }
}

/**
 * Home Assistant through the assistant server (`/api/ha/...`), so the TV only
 * holds the server's API token, never an HA token.
 */
object HaClient {
    private val json = "application/json".toMediaType()

    private fun request(context: Context, path: String): Request.Builder {
        val b = Request.Builder().url(WakePrefs.serverUrl(context) + path)
        WakePrefs.apiToken(context).takeIf { it.isNotEmpty() }?.let { b.header("Authorization", "Bearer $it") }
        return b
    }

    private fun enc(v: String) = URLEncoder.encode(v, "UTF-8")

    private suspend fun fetchList(context: Context, path: String): List<HaEntity> = withContext(Dispatchers.IO) {
        HttpClients.base.newCall(request(context, path).build()).execute().use { r ->
            if (!r.isSuccessful) throw IOException("HTTP ${r.code}")
            val arr = JSONArray(r.body?.string().orEmpty())
            List(arr.length()) { HaEntity.from(arr.getJSONObject(it)) }
        }
    }

    /** Exactly [ids], in that order (unknown ones come back `unavailable`). */
    suspend fun states(context: Context, ids: List<String>): List<HaEntity> =
        if (ids.isEmpty()) emptyList() else fetchList(context, "/api/ha/states?ids=" + enc(ids.joinToString(",")))

    /** Every entity of [domains], for the entity picker. */
    suspend fun list(context: Context, domains: List<String>): List<HaEntity> =
        fetchList(context, "/api/ha/states?domains=" + enc(domains.joinToString(",")))

    suspend fun call(context: Context, domain: String, service: String, entityId: String, data: JSONObject = JSONObject()) =
        withContext(Dispatchers.IO) {
            val body = JSONObject().put("domain", domain).put("service", service)
                .put("entity_id", entityId).put("service_data", data).toString().toRequestBody(json)
            HttpClients.base.newCall(request(context, "/api/ha/service").post(body).build()).execute().use { r ->
                if (!r.isSuccessful) throw IOException("HTTP ${r.code}: ${r.body?.string()?.take(200)}")
            }
        }

    /** A camera snapshot (HA camera_proxy through the server), downscaled to about [maxWidth] px. */
    suspend fun snapshot(context: Context, entityId: String, maxWidth: Int): Bitmap? = withContext(Dispatchers.IO) {
        HttpClients.base.newCall(request(context, "/api/camera/${enc(entityId)}/proxy").build()).execute().use { r ->
            if (!r.isSuccessful) return@withContext null
            val bytes = r.body?.bytes() ?: return@withContext null
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
            var sample = 1
            while (bounds.outWidth / (sample * 2) >= maxWidth) sample *= 2
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })
        }
    }
}
