import type { FormattedMessage, RunEvent, SelectionRequest, SessionState } from "./types";

export interface ToolStep {
  id: string;
  name: string;
  args?: string;
  result?: string;
  state: "pending" | "running" | "done" | "failed" | "needs_approval";
  durationMs?: number;
}

/** One exchange: the user's message and everything Dana did in reply. */
export interface Turn {
  id: string;
  prompt: string;
  answer: string;
  reasoning: string;
  tools: ToolStep[];
  error?: string;
  /** Still being produced by a live run. */
  live?: boolean;
  createdAt?: number;
}

/** What the chat is waiting on from the user, if anything. */
export type Pending =
  | { kind: "approval"; tool: ToolStep }
  | { kind: "selection"; request: SelectionRequest }
  | { kind: "iterations"; summary: string }
  | null;

const prettyArgs = (args: unknown): string | undefined => {
  if (args === undefined || args === null) return undefined;
  if (typeof args === "string") {
    try {
      return JSON.stringify(JSON.parse(args), null, 2);
    } catch {
      return args;
    }
  }
  return JSON.stringify(args, null, 2);
};

/** Group the server's flat message list into turns. */
export function turnsFromMessages(messages: FormattedMessage[]): Turn[] {
  const turns: Turn[] = [];
  let current: Turn | null = null;
  const ensure = (id: string): Turn => {
    if (!current) {
      current = { id, prompt: "", answer: "", reasoning: "", tools: [] };
      turns.push(current);
    }
    return current;
  };
  for (const msg of messages) {
    if (msg.isUser) {
      current = { id: msg.id, prompt: msg.message, answer: "", reasoning: "", tools: [] };
      turns.push(current);
    } else if (msg.toolCall) {
      const tc = msg.toolCall;
      ensure(msg.id).tools.push({
        id: tc.toolCallId,
        name: tc.toolName,
        args: prettyArgs(tc.parameters),
        result: tc.response || undefined,
        state: tc.requiresApproval ? "needs_approval" : "done",
      });
    } else if (msg.message?.trim()) {
      const turn = ensure(msg.id);
      turn.answer = turn.answer ? `${turn.answer}\n\n${msg.message}` : msg.message;
    }
  }
  return turns;
}

export function pendingFromState(state: SessionState, turns: Turn[]): Pending {
  if (state.status === "awaiting_selection" && state.selection_request) {
    return { kind: "selection", request: state.selection_request };
  }
  if (state.status === "iterations_exceeded") {
    return { kind: "iterations", summary: state.extend_summary || "Dana hit her step limit." };
  }
  if (state.pending_approval) {
    const tool = turns
      .flatMap((t) => t.tools)
      .reverse()
      .find((t) => t.state === "needs_approval");
    if (tool) return { kind: "approval", tool };
  }
  return null;
}

/** Live state of the run being followed. */
export interface LiveRun {
  runId?: string;
  lastSeq: number;
  turn: Turn;
  /** Answer text produced by earlier steps (survives a retry of the current step). */
  committed: string;
  /** Text streamed by the current step. */
  stepText: string;
  status: string;
  pending: Pending;
}

export function newLiveRun(prompt: string): LiveRun {
  return {
    lastSeq: 0,
    committed: "",
    stepText: "",
    status: "running",
    pending: null,
    turn: { id: `live-${Date.now()}`, prompt, answer: "", reasoning: "", tools: [], live: true, createdAt: Date.now() },
  };
}

/** Fold one run event into the live state (pure). */
export function applyEvent(run: LiveRun, event: RunEvent & { run_id?: string; seq?: number }): LiveRun {
  const next: LiveRun = { ...run, turn: { ...run.turn, tools: [...run.turn.tools] } };
  if (event.run_id && event.run_id !== run.runId) {
    next.runId = event.run_id;
  }
  if (typeof event.seq === "number") next.lastSeq = event.seq;
  const t = next.turn;
  const upsertTool = (id: string, patch: Partial<ToolStep> & { name: string }) => {
    const i = t.tools.findIndex((x) => x.id === id);
    if (i >= 0) t.tools[i] = { ...t.tools[i], ...patch };
    else t.tools.push({ id, state: "pending", ...patch });
  };

  switch (event.type) {
    case "transcription":
      if (!t.prompt) t.prompt = event.text;
      break;
    case "step_start":
      // A new LLM call: text from earlier steps is kept, separated by a blank line.
      if (next.stepText.trim()) {
        next.committed = next.committed ? `${next.committed}\n\n${next.stepText.trim()}` : next.stepText.trim();
      }
      next.stepText = "";
      break;
    case "reasoning_delta":
      t.reasoning += event.delta;
      break;
    case "text_delta":
      next.stepText += event.delta;
      break;
    case "retry":
      next.stepText = "";
      break;
    case "tool_call":
      upsertTool(event.id, { name: event.name, state: "pending" });
      break;
    case "tool_start":
      upsertTool(event.id, { name: event.name, args: prettyArgs(event.args), state: "running" });
      break;
    case "tool_end":
      upsertTool(event.id, {
        name: event.name,
        result: event.result,
        state: event.ok ? "done" : "failed",
        durationMs: event.duration_ms,
      });
      break;
    case "approval_required": {
      upsertTool(event.id, { name: event.name, args: prettyArgs(event.args), state: "needs_approval" });
      const tool = t.tools.find((x) => x.id === event.id);
      if (tool) next.pending = { kind: "approval", tool };
      break;
    }
    case "selection_required":
      next.pending = {
        kind: "selection",
        request: { prompt: event.prompt, options: event.options, multiple: event.multiple },
      };
      break;
    case "iterations_exceeded":
      next.pending = { kind: "iterations", summary: event.summary };
      break;
    case "final":
      next.committed = event.content;
      next.stepText = "";
      break;
    case "error":
      t.error = event.message;
      break;
    case "cancelled":
      t.error = "Stopped.";
      break;
    case "run_end":
      next.status = event.status;
      break;
  }
  t.answer = [next.committed, next.stepText.trim()].filter(Boolean).join("\n\n");
  return next;
}

// ---------------------------------------------------------------------------
// Rendering

const TOOL_ICON: Record<ToolStep["state"], string> = {
  pending: "⏳",
  running: "⏳",
  done: "✅",
  failed: "❌",
  needs_approval: "✋",
};

export function humanToolName(name: string): string {
  return name.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

const fence = (text: string, lang = "") => {
  const ticks = text.includes("```") ? "````" : "```";
  return `${ticks}${lang}\n${text}\n${ticks}`;
};

const truncate = (text: string, max: number) => (text.length > max ? `${text.slice(0, max)}…` : text);

export function turnMarkdown(turn: Turn, opts: { showSteps: boolean; pending?: Pending }): string {
  const parts: string[] = [];
  if (turn.prompt)
    parts.push(
      turn.prompt
        .split("\n")
        .map((l) => `> ${l}`)
        .join("\n"),
    );

  if (opts.showSteps && turn.reasoning.trim()) {
    parts.push(
      `##### 💭 Thinking\n\n${turn.reasoning
        .trim()
        .split("\n")
        .map((l) => `> ${l}`)
        .join("\n")}`,
    );
  }

  const visibleTools = opts.showSteps ? turn.tools : turn.tools.filter((t) => t.state === "needs_approval");
  if (visibleTools.length) {
    parts.push(
      visibleTools
        .map((tool) => {
          const time = tool.durationMs ? ` · ${(tool.durationMs / 1000).toFixed(1)}s` : "";
          return `${TOOL_ICON[tool.state]} **${humanToolName(tool.name)}**${time}`;
        })
        .join("  \n"),
    );
  }

  if (turn.answer.trim()) parts.push(turn.answer.trim());
  else if (turn.live && !turn.error && !opts.pending) parts.push("_Dana is working…_");

  if (turn.error) parts.push(`**⚠️ ${turn.error}**`);

  const pending = opts.pending;
  if (pending?.kind === "approval") {
    parts.push(
      `---\n\n### ✋ Approve ${humanToolName(pending.tool.name)}?\n\n${pending.tool.args ? fence(truncate(pending.tool.args, 4000), "json") : ""}\n\nPress **↵** to approve, or open the actions to deny.`,
    );
  } else if (pending?.kind === "selection") {
    const options = pending.request.options.map((o, i) => `${i + 1}. ${o}`).join("\n");
    parts.push(
      `---\n\n### 👉 ${pending.request.prompt || "Choose an option"}\n\n${options}\n\nOpen the actions (**⌘K**) to choose${pending.request.multiple ? " one or more" : ""}.`,
    );
  } else if (pending?.kind === "iterations") {
    parts.push(`---\n\n### ⏸ Dana paused after many steps\n\n${pending.summary}\n\nPress **↵** to let her continue.`);
  }
  return parts.join("\n\n");
}

/** Plain-text conversation for copying. */
export function transcript(turns: Turn[]): string {
  return turns
    .map((t) => [t.prompt && `You: ${t.prompt}`, t.answer && `Dana: ${t.answer}`].filter(Boolean).join("\n\n"))
    .join("\n\n---\n\n");
}

export function newSessionId(): string {
  return `raycast-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}
