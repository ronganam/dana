package io.github.ronganam.dana.data.models

import io.github.ronganam.dana.data.network.LenientList
import io.github.ronganam.dana.data.network.OptString
import io.github.ronganam.dana.data.network.text
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import java.util.UUID

/** A task / reminder (`GET /api/tasks`). */
@Serializable
data class Task(
    val id: String = "",
    val title: String = "",
    val description: OptString = null,
    val priority: String = "medium",
    val dueDate: OptString = null,
    val status: String = "needs_action",
    val triggers: Triggers = Triggers(),
    val createdAt: OptString = null,
    val updatedAt: OptString = null,
    val groupId: OptString = null,
    val groupName: OptString = null,
    val groupEmoji: OptString = null,
    val groupColor: OptString = null,
    /** Where the task came from / what it relates to (calls, chats…). */
    val links: LenientList<TaskLink> = emptyList()
)

/** A task ↔ item relation (`task_links`); call links carry the call's title/contact. */
@Serializable
data class TaskLink(
    val refType: String = "",
    val refId: String = "",
    val role: String = "",
    val title: OptString = null,
    val contact: OptString = null,
    val callTimestamp: OptString = null
)

@Serializable
data class TaskGroup(
    val id: String = "",
    val name: String = "",
    val emoji: String = "",
    val color: String = "",
    val description: OptString = null,
    val sortOrder: Int = 0,
    val taskCount: Int? = null
)

@Serializable
data class Memory(
    val key: String = "",
    val value: String = "",
    val category: String = "general",
    val updatedAt: OptString = null
)

/**
 * Represents a single tool call made by the assistant.
 * Embedded inside a ChatMessage when the server invokes a tool.
 */
data class ToolCallEntry(
    val toolName: String,
    val toolSummary: String,
    val parameters: String,        // Pretty-printed JSON string
    val response: String = "",     // Filled in after the tool completes
    val timestamp: Long = System.currentTimeMillis(),
    val requiresApproval: Boolean = false,
    val toolCallId: String = "",
    val toolArgs: String = "",     // Raw args for approval
    val isCollapsed: Boolean = false,
    val isRunning: Boolean = false,   // True while the tool is executing
    val isPreparing: Boolean = false, // Model decided to call it; arguments not final yet
    val succeeded: Boolean? = null,   // null = not finished, true/false = outcome
    val durationMs: Long? = null,
    /**
     * Why it waits for approval: "device_screen" when it asks only because the
     * turn carries the shared phone screen (server `approval_required.reason`),
     * "" for the usual send / delete tools.
     */
    val approvalReason: String = "",
    /** A waiting call in words (server `approval_required.card`); null from an older server. */
    val approvalCard: ApprovalWords? = null
)

/**
 * What a tool call waiting for the user's OK will do, in words (server
 * `core/approval_text.py` `describe`, the same as Telegram's card and Now's):
 * "Delete the reminder “Milk”" / "Delete this reminder?", the content it
 * would send ([body]) and other details as label → value ([facts]).
 */
@Serializable
data class ApprovalWords(
    val title: String = "",
    val question: String = "",
    val bodyLabel: String = "",
    val body: String = "",
    val facts: LenientList<List<String>> = emptyList()
) {
    /** [facts] as label → value, malformed and empty ones left out. */
    val details: List<Pair<String, String>> get() = facts.mapNotNull { f -> if (f.size >= 2 && f[1].isNotBlank()) f[0] to f[1] else null }
}

data class ChatMessage(
    val id: String = UUID.randomUUID().toString(),
    val sender: String,
    val message: String,
    val isUser: Boolean,
    val toolCall: ToolCallEntry? = null,
    val isThinking: Boolean = false,
    val images: List<String> = emptyList(),
    /** The model's streamed thought summary for this step (live only, not persisted). */
    val reasoning: String = "",
    /** True while reply text is still arriving for this bubble. */
    val isStreaming: Boolean = false,
    /** Status line under the typing indicator ("Fast · Thinking…"). */
    val thinkingLabel: String = "",
    /** When it was said (epoch ms; the shared thread's `ts`), null when unknown. */
    val timestamp: Long? = null,
    /** Which device it came from in the shared thread (`phone`, `watch`, `tablet`, `tv`, `mac`, `web`), "" when unknown. */
    val device: String = "",
    /** The reply came from a learned phrase (no model call). */
    val shortcut: Boolean = false,
    /**
     * A user message sent with the screen of the app under the power-button
     * sheet: that app's name ("" = no screen; "Screen" when unnamed). The
     * screenshot itself is in [images].
     */
    val screen: String = ""
)

/** One entry of the server's chat model catalog (`GET /api/assistant/models`). */
data class ModelOption(
    val id: String,
    val label: String,
    val description: String,
    val showsReasoning: Boolean
)

data class SelectionOption(
    val label: String,
    val value: String
)

data class SelectionRequest(
    val prompt: String,
    val options: List<SelectionOption>,
    val multiple: Boolean = false
)

/**
 * The agent ran out of iteration budget mid-task. The user decides whether to
 * grant more steps or stop and refine the request.
 */
data class PendingExtension(
    val summary: String
)

/** A calendar event from `GET /api/calendar/events` (start/end as the server sent them). */
data class CalendarEvent(
    val id: String,
    val title: String,
    val start: String?,
    val end: String?,
    val allDay: Boolean,
    val location: String? = null
)

/**
 * One of the user's zones (`GET /api/zones`): what the reminder editors show
 * ([name], [entityId]) and where it is, for the phone's geofences
 * (`data/signals/GeofenceDiff`).
 */
@Serializable
data class ZoneItem(
    val name: String = "",
    val entityId: String = "",
    val id: String = "",
    val latitude: Double? = null,
    val longitude: Double? = null,
    val radiusM: Double? = null,
    val isHome: Boolean = false
)

/** A call recording (`GET /api/calls`, and the call part of `GET /api/calls/{id}`). */
@Serializable
data class PhoneCall(
    val id: String = "",
    val filename: String = "",
    val caller: String = "Unknown",
    val receiver: String = "Me",
    val callTimestamp: String = "",
    val transcript: OptString = null,
    val summary: OptString = null,
    val title: OptString = null,
    val durationSeconds: Int = 0,
    /** processing | ready | failed */
    val status: String = "ready",
    @SerialName("label_details") val labels: LenientList<CallLabel> = emptyList(),
    /** low | normal | high — low (logistics, IVR) is hidden by default. */
    val importance: String = "normal",
    val direction: OptString = null,
    val phoneNumber: OptString = null,
    val audioAvailable: Boolean = true,
    val audioDeletedAt: OptString = null,
    val participants: LenientList<CallParticipant> = emptyList(),
    val keyFacts: LenientList<CallFact> = emptyList(),
    val securityAlert: OptString = null,
    val pendingSuggestions: Int = 0,
    val error: OptString = null
) {
    val audioDeleted: Boolean get() = audioDeletedAt != null
}

@Serializable
data class CallLabel(val id: String = "", val emoji: String = "", val name: String = "")

@Serializable
data class CallParticipant(val name: String = "", val role: String = "")

@Serializable
data class CallFact(val kind: String = "", val text: String = "")

/** The call a suggestion came from (as embedded in `GET /api/suggestions`). */
data class SuggestionSource(
    val callId: String,
    val title: String?,
    val contact: String?,
    val callTimestamp: String?,
    val labels: List<CallLabel>,
    /** The notification's text, or the call's summary. */
    val text: String? = null,
    /** Who wrote the notification (chat sender). */
    val sender: String? = null,
    /** The notification's app (for its icon). */
    val packageName: String? = null
)

/**
 * A task / calendar event / security alert extracted from a call, waiting for
 * the user (`call_suggestions`). [payload] is the raw JSON the server stores;
 * the typed getters read it.
 */
data class CallSuggestion(
    val id: String,
    /** The call it came from ("" for suggestions from other sources). */
    val callId: String,
    /** task | event | alert */
    val kind: String,
    /** pending | accepted | dismissed */
    val status: String,
    val payload: JsonObject,
    val source: SuggestionSource?,
    val resultRef: String? = null,
    /** call | notification */
    val sourceType: String = "call",
    /** Id of the call / notification it came from. */
    val sourceId: String = callId
) {
    val fromNotification: Boolean get() = sourceType == "notification"
    val title: String get() = payload.text("title")?.takeIf { it.isNotBlank() } ?: "Suggestion"
    val description: String? get() = payload.text("description")?.takeIf { it.isNotBlank() }
    val dueDate: String? get() = payload.text("due_date")?.takeIf { it.isNotBlank() }
    val priority: String get() = payload.text("priority")?.takeIf { it.isNotBlank() } ?: "medium"
    val group: String? get() = payload.text("group")?.takeIf { it.isNotBlank() }
    val triggers: Triggers get() = Triggers.of(payload["triggers"])
    val start: String? get() = payload.text("start")?.takeIf { it.isNotBlank() }
    val end: String? get() = payload.text("end")?.takeIf { it.isNotBlank() }
    val location: String? get() = payload.text("location")?.takeIf { it.isNotBlank() }
    val evidence: String? get() = payload.text("evidence")?.takeIf { it.isNotBlank() }
    val alertText: String? get() = payload.text("text")?.takeIf { it.isNotBlank() }
}

/** Full call detail (`GET /api/calls/{id}`): the call plus its suggestions and linked tasks. */
data class CallDetail(
    val call: PhoneCall,
    val suggestions: List<CallSuggestion>,
    val tasks: List<LinkedTask>
)

@Serializable
data class LinkedTask(val id: String = "", val title: String = "", val status: String = "", val dueDate: OptString = null)

/**
 * What a chat was opened from (a call suggestion, a call or a task). The
 * server feeds it to the agent; the chat shows it as an attachment card with
 * one-tap prompts.
 */
data class ChatContext(
    val kind: String,
    val refId: String,
    val callId: String?,
    val emoji: String,
    val title: String,
    val subtitle: String,
    val quickPrompts: List<String>,
    val status: String? = null
)

/** A phone notification captured by [io.github.ronganam.dana.data.notifications.DanaNotificationListener] and triaged by the server. */
data class PhoneNotification(
    val id: String,
    /** The StatusBarNotification key (to cancel it in the shade). */
    val key: String?,
    val packageName: String,
    val appName: String,
    val title: String?,
    val text: String?,
    val sender: String?,
    val conversation: String?,
    val isGroup: Boolean,
    val postedAt: String?,
    /** active | dismissed | archived */
    val status: String,
    val removedReason: String?,
    /** pending | done | failed | skipped */
    val triageStatus: String,
    val category: String?,
    val categoryEmoji: String,
    /** urgent | important | normal | low | noise */
    val priority: String,
    /** needs_you | normal | dismiss */
    val bucket: String,
    val escalated: Boolean,
    val suggestions: List<CallSuggestion>,
    /** Local ISO time it's snoozed until (null = not snoozed). */
    val snoozedUntil: String? = null
) {
    val pendingSuggestions: List<CallSuggestion> get() = suggestions.filter { it.status == "pending" }
}

@Serializable
data class NotificationCounts(
    val needsYou: Int = 0,
    val normal: Int = 0,
    val dismiss: Int = 0,
    val active: Int = 0,
    val snoozed: Int = 0,
    val pendingSuggestions: Int = 0,
    val untriaged: Int = 0
)

/** One AI summary (`GET /api/notifications/summaries`): an app's notifications or the whole Inbox. */
data class InboxSummary(
    val summary: String?,
    val count: Int,
    /** Short bullet points in the user's first language; older servers send only [summary]. */
    val points: List<String> = summary?.split('\n')?.filter { it.isNotBlank() }.orEmpty(),
    /** The notifications changed since it was written; a rewrite is on its way. */
    val stale: Boolean
)

data class InboxSummaries(
    /** The Inbox at a glance (null under 2 notifications). */
    val overall: InboxSummary? = null,
    /** By package; only apps with 2+ notifications. */
    val apps: Map<String, InboxSummary> = emptyMap(),
    val refreshing: Boolean = false
)

/** Per-app numbers for the rules screen (`GET /api/notifications/apps`). */
data class NotificationApp(
    val packageName: String,
    val appName: String,
    val total: Int,
    val opened: Int,
    val swiped: Int,
    val suggestedDismiss: Int,
    /** mute | important | null */
    val rule: String?
)
