import { getPreferenceValues } from "@raycast/api";
import type {
  ModelCatalog,
  SessionState,
  SessionSummary,
  ShoppingAdded,
  ShoppingChange,
  ShoppingScreen,
  SnoozeRequest,
  StreamFrame,
  Task,
  TaskGroup,
  TaskInput,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body: Record<string, unknown> | null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function serverUrl(): string {
  return getPreferenceValues<Preferences>().serverUrl.trim().replace(/\/+$/, "");
}

function headers(json: boolean): Headers {
  const { apiToken } = getPreferenceValues<Preferences>();
  const h = new Headers({ Accept: json ? "application/json" : "text/event-stream" });
  if (apiToken?.trim()) h.set("Authorization", `Bearer ${apiToken.trim()}`);
  return h;
}

async function errorFrom(response: Response): Promise<ApiError> {
  const text = await response.text().catch(() => "");
  let body: Record<string, unknown> | null = null;
  try {
    body = JSON.parse(text);
  } catch {
    // not JSON
  }
  if (response.status === 401) {
    return new ApiError("Unauthorized — check the API Token in the extension preferences", 401, body);
  }
  const detail = (body?.error as string | undefined) || text || response.statusText;
  return new ApiError(`${detail} (HTTP ${response.status})`, response.status, body);
}

async function request(path: string, init: RequestInit & { json?: unknown } = {}, accept = true): Promise<Response> {
  const h = headers(accept);
  let body = init.body;
  if (init.json !== undefined) {
    h.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  let response: Response;
  try {
    response = await fetch(`${serverUrl()}${path}`, { ...init, headers: h, body });
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new Error(`Can't reach ${serverUrl()} — ${(error as Error).message}`);
  }
  if (!response.ok) throw await errorFrom(response);
  return response;
}

async function json<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const response = await request(path, init);
  return (await response.json()) as T;
}

// ---------------------------------------------------------------------------
// Assistant: every agent endpoint answers with Server-Sent Events.

/**
 * Read an SSE response frame by frame. Ends after the closing `state` frame or
 * when the server closes the stream. Aborting the signal only stops reading —
 * the run keeps going on the server and can be re-attached.
 */
async function readStream(response: Response, onFrame: (frame: StreamFrame) => void): Promise<void> {
  if (!response.body) return;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const flush = (block: string) => {
    const data = block
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).replace(/^ /, ""))
      .join("\n");
    if (!data) return; // heartbeat comment
    try {
      onFrame(JSON.parse(data) as StreamFrame);
    } catch (error) {
      console.error("Bad SSE frame", error, data);
    }
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let index: number;
    while ((index = buffer.indexOf("\n\n")) >= 0) {
      flush(buffer.slice(0, index));
      buffer = buffer.slice(index + 2);
    }
  }
  if (buffer.trim()) flush(buffer);
}

export interface StreamOptions {
  onFrame: (frame: StreamFrame) => void;
  signal?: AbortSignal;
}

/** POST /api/assistant/message → SSE. Throws ApiError(409, code=session_busy) while a run is going. */
export async function sendMessage(
  sessionId: string,
  message: string,
  model: string | undefined,
  { onFrame, signal }: StreamOptions,
): Promise<void> {
  const response = await request(
    "/api/assistant/message",
    { method: "POST", json: { session_id: sessionId, message, model }, signal },
    false,
  );
  await readStream(response, onFrame);
}

export type ResumeAction = "approve" | "deny" | "extend_iterations" | "decline_iterations";

/** POST /api/assistant/approve → SSE (resumes a paused run). */
export async function resume(
  sessionId: string,
  action: ResumeAction | { select: number[] },
  { onFrame, signal }: StreamOptions,
): Promise<void> {
  const body =
    typeof action === "string"
      ? { session_id: sessionId, action }
      : { session_id: sessionId, action: "select", selection: action.select };
  const response = await request("/api/assistant/approve", { method: "POST", json: body, signal }, false);
  await readStream(response, onFrame);
}

/** GET /api/sessions/{id}/stream → SSE. Replays the current run after `after`, then follows it live. */
export async function attach(
  sessionId: string,
  runId: string | undefined,
  after: number,
  { onFrame, signal }: StreamOptions,
): Promise<void> {
  const query = new URLSearchParams();
  if (runId) {
    query.set("run_id", runId);
    query.set("after", String(after));
  }
  const response = await request(
    `/api/sessions/${encodeURIComponent(sessionId)}/stream?${query}`,
    { method: "GET", signal },
    false,
  );
  await readStream(response, onFrame);
}

/** Stop the session's running agent work. */
export async function cancelRun(sessionId: string): Promise<void> {
  await json("/api/assistant/approve", { method: "POST", json: { session_id: sessionId, action: "cancel" } });
}

export const getModels = () => json<ModelCatalog>("/api/assistant/models");

// ---------------------------------------------------------------------------
// Sessions

export const getSessions = () => json<SessionSummary[]>("/api/sessions");

/** Session state, or null when the session doesn't exist yet. */
export async function getSession(sessionId: string): Promise<SessionState | null> {
  try {
    return await json<SessionState>(`/api/sessions/${encodeURIComponent(sessionId)}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export const deleteSession = (sessionId: string) =>
  json(`/api/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });

// ---------------------------------------------------------------------------
// Tasks

export const getTasks = () => json<Task[]>("/api/tasks");
export const getTaskGroups = () => json<TaskGroup[]>("/api/task-groups");

export const createTask = (task: TaskInput & { title: string }) =>
  json<{ status: string; id: string; group_id: string }>("/api/tasks", { method: "POST", json: task });

export const updateTask = (id: string, changes: TaskInput) =>
  json<{ status: string; id: string }>(`/api/tasks/${encodeURIComponent(id)}`, { method: "PUT", json: changes });

export const snoozeTask = (id: string, snooze: SnoozeRequest) =>
  json<{ status: string }>(`/api/tasks/${encodeURIComponent(id)}/snooze`, { method: "POST", json: snooze });

export const deleteTask = (id: string) => json(`/api/tasks/${encodeURIComponent(id)}`, { method: "DELETE" });

// ---------------------------------------------------------------------------
// Shopping list (docs/shopping.md, "API"): every write answers with the screen and its undo.

export const getShopping = () => json<ShoppingScreen>("/api/shopping");

export type ShoppingInput = string | { name: string; quantity?: string; note?: string };

/** Add typed text ("2 milk, bread") or items with their own quantity and note. */
export const addShopping = (body: { text: string } | { items: ShoppingInput[] }) =>
  json<ShoppingAdded>("/api/shopping/items", { method: "POST", json: body });

export const updateShoppingItem = (
  id: string,
  changes: { checked?: boolean; name?: string; quantity?: string; note?: string; category?: string },
) => json<ShoppingChange>(`/api/shopping/items/${encodeURIComponent(id)}`, { method: "PATCH", json: changes });

export const removeShoppingItem = (id: string) =>
  json<ShoppingChange>(`/api/shopping/items/${encodeURIComponent(id)}`, { method: "DELETE" });

export const clearBoughtShopping = () => json<ShoppingChange>("/api/shopping/clear-bought", { method: "POST" });

/** Reverse one change by its undo id (core/undo.py). 404 when expired, 409 when it can no longer be undone. */
export const undo = (id: string) =>
  json<{ ok: boolean; label: string }>(`/api/undo/${encodeURIComponent(id)}`, { method: "POST" });
