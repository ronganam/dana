import { Color, Icon, launchCommand, LaunchType, MenuBarExtra, open, showHUD } from "@raycast/api";
import { useCachedPromise } from "@raycast/utils";
import * as api from "./lib/api";
import { dueBucket, errorMessage, formatDue, parseDueDate } from "./lib/format";
import type { Task } from "./lib/types";

const byDue = (a: Task, b: Task) =>
  (parseDueDate(a.due_date)?.getTime() ?? Infinity) - (parseDueDate(b.due_date)?.getTime() ?? Infinity);

export default function MenuBarTasks() {
  const { data, isLoading, revalidate } = useCachedPromise(api.getTasks, [], { keepPreviousData: true });
  const openTasks = (data ?? []).filter((t) => t.status !== "completed");
  const overdue = openTasks.filter((t) => dueBucket(t) === "overdue").sort(byDue);
  const today = openTasks.filter((t) => dueBucket(t) === "today").sort(byDue);
  const urgent = overdue.length + today.length;

  async function complete(task: Task) {
    try {
      await api.updateTask(task.id, { status: "completed" });
      await showHUD(`✓ ${task.title}`);
      revalidate();
    } catch (error) {
      await showHUD(`Couldn't complete task: ${errorMessage(error)}`);
    }
  }

  const item = (task: Task) => (
    <MenuBarExtra.Item
      key={task.id}
      icon={{ source: Icon.Circle, tintColor: dueBucket(task) === "overdue" ? Color.Red : undefined }}
      title={task.title}
      subtitle={formatDue(task.due_date)}
      tooltip="Click to complete"
      onAction={() => complete(task)}
    />
  );

  return (
    <MenuBarExtra
      isLoading={isLoading}
      icon={{ source: Icon.CheckList, tintColor: overdue.length ? Color.Red : undefined }}
      title={urgent > 0 ? String(urgent) : undefined}
      tooltip={urgent > 0 ? `${urgent} task${urgent === 1 ? "" : "s"} due` : "No tasks due today"}
    >
      {overdue.length > 0 && <MenuBarExtra.Section title="Overdue">{overdue.map(item)}</MenuBarExtra.Section>}
      {today.length > 0 && <MenuBarExtra.Section title="Today">{today.map(item)}</MenuBarExtra.Section>}
      {urgent === 0 && <MenuBarExtra.Item title="Nothing due today" icon={Icon.Checkmark} />}
      <MenuBarExtra.Section>
        <MenuBarExtra.Item
          title="Manage Tasks"
          icon={Icon.List}
          subtitle={`${openTasks.length} open`}
          onAction={() => launchCommand({ name: "manage-tasks", type: LaunchType.UserInitiated })}
        />
        <MenuBarExtra.Item
          title="Create Task"
          icon={Icon.Plus}
          onAction={() => launchCommand({ name: "create-task", type: LaunchType.UserInitiated })}
        />
        <MenuBarExtra.Item
          title="Ask Dana"
          icon={Icon.Message}
          onAction={() => launchCommand({ name: "ask-dana", type: LaunchType.UserInitiated })}
        />
        <MenuBarExtra.Item title="Open Dana on the Web" icon={Icon.Globe} onAction={() => open(api.serverUrl())} />
      </MenuBarExtra.Section>
    </MenuBarExtra>
  );
}
