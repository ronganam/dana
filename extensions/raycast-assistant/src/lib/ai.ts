import * as api from "./api";
import { humanToolName, pendingFromState, turnsFromMessages } from "./chat";
import type { SessionState } from "./types";

/** Conversation Raycast AI uses when it talks to Dana (shows up in Search Chats). */
export const AI_SESSION = "raycast-ai";

const MAX_WAIT_MS = 4 * 60_000;

/** Run a streaming call to the end and return the final session state. */
async function untilDone(start: (opts: api.StreamOptions) => Promise<void>): Promise<SessionState> {
  const box: { final: SessionState | null } = { final: null };
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), MAX_WAIT_MS);
  try {
    await start({
      signal: controller.signal,
      onFrame: (frame) => {
        if (frame.type === "state") box.final = frame.state;
      },
    });
  } catch (error) {
    if (error instanceof api.ApiError && error.status === 409) {
      // Dana is still busy with an earlier request: wait for it, then report.
      await api.attach(AI_SESSION, undefined, 0, {
        signal: controller.signal,
        onFrame: (frame) => {
          if (frame.type === "state") box.final = frame.state;
        },
      });
      throw new Error(
        `Dana was still busy with an earlier request, so this one wasn't sent. Her last reply: ${lastAnswer(box.final) || "(none)"}`,
      );
    }
    if (controller.signal.aborted) throw new Error("Dana is taking longer than expected; check Search Chats later.");
    throw error;
  } finally {
    clearTimeout(timer);
  }
  const state = box.final ?? (await api.getSession(AI_SESSION));
  if (!state) throw new Error("Dana didn't answer.");
  return state;
}

function lastAnswer(state: SessionState | null): string {
  if (!state) return "";
  const turns = turnsFromMessages(state.messages ?? []);
  return turns[turns.length - 1]?.answer ?? "";
}

export interface DanaReply {
  reply: string;
  status: "done" | "needs_approval" | "needs_selection" | "paused" | "error";
  /** Present when Dana is waiting for the user to approve a write action. */
  pendingAction?: { name: string; arguments: string };
  /** Present when Dana asked the user to choose. Answer with respond-to-dana (action "select", 0-based indices). */
  choice?: { prompt: string; options: string[]; multiple: boolean };
  note?: string;
}

/** Summarise a finished state for the AI. */
export function describe(state: SessionState): DanaReply {
  const turns = turnsFromMessages(state.messages ?? []);
  const reply = turns[turns.length - 1]?.answer ?? "";
  const pending = pendingFromState(state, turns);
  if (pending?.kind === "approval") {
    return {
      reply,
      status: "needs_approval",
      pendingAction: { name: humanToolName(pending.tool.name), arguments: pending.tool.args ?? "" },
      note: "Dana is waiting for permission. Tell the user what she wants to do; if they agree, call respond-to-dana with action 'approve' (or 'deny').",
    };
  }
  if (pending?.kind === "selection") {
    return {
      reply,
      status: "needs_selection",
      choice: pending.request,
      note: "Ask the user to choose, then call respond-to-dana with action 'select' and the 0-based option indices.",
    };
  }
  if (pending?.kind === "iterations") {
    return {
      reply: reply || pending.summary,
      status: "paused",
      note: "Dana paused after many steps. Call respond-to-dana with action 'continue' to let her go on, or 'stop'.",
    };
  }
  if (state.status === "error") return { reply, status: "error", note: state.error };
  return { reply, status: "done" };
}

export async function askDana(message: string): Promise<DanaReply> {
  const state = await untilDone((opts) => api.sendMessage(AI_SESSION, message, undefined, opts));
  return describe(state);
}

export type DanaResponse = "approve" | "deny" | "select" | "continue" | "stop";

export async function respond(action: DanaResponse, selection?: number[]): Promise<DanaReply> {
  const resumeAction =
    action === "select"
      ? { select: selection ?? [] }
      : action === "continue"
        ? "extend_iterations"
        : action === "stop"
          ? "decline_iterations"
          : action;
  const state = await untilDone((opts) => api.resume(AI_SESSION, resumeAction, opts));
  return describe(state);
}

/** What Dana is currently waiting for in the AI session (used by confirmations). */
export async function pendingInAiSession(): Promise<DanaReply | null> {
  const state = await api.getSession(AI_SESSION);
  return state ? describe(state) : null;
}
