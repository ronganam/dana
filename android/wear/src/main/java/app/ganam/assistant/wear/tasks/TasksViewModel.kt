package app.ganam.assistant.wear.tasks

import android.content.Context
import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import app.ganam.assistant.wear.data.WearPrefs
import com.google.android.gms.wearable.MessageClient
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.Wearable
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

class TasksViewModel : ViewModel(), MessageClient.OnMessageReceivedListener {

    companion object {
        private const val TAG = "TasksViewModel"
    }

    private lateinit var appContext: Context

    private val _tasks = MutableStateFlow<List<WearTask>>(emptyList())
    val tasks: StateFlow<List<WearTask>> = _tasks.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private val _statusText = MutableStateFlow("Loading...")
    val statusText: StateFlow<String> = _statusText.asStateFlow()

    fun initialize(context: Context) {
        appContext = context.applicationContext
        Log.d(TAG, "initialize() — registering message listener")
        Wearable.getMessageClient(appContext).addListener(this)
        refreshTasks()
    }

    fun refreshTasks() {
        Log.d(TAG, "refreshTasks() — requesting phone node")
        _isLoading.value = true
        _statusText.value = "Loading..."
        getPhoneNodeId { phoneId ->
            Log.d(TAG, "refreshTasks() — sending /get_tasks to $phoneId")
            Wearable.getMessageClient(appContext).sendMessage(phoneId, "/get_tasks", null)
                .addOnSuccessListener { Log.d(TAG, "refreshTasks() — message sent successfully") }
                .addOnFailureListener { e ->
                    Log.e(TAG, "refreshTasks() — failed to send message", e)
                    _statusText.value = "Send error"
                    _isLoading.value = false
                }
        }
    }

    fun completeTask(taskId: String) {
        Log.d(TAG, "completeTask($taskId)")
        val payload = JSONObject().apply {
            put("task_id", taskId)
            put("status", "completed")
        }
        getPhoneNodeId { phoneId ->
            Wearable.getMessageClient(appContext).sendMessage(phoneId, "/update_task_status", payload.toString().toByteArray())
        }
        // Optimistically remove from list, then refresh for server confirmation
        _tasks.value = _tasks.value.filter { it.id != taskId }
        viewModelScope.launch {
            kotlinx.coroutines.delay(500)
            refreshTasks()
        }
    }

    fun snoozeTask(taskId: String, minutes: Int) {
        Log.d(TAG, "snoozeTask($taskId, $minutes min)")
        val payload = JSONObject().apply {
            put("task_id", taskId)
            put("type", "time")
            put("minutes", minutes)
        }
        getPhoneNodeId { phoneId ->
            Wearable.getMessageClient(appContext).sendMessage(phoneId, "/snooze_task", payload.toString().toByteArray())
        }
        // Optimistically remove, then refresh
        _tasks.value = _tasks.value.filter { it.id != taskId }
        viewModelScope.launch {
            kotlinx.coroutines.delay(500)
            refreshTasks()
        }
    }

    override fun onMessageReceived(messageEvent: MessageEvent) {
        val path = messageEvent.path
        val jsonString = String(messageEvent.data)
        Log.d(TAG, "onMessageReceived: path=$path, data=${jsonString.take(200)}")

        viewModelScope.launch {
            try {
                when (path) {
                    "/response/get_tasks" -> {
                        val array = JSONArray(jsonString)
                        val list = mutableListOf<WearTask>()
                        for (i in 0 until array.length()) {
                            val obj = array.getJSONObject(i)
                            val status = obj.optString("status", "needs_action")
                            if (status != "needs_action") continue
                            list.add(
                                WearTask(
                                    id = obj.optString("id", ""),
                                    title = obj.optString("title", ""),
                                    description = obj.optString("description").takeIf { it.isNotEmpty() && it != "null" },
                                    priority = obj.optString("priority", "medium"),
                                    dueDate = obj.optString("due_date").takeIf { it.isNotEmpty() && it != "null" },
                                    status = status,
                                    groupName = obj.optString("group_name").takeIf { it.isNotEmpty() && it != "null" },
                                    groupEmoji = obj.optString("group_emoji").takeIf { it.isNotEmpty() && it != "null" }
                                )
                            )
                        }
                        _tasks.value = list
                        _isLoading.value = false
                        _statusText.value = if (list.isEmpty()) "No tasks" else ""
                    }
                    "/response/update_task_status", "/response/snooze_task" -> {
                        refreshTasks()
                    }
                    "/response/error" -> {
                        val json = JSONObject(jsonString)
                        _statusText.value = "Error: ${json.optString("error", "Failed")}"
                        _isLoading.value = false
                    }
                }
            } catch (e: Exception) {
                Log.e("TasksViewModel", "Error parsing response: $path", e)
                _statusText.value = "Error"
                _isLoading.value = false
            }
        }
    }

    private fun getPhoneNodeId(action: (String) -> Unit) {
        Log.d(TAG, "getPhoneNodeId() — querying connected nodes")
        Wearable.getNodeClient(appContext).connectedNodes
            .addOnSuccessListener { nodes ->
                Log.d(TAG, "getPhoneNodeId() — found ${nodes.size} nodes: ${nodes.map { it.id }}")
                val nodeId = nodes.firstOrNull()?.id
                if (nodeId != null) {
                    action(nodeId)
                } else {
                    Log.w(TAG, "getPhoneNodeId() — no connected phone!")
                    _statusText.value = "Phone not found"
                    _isLoading.value = false
                }
            }
            .addOnFailureListener { e ->
                Log.e(TAG, "getPhoneNodeId() — failed", e)
                _statusText.value = "Connection error"
                _isLoading.value = false
            }
    }

    override fun onCleared() {
        super.onCleared()
        if (::appContext.isInitialized) {
            Wearable.getMessageClient(appContext).removeListener(this)
        }
    }
}
