package io.github.ronganam.dana.wear.tasks

data class WearTask(
    val id: String,
    val title: String,
    val description: String?,
    val priority: String,
    val dueDate: String?,
    val status: String,
    val groupName: String?,
    val groupEmoji: String?
)
