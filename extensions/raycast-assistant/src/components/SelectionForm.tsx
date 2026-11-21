import { Action, ActionPanel, Form, Icon, showToast, Toast, useNavigation } from "@raycast/api";
import type { SelectionRequest } from "../lib/types";

/** Pick several of Dana's options at once (selection prompts with `multiple: true`). */
export function SelectionForm({
  request,
  onSubmit,
}: {
  request: SelectionRequest;
  onSubmit: (indices: number[]) => void;
}) {
  const { pop } = useNavigation();
  return (
    <Form
      navigationTitle="Choose Options"
      actions={
        <ActionPanel>
          <Action.SubmitForm
            title="Send Choice"
            icon={Icon.Check}
            onSubmit={async (values: Record<string, boolean>) => {
              const indices = request.options.map((_, i) => i).filter((i) => values[`option-${i}`]);
              if (indices.length === 0) {
                await showToast({ style: Toast.Style.Failure, title: "Pick at least one option" });
                return;
              }
              pop();
              onSubmit(indices);
            }}
          />
        </ActionPanel>
      }
    >
      <Form.Description text={request.prompt || "Choose one or more:"} />
      {request.options.map((option, i) => (
        <Form.Checkbox key={`${i}-${option}`} id={`option-${i}`} label={option} />
      ))}
    </Form>
  );
}
