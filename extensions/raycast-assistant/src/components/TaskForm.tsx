import { Action, ActionPanel, Form, Icon, showToast, Toast, useNavigation } from "@raycast/api";
import { FormValidation, useCachedPromise, useForm } from "@raycast/utils";
import * as api from "../lib/api";
import { errorMessage, parseDueDate, PRIORITY, toDueDateString } from "../lib/format";
import type { Priority, Task } from "../lib/types";

interface Values {
  title: string;
  description: string;
  priority: string;
  group: string;
  due: Date | null;
}

const AUTO_GROUP = "__auto__";

/**
 * Create a task, or edit `task` when given. Calls `onSaved` after a successful
 * save; pops itself unless it is the command's root view (`isRoot`).
 */
export function TaskForm({
  task,
  isRoot,
  draftValues,
  onSaved,
}: {
  task?: Task;
  isRoot?: boolean;
  draftValues?: Partial<Values>;
  onSaved?: () => void;
}) {
  const { pop } = useNavigation();
  const { data: groups, isLoading } = useCachedPromise(api.getTaskGroups, [], { keepPreviousData: true });

  const { handleSubmit, itemProps, reset, focus } = useForm<Values>({
    initialValues: {
      title: task?.title ?? draftValues?.title ?? "",
      description: task?.description ?? draftValues?.description ?? "",
      priority: task?.priority ?? draftValues?.priority ?? "medium",
      group: task?.group_id ?? draftValues?.group ?? AUTO_GROUP,
      due: parseDueDate(task?.due_date) ?? (draftValues?.due ? new Date(draftValues.due) : null),
    },
    validation: { title: FormValidation.Required },
    async onSubmit(values) {
      const toast = await showToast({ style: Toast.Style.Animated, title: task ? "Saving task" : "Creating task" });
      const body = {
        title: values.title.trim(),
        description: values.description.trim() || null,
        priority: values.priority as Priority,
        due_date: toDueDateString(values.due),
        group_id: values.group === AUTO_GROUP ? null : values.group,
      };
      try {
        if (task) {
          await api.updateTask(task.id, body);
        } else {
          // Without a group the server picks one from the title.
          await api.createTask({ ...body, group_id: body.group_id ?? undefined });
        }
        toast.style = Toast.Style.Success;
        toast.title = task ? "Task saved" : "Task created";
        toast.message = body.title;
        onSaved?.();
        if (isRoot && !task) {
          reset({ title: "", description: "", priority: "medium", group: AUTO_GROUP, due: null });
          focus("title");
        } else {
          pop();
        }
      } catch (error) {
        toast.style = Toast.Style.Failure;
        toast.title = task ? "Couldn't save task" : "Couldn't create task";
        toast.message = errorMessage(error);
      }
    },
  });

  return (
    <Form
      isLoading={isLoading}
      enableDrafts={isRoot && !task}
      navigationTitle={isRoot ? undefined : task ? "Edit Task" : "New Task"}
      actions={
        <ActionPanel>
          <Action.SubmitForm title={task ? "Save Task" : "Create Task"} icon={Icon.Check} onSubmit={handleSubmit} />
        </ActionPanel>
      }
    >
      <Form.TextField {...itemProps.title} title="Title" placeholder="What needs to be done?" autoFocus />
      <Form.TextArea {...itemProps.description} title="Notes" placeholder="Optional details" enableMarkdown />
      <Form.Separator />
      <Form.DatePicker {...itemProps.due} title="Due" type={Form.DatePicker.Type.DateTime} />
      <Form.Dropdown {...itemProps.priority} title="Priority">
        {(Object.keys(PRIORITY) as Priority[]).map((p) => (
          <Form.Dropdown.Item key={p} value={p} title={PRIORITY[p].title} icon={PRIORITY[p].icon} />
        ))}
      </Form.Dropdown>
      <Form.Dropdown {...itemProps.group} title="Group">
        {!task && <Form.Dropdown.Item value={AUTO_GROUP} title="Let Dana Choose" icon={Icon.Stars} />}
        {(groups ?? []).map((g) => (
          <Form.Dropdown.Item key={g.id} value={g.id} title={`${g.emoji ? `${g.emoji} ` : ""}${g.name}`} />
        ))}
        {task && !task.group_id && <Form.Dropdown.Item value={AUTO_GROUP} title="No Group" icon={Icon.Minus} />}
      </Form.Dropdown>
    </Form>
  );
}
