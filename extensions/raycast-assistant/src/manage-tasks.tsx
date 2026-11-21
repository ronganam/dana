import { Action, ActionPanel, Alert, Color, confirmAlert, Icon, Keyboard, List, showToast, Toast } from "@raycast/api";
import { MutatePromise, useCachedPromise, useCachedState } from "@raycast/utils";
import { TaskForm } from "./components/TaskForm";
import * as api from "./lib/api";
import {
  describeTriggers,
  DueBucket,
  dueBucket,
  errorMessage,
  formatDue,
  parseDueDate,
  parseServerTime,
  PRIORITY,
  relativeTime,
} from "./lib/format";
import type { Priority, SnoozeRequest, Task, TaskGroup } from "./lib/types";
import { mod } from "./lib/shortcuts";

const SECTIONS: { key: DueBucket; title: string }[] = [
  { key: "overdue", title: "Overdue" },
  { key: "today", title: "Today" },
  { key: "tomorrow", title: "Tomorrow" },
  { key: "upcoming", title: "Upcoming" },
  { key: "someday", title: "No Due Date" },
];

const PRIORITY_RANK: Record<Priority, number> = { high: 0, medium: 1, low: 2 };

function sortTasks(a: Task, b: Task): number {
  const da = parseDueDate(a.due_date)?.getTime() ?? Infinity;
  const db = parseDueDate(b.due_date)?.getTime() ?? Infinity;
  if (da !== db) return da - db;
  return (PRIORITY_RANK[a.priority] ?? 1) - (PRIORITY_RANK[b.priority] ?? 1);
}

/** Minutes from now until the next 9:00 local time (for "Tomorrow Morning"). */
function minutesUntilMorning(): number {
  const now = new Date();
  const target = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 9, 0);
  return Math.max(1, Math.round((target.getTime() - now.getTime()) / 60_000));
}

const SNOOZES: { title: string; icon: Icon; request: () => SnoozeRequest }[] = [
  { title: "15 Minutes", icon: Icon.Clock, request: () => ({ type: "time", minutes: 15 }) },
  { title: "1 Hour", icon: Icon.Clock, request: () => ({ type: "time", minutes: 60 }) },
  { title: "3 Hours", icon: Icon.Clock, request: () => ({ type: "time", minutes: 180 }) },
  { title: "Tomorrow Morning", icon: Icon.Sunrise, request: () => ({ type: "time", minutes: minutesUntilMorning() }) },
  { title: "When I Wake Up", icon: Icon.Sun, request: () => ({ type: "wake" }) },
  { title: "When I Get in the Car", icon: Icon.Car, request: () => ({ type: "car", action: "enter" }) },
  { title: "When I Get Out of the Car", icon: Icon.Car, request: () => ({ type: "car", action: "leave" }) },
];

export default function ManageTasks() {
  const {
    data: tasks,
    isLoading,
    mutate,
    revalidate,
  } = useCachedPromise(api.getTasks, [], {
    keepPreviousData: true,
    failureToastOptions: { title: "Couldn't load tasks" },
  });
  const { data: groups } = useCachedPromise(api.getTaskGroups, [], { keepPreviousData: true });
  const [filter, setFilter] = useCachedState<string>("tasks-filter", "active");
  const [showDetail, setShowDetail] = useCachedState<boolean>("tasks-detail", false);

  const all = tasks ?? [];
  const groupFilter = filter.startsWith("group:") ? filter.slice(6) : null;
  const visible = all.filter((t) => {
    if (filter === "completed") return t.status === "completed";
    if (t.status === "completed") return false;
    if (groupFilter) return t.group_id === groupFilter;
    return true;
  });

  const filterDropdown = (
    <List.Dropdown tooltip="Filter Tasks" value={filter} onChange={setFilter}>
      <List.Dropdown.Section>
        <List.Dropdown.Item value="active" title="Open Tasks" icon={Icon.Circle} />
        <List.Dropdown.Item value="completed" title="Completed" icon={Icon.CheckCircle} />
      </List.Dropdown.Section>
      {groups?.length ? (
        <List.Dropdown.Section title="Groups">
          {groups.map((g) => (
            <List.Dropdown.Item key={g.id} value={`group:${g.id}`} title={`${g.emoji ? `${g.emoji} ` : ""}${g.name}`} />
          ))}
        </List.Dropdown.Section>
      ) : null}
    </List.Dropdown>
  );

  const itemProps = { mutate, revalidate, groups: groups ?? [], showDetail, setShowDetail };

  return (
    <List
      isLoading={isLoading}
      isShowingDetail={showDetail && visible.length > 0}
      searchBarPlaceholder="Filter tasks…"
      searchBarAccessory={filterDropdown}
    >
      <List.EmptyView
        icon={filter === "completed" ? Icon.CheckCircle : Icon.Checkmark}
        title={filter === "completed" ? "No completed tasks" : "All caught up"}
        description="Press ⌘N to add a task."
        actions={
          <ActionPanel>
            <Action.Push
              title="Create Task"
              icon={Icon.Plus}
              shortcut={Keyboard.Shortcut.Common.New}
              target={<TaskForm onSaved={revalidate} />}
            />
          </ActionPanel>
        }
      />
      {filter === "completed" ? (
        <List.Section title="Completed" subtitle={String(visible.length)}>
          {[...visible]
            .sort(
              (a, b) =>
                (parseServerTime(b.updated_at)?.getTime() ?? 0) - (parseServerTime(a.updated_at)?.getTime() ?? 0),
            )
            .map((t) => (
              <TaskItem key={t.id} task={t} {...itemProps} />
            ))}
        </List.Section>
      ) : (
        SECTIONS.map(({ key, title }) => {
          const items = visible.filter((t) => dueBucket(t) === key).sort(sortTasks);
          if (!items.length) return null;
          return (
            <List.Section key={key} title={title} subtitle={String(items.length)}>
              {items.map((t) => (
                <TaskItem key={t.id} task={t} {...itemProps} />
              ))}
            </List.Section>
          );
        })
      )}
    </List>
  );
}

function taskMarkdown(task: Task): string {
  const parts = [`## ${task.title}`];
  if (task.description) parts.push(task.description);
  return parts.join("\n\n");
}

function TaskItem({
  task,
  mutate,
  revalidate,
  groups,
  showDetail,
  setShowDetail,
}: {
  task: Task;
  mutate: MutatePromise<Task[] | undefined>;
  revalidate: () => void;
  groups: TaskGroup[];
  showDetail: boolean;
  setShowDetail: (v: boolean) => void;
}) {
  const done = task.status === "completed";
  const bucket = dueBucket(task);
  const priority = PRIORITY[task.priority] ?? PRIORITY.medium;
  const triggers = describeTriggers(task);

  async function run(title: string, change: () => Promise<unknown>, optimistic: (t: Task) => Task | null) {
    const toast = await showToast({ style: Toast.Style.Animated, title });
    try {
      await mutate(change(), {
        optimisticUpdate: (list) =>
          (list ?? []).flatMap((t) => {
            if (t.id !== task.id) return [t];
            const next = optimistic(t);
            return next ? [next] : [];
          }),
      });
      toast.style = Toast.Style.Success;
      toast.message = task.title;
    } catch (error) {
      toast.style = Toast.Style.Failure;
      toast.title = "Something went wrong";
      toast.message = errorMessage(error);
    }
  }

  const toggle = () =>
    run(
      done ? "Reopened" : "Completed",
      () => api.updateTask(task.id, { status: done ? "needs_action" : "completed" }),
      (t) => ({ ...t, status: done ? "needs_action" : "completed" }),
    );

  const remove = async () => {
    const ok = await confirmAlert({
      title: "Delete task?",
      message: task.title,
      icon: { source: Icon.Trash, tintColor: Color.Red },
      primaryAction: { title: "Delete", style: Alert.ActionStyle.Destructive },
    });
    if (ok)
      await run(
        "Deleted",
        () => api.deleteTask(task.id),
        () => null,
      );
  };

  const accessories: List.Item.Accessory[] = [];
  if (!showDetail) {
    for (const trig of triggers) accessories.push({ icon: trig.icon, tooltip: trig.text });
    if (task.group_name) {
      accessories.push({
        tag: {
          value: `${task.group_emoji ? `${task.group_emoji} ` : ""}${task.group_name}`,
          color: task.group_color ?? undefined,
        },
      });
    }
  }
  if (task.due_date) {
    accessories.push({
      text: { value: formatDue(task.due_date), color: !done && bucket === "overdue" ? Color.Red : undefined },
      icon: Icon.Calendar,
    });
  } else if (done) {
    accessories.push({ text: relativeTime(parseServerTime(task.updated_at)) });
  }

  return (
    <List.Item
      id={task.id}
      title={task.title}
      subtitle={!showDetail && task.description ? task.description.split("\n")[0] : undefined}
      icon={
        done
          ? { source: Icon.CheckCircle, tintColor: Color.Green }
          : { source: Icon.Circle, tintColor: task.priority === "medium" ? undefined : priority.color }
      }
      keywords={[task.group_name ?? "", task.description ?? ""].filter(Boolean)}
      accessories={accessories}
      detail={
        <List.Item.Detail
          markdown={taskMarkdown(task)}
          metadata={
            <List.Item.Detail.Metadata>
              <List.Item.Detail.Metadata.Label title="Status" text={done ? "Completed" : "Open"} />
              <List.Item.Detail.Metadata.Label title="Priority" text={priority.title} icon={priority.icon} />
              {task.due_date && <List.Item.Detail.Metadata.Label title="Due" text={formatDue(task.due_date)} />}
              {task.group_name && (
                <List.Item.Detail.Metadata.TagList title="Group">
                  <List.Item.Detail.Metadata.TagList.Item
                    text={`${task.group_emoji ?? ""} ${task.group_name}`.trim()}
                    color={task.group_color ?? undefined}
                  />
                </List.Item.Detail.Metadata.TagList>
              )}
              {triggers.map((trig) => (
                <List.Item.Detail.Metadata.Label key={trig.text} title="Trigger" text={trig.text} icon={trig.icon} />
              ))}
              <List.Item.Detail.Metadata.Separator />
              <List.Item.Detail.Metadata.Label
                title="Updated"
                text={parseServerTime(task.updated_at)?.toLocaleString() ?? task.updated_at}
              />
            </List.Item.Detail.Metadata>
          }
        />
      }
      actions={
        <ActionPanel>
          <ActionPanel.Section>
            <Action
              title={done ? "Reopen Task" : "Complete Task"}
              icon={done ? Icon.ArrowCounterClockwise : Icon.CheckCircle}
              onAction={toggle}
            />
            <Action
              title={showDetail ? "Hide Details" : "Show Details"}
              icon={Icon.Sidebar}
              shortcut={mod("i")}
              onAction={() => setShowDetail(!showDetail)}
            />
            <Action.Push
              title="Edit Task"
              icon={Icon.Pencil}
              shortcut={Keyboard.Shortcut.Common.Edit}
              target={<TaskForm task={task} onSaved={revalidate} />}
            />
          </ActionPanel.Section>
          <ActionPanel.Section>
            {!done && (
              <ActionPanel.Submenu title="Snooze…" icon={Icon.Alarm} shortcut={Keyboard.Shortcut.Common.Save}>
                {SNOOZES.map((s) => (
                  <Action
                    key={s.title}
                    title={s.title}
                    icon={s.icon}
                    onAction={() =>
                      run(
                        `Snoozed: ${s.title}`,
                        () => api.snoozeTask(task.id, s.request()),
                        (t) => ({ ...t, status: "needs_action" }),
                      ).then(revalidate)
                    }
                  />
                ))}
              </ActionPanel.Submenu>
            )}
            <ActionPanel.Submenu title="Set Priority…" icon={Icon.Exclamationmark} shortcut={mod("p", true)}>
              {(Object.keys(PRIORITY) as Priority[]).map((p) => (
                <Action
                  key={p}
                  title={PRIORITY[p].title}
                  icon={PRIORITY[p].icon}
                  onAction={() =>
                    run(
                      `Priority: ${PRIORITY[p].title}`,
                      () => api.updateTask(task.id, { priority: p }),
                      (t) => ({ ...t, priority: p }),
                    )
                  }
                />
              ))}
            </ActionPanel.Submenu>
            {groups.length > 0 && (
              <ActionPanel.Submenu title="Move to Group…" icon={Icon.Folder} shortcut={mod("g", true)}>
                {groups.map((g) => (
                  <Action
                    key={g.id}
                    title={`${g.emoji ? `${g.emoji} ` : ""}${g.name}`}
                    onAction={() =>
                      run(
                        `Moved to ${g.name}`,
                        () => api.updateTask(task.id, { group_id: g.id }),
                        (t) => ({
                          ...t,
                          group_id: g.id,
                          group_name: g.name,
                          group_emoji: g.emoji,
                          group_color: g.color,
                        }),
                      )
                    }
                  />
                ))}
              </ActionPanel.Submenu>
            )}
          </ActionPanel.Section>
          <ActionPanel.Section>
            <Action.Push
              title="Create Task"
              icon={Icon.Plus}
              shortcut={Keyboard.Shortcut.Common.New}
              target={<TaskForm onSaved={revalidate} />}
            />
            <Action.CopyToClipboard title="Copy Title" content={task.title} shortcut={Keyboard.Shortcut.Common.Copy} />
            <Action
              title="Refresh"
              icon={Icon.ArrowClockwise}
              shortcut={Keyboard.Shortcut.Common.Refresh}
              onAction={revalidate}
            />
          </ActionPanel.Section>
          <ActionPanel.Section>
            <Action
              title="Delete Task"
              icon={Icon.Trash}
              style={Action.Style.Destructive}
              shortcut={Keyboard.Shortcut.Common.Remove}
              onAction={remove}
            />
          </ActionPanel.Section>
        </ActionPanel>
      }
    />
  );
}
