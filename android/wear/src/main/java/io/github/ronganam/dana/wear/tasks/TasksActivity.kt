package io.github.ronganam.dana.wear.tasks

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.wear.compose.navigation.SwipeDismissableNavHost
import androidx.wear.compose.navigation.composable
import androidx.wear.compose.navigation.rememberSwipeDismissableNavController
import io.github.ronganam.dana.wear.tasks.ui.SnoozeScreen
import io.github.ronganam.dana.wear.tasks.ui.TaskListScreen

class TasksActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        setContent {
            io.github.ronganam.dana.wear.ui.theme.DanaWearTheme {
                val navController = rememberSwipeDismissableNavController()
                val viewModel: TasksViewModel = viewModel()

                androidx.compose.runtime.LaunchedEffect(viewModel) {
                    viewModel.initialize(this@TasksActivity)
                }

                SwipeDismissableNavHost(
                    navController = navController,
                    startDestination = "task_list"
                ) {
                    composable("task_list") {
                        TaskListScreen(
                            viewModel = viewModel,
                            onTaskSnooze = { taskId, taskTitle ->
                                navController.navigate("snooze/$taskId/${java.net.URLEncoder.encode(taskTitle, "UTF-8")}")
                            }
                        )
                    }
                    composable("snooze/{taskId}/{taskTitle}") { backStackEntry ->
                        val taskId = backStackEntry.arguments?.getString("taskId") ?: return@composable
                        val taskTitle = java.net.URLDecoder.decode(
                            backStackEntry.arguments?.getString("taskTitle") ?: "",
                            "UTF-8"
                        )
                        SnoozeScreen(
                            taskTitle = taskTitle,
                            onSnoozeSelected = { minutes ->
                                viewModel.snoozeTask(taskId, minutes)
                                navController.popBackStack()
                            },
                            onDismiss = { navController.popBackStack() }
                        )
                    }
                }
            }
        }
    }
}
