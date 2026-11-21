import { LaunchProps } from "@raycast/api";
import { TaskForm } from "./components/TaskForm";

export default function CreateTask(props: LaunchProps) {
  return <TaskForm isRoot draftValues={props.draftValues} />;
}
