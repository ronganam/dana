import { LaunchProps } from "@raycast/api";
import { Chat } from "./components/Chat";

export default function AskDana(props: LaunchProps<{ arguments: Arguments.AskDana }>) {
  // A typed argument or fallback-search text starts a fresh chat with that message.
  const prompt = props.arguments.prompt?.trim() || props.fallbackText?.trim() || undefined;
  return <Chat initialPrompt={prompt} />;
}
