// Shapes returned by the Dana server (core/server.py, core/session.py).

export interface ToolCallInfo {
  toolName: string;
  toolSummary: string;
  parameters: string;
  response: string;
  requiresApproval: boolean;
  toolCallId: string;
  toolArgs: string;
  isCollapsed: boolean;
}

export interface FormattedMessage {
  id: string;
  sender: "You" | "Assistant" | "Tool";
  message: string;
  isUser: boolean;
  toolCall: ToolCallInfo | null;
  images?: string[];
}

export interface SelectionRequest {
  prompt: string;
  options: string[];
  multiple: boolean;
}

export interface RunInfo {
  run_id: string;
  kind: string;
  status: string;
  last_seq?: number;
}

/** The authoritative session state (`state` SSE frame, GET /api/sessions/{id}). */
export interface SessionState {
  session_id: string;
  status: "idle" | "running" | "waiting_approval" | "awaiting_selection" | "iterations_exceeded" | "error" | string;
  title: string;
  pending_approval: unknown;
  selection_request: SelectionRequest | null;
  messages: FormattedMessage[];
  active_run: RunInfo | null;
  extend_summary?: string;
  error?: string;
  updated_at?: string;
}

export interface SessionSummary {
  session_id: string;
  status: string;
  title: string;
  snippet: string;
  updated_at: string;
}

export interface ModelProfile {
  id: string;
  label: string;
  description: string;
  model: string;
  shows_reasoning: boolean;
}

export interface ModelCatalog {
  default: string;
  models: ModelProfile[];
}

/** One event of a run's live log (see the docstring at the top of core/runs.py). */
export type RunEvent =
  | { type: "run_start"; kind: string; model?: ModelProfile }
  | { type: "transcription"; text: string }
  | { type: "step_start"; iteration: number; max_iterations: number }
  | { type: "reasoning_delta"; iteration: number; delta: string }
  | { type: "text_delta"; iteration: number; delta: string }
  | { type: "tool_call"; id: string; name: string; iteration: number }
  | { type: "tool_start"; id: string; name: string; args: unknown; iteration: number }
  | { type: "tool_end"; id: string; name: string; ok: boolean; result: string; duration_ms: number }
  | { type: "retry"; iteration: number; attempt: number; message: string }
  | { type: "final"; content: string }
  | { type: "error"; message: string }
  | { type: "approval_required"; id: string; name: string; args: unknown }
  | { type: "selection_required"; prompt: string; options: string[]; multiple: boolean }
  | { type: "iterations_exceeded"; iteration: number; max_iterations: number; summary: string }
  | { type: "cancelled" }
  | { type: "run_end"; status: string };

export type StreamFrame = (RunEvent & { run_id: string; seq: number }) | { type: "state"; state: SessionState };

export type Priority = "low" | "medium" | "high";
export type TaskStatus = "needs_action" | "completed";

export interface Task {
  id: string;
  title: string;
  description: string | null;
  priority: Priority;
  due_date: string | null;
  status: TaskStatus;
  /** JSON string: {time?: iso, location?: {location, event}, car?: "enter"|"exit", wake?: true} */
  triggers: string | null;
  group_id: string | null;
  group_name: string | null;
  group_emoji: string | null;
  group_color: string | null;
  created_at: string;
  updated_at: string;
}

export interface TaskGroup {
  id: string;
  name: string;
  emoji: string;
  color: string;
  description?: string;
}

export interface TaskInput {
  title?: string;
  description?: string | null;
  priority?: Priority;
  due_date?: string | null;
  status?: TaskStatus;
  group_id?: string | null;
}

export type SnoozeRequest =
  | { type: "time"; minutes: number }
  | { type: "car"; action: "enter" | "leave" }
  | { type: "wake" }
  | { type: "location"; location: string; event: "arrive" | "leave" };

export interface Person {
  id: string;
  name: string;
}

export type ShoppingAccess = "owner" | "edit" | "read";

/** One item of a shopping list (`GET /api/shopping`, docs/shopping.md). */
export interface ShoppingItem {
  id: string;
  name: string;
  quantity: string;
  note: string;
  /** An aisle key from `categories` ("other" until the server knows it). */
  category: string;
  checked: boolean;
  checked_at: string | null;
  checked_by: Person | null;
  added_by: Person | null;
  created_at: string;
}

export interface ShoppingCategory {
  key: string;
  name: string;
}

export interface ShoppingUndo {
  id: string;
  label: string;
}

/** `GET /api/shopping`: the whole screen; every write answers with the same fields plus `undo`. */
export interface ShoppingScreen {
  list: {
    id: string;
    name: string;
    access: ShoppingAccess;
    owner: Person;
    people: (Person & { access: ShoppingAccess })[];
    version: number;
  };
  categories: ShoppingCategory[];
  items: ShoppingItem[];
  counts: { to_buy: number; bought: number };
  usuals: { name: string; category: string; times: number }[];
  invites: { list_id: string; name: string; owner: Person; access: ShoppingAccess }[];
  lists: { id: string; name: string; owner: Person; access: ShoppingAccess; active: boolean }[];
}

export interface ShoppingChange extends ShoppingScreen {
  undo: ShoppingUndo | null;
}

export interface ShoppingAdded extends ShoppingChange {
  results: { id: string; name: string; status: "added" | "already" | "restored" }[];
}
