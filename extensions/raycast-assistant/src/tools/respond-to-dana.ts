import { Tool } from "@raycast/api";
import { pendingInAiSession, respond } from "../lib/ai";

type Input = {
  /**
   * How to answer Dana:
   * - "approve" / "deny": the action she asked permission for
   * - "select": the choice she asked for (pass `selection`)
   * - "continue" / "stop": whether she may keep going after pausing
   */
  action: "approve" | "deny" | "select" | "continue" | "stop";
  /**
   * 0-based indices of the chosen options. Only for action "select".
   */
  selection?: number[];
};

/**
 * Answer Dana when ask-dana reported she is waiting: approve or deny an action, pick from the
 * options she offered, or let her continue after a pause. Returns her reply after resuming.
 */
export default async function tool(input: Input) {
  return respond(input.action, input.selection);
}

export const confirmation: Tool.Confirmation<Input> = async (input) => {
  if (input.action !== "approve") return undefined;
  const pending = await pendingInAiSession();
  const action = pending?.pendingAction;
  return {
    message: action ? `Let Dana run “${action.name}”?` : "Let Dana go ahead with her action?",
    info: action?.arguments ? [{ name: "Details", value: action.arguments }] : undefined,
  };
};
