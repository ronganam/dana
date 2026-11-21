import { getTasks } from "../lib/api";
import { describeTriggers } from "../lib/format";

type Input = {
  /**
   * Which tasks to return. Defaults to "open".
   */
  status?: "open" | "completed" | "all";
  /**
   * Only return tasks whose title, notes or group contain this text (case-insensitive).
   */
  search?: string;
};

/**
 * List the user's tasks. Due dates are local times (YYYY-MM-DDTHH:mm).
 */
export default async function tool(input: Input) {
  const status = input.status ?? "open";
  const query = input.search?.trim().toLowerCase();
  return (await getTasks())
    .filter((t) => status === "all" || (status === "completed" ? t.status === "completed" : t.status !== "completed"))
    .filter(
      (t) =>
        !query ||
        [t.title, t.description ?? "", t.group_name ?? ""].some((field) => field.toLowerCase().includes(query)),
    )
    .map((t) => ({
      id: t.id,
      title: t.title,
      notes: t.description ?? undefined,
      status: t.status,
      priority: t.priority,
      due_date: t.due_date ?? undefined,
      group: t.group_name ?? undefined,
      reminders: describeTriggers(t).map((r) => r.text),
    }));
}
