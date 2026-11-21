package io.github.ronganam.dana.tv

import android.content.Context
import android.view.KeyEvent
import io.github.ronganam.dana.BuildConfig
import io.github.ronganam.dana.wake.WakePrefs
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONArray
import org.json.JSONObject
import androidx.core.content.edit

/** A camera the TV can show in picture-in-picture. */
data class Camera(
    val id: String,
    val name: String,
    /** RTSP URL, normally go2rtc's restream: rtsp://<frigate host>:8554/<stream>. */
    val url: String,
    /** HA camera entity for the home-screen snapshot (through the assistant server). */
    val snapshotEntity: String?,
)

enum class Press(val label: String) { SHORT("Short press"), LONG("Long press") }

/** What a remote button does. */
sealed class TvAction {
    data class TogglePip(val cameraId: String) : TvAction()
    data class OpenApp(val pkg: String) : TvAction()
    data class ToggleEntity(val entityId: String) : TvAction()
    object OpenAssistant : TvAction()
    object GoHome : TvAction()
    object OpenSettings : TvAction()

    fun toJson(): JSONObject = when (this) {
        is TogglePip -> JSONObject().put("type", "pip").put("camera", cameraId)
        is OpenApp -> JSONObject().put("type", "app").put("package", pkg)
        is ToggleEntity -> JSONObject().put("type", "entity").put("entity_id", entityId)
        OpenAssistant -> JSONObject().put("type", "assistant")
        GoHome -> JSONObject().put("type", "home")
        OpenSettings -> JSONObject().put("type", "settings")
    }

    companion object {
        fun fromJson(o: JSONObject): TvAction? = when (o.optString("type")) {
            "pip" -> TogglePip(o.optString("camera"))
            "app" -> OpenApp(o.optString("package"))
            "entity" -> ToggleEntity(o.optString("entity_id"))
            "assistant" -> OpenAssistant
            "home" -> GoHome
            "settings" -> OpenSettings
            else -> null
        }
    }
}

data class Binding(val keyCode: Int, val press: Press, val action: TvAction)

enum class Corner(val label: String) {
    TOP_LEFT("Top left"), TOP_RIGHT("Top right"), BOTTOM_LEFT("Bottom left"), BOTTOM_RIGHT("Bottom right")
}

data class TvSettings(
    val favorites: List<String>,
    val entities: List<String>,
    val cameras: List<Camera>,
    val bindings: List<Binding>,
    val pipCorner: Corner,
    /** PiP width as a fraction of the screen width. */
    val pipScale: Float,
    /**
     * Keep Dana TV as the home screen even though Google TV's launcher outranks it:
     * the key service catches Home and replaces Google TV's home whenever it comes forward.
     */
    val homeOverride: Boolean,
)

/**
 * The TV's settings, kept as one JSON document in `DanaPrefs` and published as
 * a [StateFlow] so the home screen, the key service and the PiP overlay (all in
 * one process) see edits immediately.
 */
object TvConfig {
    private const val KEY = "tv_settings"

    /** A fresh TV with no tv-defaults.json in the build: a few common apps, nothing of anyone's home. */
    private val GENERIC = TvSettings(
        favorites = listOf("com.google.android.youtube.tv", "com.netflix.ninja", "com.disneyplus"),
        entities = emptyList(),
        cameras = emptyList(),
        bindings = emptyList(),
        pipCorner = Corner.BOTTOM_RIGHT,
        pipScale = 0.32f,
        homeOverride = true,
    )

    /**
     * What a TV starts with before its settings are first saved: the build's
     * tv-defaults.json (`BuildConfig.TV_DEFAULTS_JSON`, this file's JSON shape;
     * docs/tv.md) over [GENERIC].
     */
    val DEFAULTS: TvSettings = BuildConfig.TV_DEFAULTS_JSON.takeIf { it.isNotBlank() }
        ?.let { runCatching { decode(JSONObject(it), GENERIC) }.getOrNull() } ?: GENERIC

    private val _settings = MutableStateFlow(DEFAULTS)
    val settings: StateFlow<TvSettings> = _settings.asStateFlow()
    val current: TvSettings get() = _settings.value

    @Volatile private var loaded = false

    fun load(context: Context) {
        if (loaded) return
        synchronized(this) {
            if (loaded) return
            val raw = WakePrefs.prefs(context).getString(KEY, null)
            _settings.value = raw?.let { runCatching { decode(JSONObject(it), DEFAULTS) }.getOrNull() } ?: DEFAULTS
            loaded = true
        }
    }

    fun update(context: Context, change: (TvSettings) -> TvSettings) {
        load(context)
        val next = change(_settings.value)
        _settings.value = next
        WakePrefs.prefs(context).edit { putString(KEY, encode(next).toString()) }
    }

    fun camera(id: String): Camera? = current.cameras.firstOrNull { it.id == id }

    private fun encode(s: TvSettings): JSONObject = JSONObject()
        .put("favorites", JSONArray(s.favorites))
        .put("entities", JSONArray(s.entities))
        .put("cameras", JSONArray(s.cameras.map {
            JSONObject().put("id", it.id).put("name", it.name).put("url", it.url).put("snapshot", it.snapshotEntity ?: "")
        }))
        .put("bindings", JSONArray(s.bindings.map {
            JSONObject().put("key", it.keyCode).put("press", it.press.name).put("action", it.action.toJson())
        }))
        .put("pip_corner", s.pipCorner.name)
        .put("pip_scale", s.pipScale.toDouble())
        .put("home_override", s.homeOverride)

    private fun decode(o: JSONObject, base: TvSettings): TvSettings {
        fun strings(name: String, fallback: List<String>) =
            o.optJSONArray(name)?.let { a -> List(a.length()) { a.getString(it) } } ?: fallback
        val cameras = o.optJSONArray("cameras")?.let { a ->
            List(a.length()) { i ->
                val c = a.getJSONObject(i)
                Camera(c.getString("id"), c.optString("name"), c.optString("url"), c.optString("snapshot").ifBlank { null })
            }
        } ?: base.cameras
        val bindings = o.optJSONArray("bindings")?.let { a ->
            (0 until a.length()).mapNotNull { i ->
                val b = a.getJSONObject(i)
                val action = TvAction.fromJson(b.getJSONObject("action")) ?: return@mapNotNull null
                Binding(b.getInt("key"), runCatching { Press.valueOf(b.getString("press")) }.getOrDefault(Press.SHORT), action)
            }
        } ?: base.bindings
        return TvSettings(
            favorites = strings("favorites", base.favorites),
            entities = strings("entities", base.entities),
            cameras = cameras,
            bindings = bindings,
            pipCorner = runCatching { Corner.valueOf(o.getString("pip_corner")) }.getOrDefault(base.pipCorner),
            pipScale = o.optDouble("pip_scale", base.pipScale.toDouble()).toFloat().coerceIn(0.2f, 0.6f),
            homeOverride = o.optBoolean("home_override", true),
        )
    }

    /** "Info", "Prog red", "Bookmark" … for a key code. */
    fun keyName(keyCode: Int): String =
        KeyEvent.keyCodeToString(keyCode).removePrefix("KEYCODE_").lowercase()
            .replace('_', ' ').replaceFirstChar { it.uppercase() }
}
