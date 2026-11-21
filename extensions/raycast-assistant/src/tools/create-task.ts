import { Tool } from "@raycast/api";
import { createTask } from "../lib/api";
import { formatDue } from "../lib/format";

type Input = {
  /**
   * Short title of the task, e.g. "Call the dentist".
   */
  title: string;
  /**
   * Optional longer notes.
   */
  notes?: string;
  /**
   * Optional due date and time in the user's local time, formatted YYYY-MM-DDTHH:mm.
   */
  dueDate?: string;
  /**
   * Priority. Defaults to "medium".
   */
  priority?: "low" | "medium" | "high";
};

/**
 * Create a task for the user. Dana files it in a fitting group automatically.
 */
export default async function tool(input: Input) {
  return createTask({
    title: input.title,
    description: input.notes || null,
    due_date: input.dueDate || null,
    priority: input.priority ?? "medium",
  });
}

export const confirmation: Tool.Confirmation<Input> = async (input) => ({
  message: "Create this task?",
  info: [
    { name: "Title", value: input.title },
    ...(input.dueDate ? [{ name: "Due", value: formatDue(input.dueDate) }] : []),
    ...(input.priority ? [{ name: "Priority", value: input.priority }] : []),
    ...(input.notes ? [{ name: "Notes", value: input.notes }] : []),
  ],
});
