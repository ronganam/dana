package io.github.ronganam.dana.data.watches

import io.github.ronganam.dana.data.models.isoMillis
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.JsonText
import io.github.ronganam.dana.data.network.LenientList
import io.github.ronganam.dana.data.network.OptString
import io.github.ronganam.dana.data.network.TextList
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/*
 * The wire shapes of `/api/watches` (server docs/watches.md, "API"; fixture
 * `server/tests/fixtures/watches_web.json`). Lenient: omitted fields take the
 * defaults, unknown keys are ignored. Datetimes stay ISO text; `*Ms` reads them.
 */

/** One condition of a trigger's rule (`field op value`). */
@Serializable
data class WatchCondition(val field: String = "", val op: String = "", val value: JsonText = null, val value2: JsonText = null)

/** One trigger: an event (with its rule, keywords, time of day and AI check) or a time. */
@Serializable
data class WatchTrigger(
    val id: String = "",
    val kind: String = "event",
    val at: OptString = null,
    val every: OptString = null,
    val relativeTo: OptString = null,
    val offsetMinutes: Int? = null,
    val eventType: OptString = null,
    val where: LenientList<WatchCondition> = emptyList(),
    val keywords: TextList = emptyList(),
    val during: OptString = null,
    val semantic: OptString = null,
)

/** What a watch does when it fires. */
@Serializable
data class WatchActionSpec(
    val type: String = "",
    val text: OptString = null,
    val urgency: OptString = null,
    val tool: OptString = null,
    val instructions: OptString = null,
    val autonomy: OptString = null,
)

/** A watch's compiled spec (the part the phone reads). */
@Serializable
data class WatchSpec(
    val title: String = "",
    val kind: String = "watch",
    val triggers: LenientList<WatchTrigger> = emptyList(),
    val repeat: OptString = null,
    val cooldownMinutes: Int? = null,
    val onMatch: LenientList<WatchActionSpec> = emptyList(),
    val onDeadline: LenientList<WatchActionSpec> = emptyList(),
    val unsupported: OptString = null,
    val windowStart: OptString = null,
    val windowEnd: OptString = null,
)

/** One watch (`GET /api/watches`). [spec] stays raw JSON so it can go back as it is (the dry run). */
@Serializable
data class Watch(
    val id: String = "",
    val title: String = "",
    val kind: String = "watch",
    val status: String = "active",
    val request: String = "",
    val spec: JsonObject = JsonObject(emptyMap()),
    val createdAt: String = "",
    val firesCount: Int = 0,
    val lastFiredAt: OptString = null,
    val nextTime: OptString = null,
    val taskId: OptString = null,
) {
    /** The spec, read. */
    val parsed: WatchSpec by lazy { runCatching { ApiJson.decodeFromJsonElement(WatchSpec.serializer(), spec) }.getOrDefault(WatchSpec()) }
    val lastFiredMs: Long? get() = isoMillis(lastFiredAt)
    val createdMs: Long? get() = isoMillis(createdAt)
    val isActive: Boolean get() = status == "active"
    val isPaused: Boolean get() = status == "paused"
    /** Done or expired: kept, but it watches nothing any more. */
    val isClosed: Boolean get() = !isActive && !isPaused
}

@Serializable
internal data class WatchList(val watches: LenientList<Watch> = emptyList())

@Serializable
internal data class WatchAnswer(val watch: Watch? = null)

/** What one action of a fire did (`done`, `held`, `failed`, `skipped`), and the agent run it started. */
@Serializable
data class FireAction(
    val type: String = "",
    val status: String = "",
    val detail: String = "",
    val runId: OptString = null,
    val sessionId: OptString = null,
)

/** One time a watch fired. */
@Serializable
data class WatchFire(
    val id: String = "",
    val at: String = "",
    val triggerId: OptString = null,
    val eventType: OptString = null,
    val event: JsonObject? = null,
    val actions: LenientList<FireAction> = emptyList(),
) {
    val atMs: Long? get() = isoMillis(at)
}

/** `GET /api/watches/{id}`. */
@Serializable
data class WatchDetail(val watch: Watch = Watch(), val fires: LenientList<WatchFire> = emptyList())

/** One check of the trace: an event the watch looked at and what became of it. */
@Serializable
data class TraceRow(
    val id: Long = 0,
    val at: String = "",
    val triggerId: OptString = null,
    val eventType: OptString = null,
    val eventId: OptString = null,
    val summary: String = "",
    val outcome: String = "",
    val reason: String = "",
    val near: Boolean = false,
    val data: JsonObject? = null,
) {
    val atMs: Long? get() = isoMillis(at)
}

/** `GET /api/watches/{id}/trace`. */
@Serializable
data class WatchTrace(val trace: LenientList<TraceRow> = emptyList(), val unstored: Int = 0)

/** One step of the engine, live (`/api/watches/pipeline/stream`) or recent (`/api/watches/pipeline`). */
@Serializable
data class PipelineEvent(
    val seq: Long = 0,
    val at: String = "",
    val stage: String = "",
    val eventType: OptString = null,
    val eventId: OptString = null,
    val summary: String = "",
    val watchId: OptString = null,
    val watchTitle: OptString = null,
    val triggerId: OptString = null,
    val result: OptString = null,
    val actionType: OptString = null,
) {
    val atMs: Long? get() = isoMillis(at)
}

/** One past event a dry run's rule let through. */
@Serializable
data class DrySample(val at: String = "", val eventType: OptString = null, val summary: String = "", val data: JsonObject? = null) {
    val atMs: Long? get() = isoMillis(at)
}

/** A trigger's dry run over the user's past events: what the rule lets through. */
@Serializable
data class DryRunTrigger(
    val matches: Int = 0,
    val perDay: Double = 0.0,
    val overBudget: Boolean = false,
    val sample: LenientList<DrySample> = emptyList(),
)

/** `POST /api/watches/dry-run`. */
@Serializable
data class DryRun(val triggers: Map<String, DryRunTrigger> = emptyMap())
