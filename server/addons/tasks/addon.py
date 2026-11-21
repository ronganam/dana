"""The ``tasks`` addon (Tasks)."""

from core.addons import Addon, PromptPart
from addons.tasks import groups

# Connecting it gives the user the default task lists.
addon = Addon(
    "tasks", description="Task lists, tasks and their triggers", on_connect=groups.ensure_default_groups,
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
