"""The ``tasks`` addon (Tasks)."""

from core.addons import Addon, LlmJob, Nav, Page, PromptPart, Need, Touches
from addons.tasks import groups

# Connecting it gives the user the default task lists.
addon = Addon(
    "tasks", order=20, default_on=True,
    web=(Page("tasks", panel="tasks.js", style="tasks.css", nav=Nav("Tasks", icon="check-square", order=5, primary=True)),), llm_jobs={"task_emoji": LlmJob("fast", "Task emoji"), "task_group": LlmJob("fast", "Task grouping")}, title="Tasks", icon="list-checks",
    needs=(Need("addon", "homeassistant", "Reminders when you arrive at a place, leave it or get in the car", optional=True),
           Need("addon", "phone", "Reminders as notifications on your phone", optional=True)),
    touches=Touches(reads=("homeassistant", "people", "phone")),
    description="Task lists, reminders and their triggers",
    features=("Tasks and lists, by chat or voice", "Reminders at a time, when you arrive somewhere or wake up"),
    examples=("Remind me tomorrow at 9 to call the bank", "What's on my list today?"), on_connect=groups.ensure_default_groups,
    prompt_parts=(
        PromptPart("tools", "task list/todos", rank=30),
        PromptPart("undoable", "adding/completing/editing tasks", rank=10),
        PromptPart("done_examples", "'Moved the dentist to 9:00.'", rank=10),
        PromptPart("action_examples", "create a reminder", rank=30),
        PromptPart("routing_heading", "TASKS", rank=10),
        PromptPart("routing", "Use add_task with a time_trigger for reminders and personal to-dos that need a "
                   "notification at a specific time. ", rank=10),
        PromptPart("routing", "IMPORTANT: When you add a task, tell the user it was added as a 'task' or 'reminder' "
                   "or 'משימה' or 'תזכורת'. Never say 'added to your calendar' or 'ליומן' — tasks are NOT calendar "
                   "events. Say something like 'added as a reminder' or 'added to your task list' instead. ",
                   rank=40, needs=("calendar",)),
    ),
)
