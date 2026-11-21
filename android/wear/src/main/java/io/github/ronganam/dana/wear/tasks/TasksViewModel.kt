package io.github.ronganam.dana.wear.tasks

import io.github.ronganam.dana.protocol.SnoozeRequest
import io.github.ronganam.dana.protocol.TaskStatusRequest
import io.github.ronganam.dana.protocol.WearPaths
import android.content.Context
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import android.app.Application
import androidx.annotation.StringRes
import io.github.ronganam.dana.wear.R
import androidx.lifecycle.viewModelScope
import io.github.ronganam.dana.wear.debug.DebugSamples
import com.google.android.gms.wearable.MessageClient
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.Wearable
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import io.github.ronganam.dana.protocol.WearReply
import io.github.ronganam.dana.protocol.WearJson
import io.github.ronganam.dana.protocol.WearTaskItem
import kotlinx.serialization.builtins.ListSerializer

class TasksViewModel(app: Application) : AndroidViewModel(app), MessageClient.OnMessageReceivedListener {
    private fun str(@StringRes id: Int): String = getApplication<Application>().getString(id)


    companion object {
        private const val TAG = "TasksViewModel"
    }

    private lateinit var appContext: Context

    private val _tasks = MutableStateFlow<List<WearTask>>(emptyList())
    val tasks: StateFlow<List<WearTask>> = _tasks.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private val _statusText = MutableStateFlow(str(R.string.tasks_status_loading))
    val statusText: StateFlow<String> = _statusText.asStateFlow()

    fun initialize(context: Context) {
        appContext = context.applicationContext
        Log.d(TAG, "initialize() — registering message listener")
        Wearable.getMessageClient(appContext).addListener(this)
        refreshTasks()
    }

    fun refreshTasks() {
        Log.d(TAG, "refreshTasks() — requesting phone node")
        DebugSamples.tasks(appContext)?.let { showTasks(it); return }
        _isLoading.value = true
        _statusText.value = str(R.string.tasks_status_loading)
        getPhoneNodeId { phoneId ->
            Log.d(TAG, "refreshTasks() — sending ${WearPaths.GET_TASKS} to $phoneId")
            Wearable.getMessageClient(appContext).sendMessage(phoneId, WearPaths.GET_TASKS, null)
                .addOnSuccessListener { Log.d(TAG, "refreshTasks() — message sent successfully") }
                .addOnFailureListener { e ->
                    Log.e(TAG, "refreshTasks() — failed to send message", e)
                    _statusText.value = str(R.string.tasks_status_send_error)
                    _isLoading.value = false
                }
        }
    }

    fun completeTask(taskId: String) {
        Log.d(TAG, "completeTask($taskId)")
        DebugSamples.removeTask(appContext, taskId)
        val payload = TaskStatusRequest(taskId, "completed").encode()
        getPhoneNodeId { phoneId ->
            Wearable.getMessageClient(appContext).sendMessage(phoneId, WearPaths.UPDATE_TASK_STATUS, payload.toByteArray())
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
        DebugSamples.removeTask(appContext, taskId)
        val payload = SnoozeRequest(taskId, "time", minutes = minutes).encode()
        getPhoneNodeId { phoneId ->
            Wearable.getMessageClient(appContext).sendMessage(phoneId, WearPaths.SNOOZE_TASK, payload.toByteArray())
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
                    WearPaths.response(WearPaths.GET_TASKS) ->
                        showTasks(WearJson.decodeFromString(ListSerializer(WearTaskItem.serializer()), jsonString))
                    WearPaths.response(WearPaths.UPDATE_TASK_STATUS), WearPaths.response(WearPaths.SNOOZE_TASK) -> {
                        refreshTasks()
                    }
                    WearPaths.RESPONSE_ERROR -> {
                        val error = WearReply.decode(jsonString)?.error ?: str(R.string.failed)
                        _statusText.value = getApplication<Application>().getString(R.string.error_detail, error)
                        _isLoading.value = false
                    }
                }
            } catch (e: Exception) {
                Log.e("TasksViewModel", "Error parsing response: $path", e)
                _statusText.value = str(R.string.tasks_status_error)
                _isLoading.value = false
            }
        }
    }

    private fun showTasks(items: List<WearTaskItem>) {
        fun given(v: String?) = v?.takeIf { it.isNotEmpty() && it != "null" }
        val list = items
            .filter { it.status == "needs_action" }
            .map {
                WearTask(
                    id = it.id,
                    title = it.title,
                    description = given(it.description),
                    priority = it.priority,
                    dueDate = given(it.dueDate),
                    status = it.status,
                    groupName = given(it.groupName),
                    groupEmoji = given(it.groupEmoji)
                )
            }
        _tasks.value = list
        _isLoading.value = false
        _statusText.value = if (list.isEmpty()) str(R.string.tasks_status_none) else ""
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
                    _statusText.value = str(R.string.phone_not_found)
                    _isLoading.value = false
                }
            }
            .addOnFailureListener { e ->
                Log.e(TAG, "getPhoneNodeId() — failed", e)
                _statusText.value = str(R.string.tasks_status_connection_error)
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
