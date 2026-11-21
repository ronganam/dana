import { Tool } from "@raycast/api";
import { getTasks, updateTask } from "../lib/api";
import { formatDue } from "../lib/format";

type Input = {
  /**
   * The task id (from get-tasks).
   */
  id: string;
  /**
   * Set to "completed" to mark the task done, or "open" to reopen it.
   */
  status?: "completed" | "open";
  /**
   * New title.
   */
  title?: string;
  /**
   * New notes. Use an empty string to clear them.
   */
  notes?: string;
  /**
   * New due date in local time, formatted YYYY-MM-DDTHH:mm. Use an empty string to remove it.
   */
  dueDate?: string;
  /**
   * New priority.
   */
  priority?: "low" | "medium" | "high";
};

/**
 * Update a task: complete or reopen it, or change its title, notes, due date or priority.
 */
export default async function tool(input: Input) {
  return updateTask(input.id, {
    status: input.status === undefined ? undefined : input.status === "completed" ? "completed" : "needs_action",
    title: input.title,
    description: input.notes,
    due_date: input.dueDate,
    priority: input.priority,
  });
}

export const confirmation: Tool.Confirmation<Input> = async (input) => {
  const task = (await getTasks()).find((t) => t.id === input.id);
  const changes = [
    input.status && { name: "Status", value: input.status === "completed" ? "Completed" : "Open" },
    input.title !== undefined && { name: "Title", value: input.title },
    input.dueDate !== undefined && { name: "Due", value: input.dueDate ? formatDue(input.dueDate) : "None" },
    input.priority && { name: "Priority", value: input.priority },
    input.notes !== undefined && { name: "Notes", value: input.notes || "None" },
  ].filter((c): c is { name: string; value: string } => Boolean(c));
  return {
    message:
      input.status === "completed" ? `Complete “${task?.title ?? input.id}”?` : `Update “${task?.title ?? input.id}”?`,
    info: changes,
  };
};
