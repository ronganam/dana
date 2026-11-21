import { Color, Icon, Image } from "@raycast/api";
import type { Priority, Task } from "./types";

/** Server timestamps from SQLite CURRENT_TIMESTAMP are UTC without a zone marker. */
export function parseServerTime(value: string | null | undefined): Date | null {
  if (!value) return null;
  let s = value.trim();
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(s)) s = `${s.replace(" ", "T")}Z`;
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Due dates are local wall-clock times ("YYYY-MM-DDTHH:mm" or a plain date). */
export function parseDueDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const s = value.trim();
  const d = /^\d{4}-\d{2}-\d{2}$/.test(s) ? new Date(`${s}T00:00`) : new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** Format a Date the way the server and web UI store due dates. */
export function toDueDateString(date: Date | null | undefined): string | null {
  if (!date) return null;
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const dayDiff = (a: Date, b: Date) => Math.round((startOfDay(a).getTime() - startOfDay(b).getTime()) / 86_400_000);

export function relativeTime(date: Date | null): string {
  if (!date) return "";
  const mins = Math.round((Date.now() - date.getTime()) / 60_000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d`;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export type DueBucket = "overdue" | "today" | "tomorrow" | "upcoming" | "someday";

export function dueBucket(task: Task, now = new Date()): DueBucket {
  const due = parseDueDate(task.due_date);
  if (!due) return "someday";
  if (due.getTime() < now.getTime()) return "overdue";
  const diff = dayDiff(due, now);
  if (diff === 0) return "today";
  if (diff === 1) return "tomorrow";
  return "upcoming";
}

export function formatDue(value: string | null): string {
  const due = parseDueDate(value);
  if (!due) return "";
  const now = new Date();
  const diff = dayDiff(due, now);
  const hasTime = due.getHours() !== 0 || due.getMinutes() !== 0;
  const time = hasTime ? due.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) : "";
  let day: string;
  if (diff === 0) day = "Today";
  else if (diff === 1) day = "Tomorrow";
  else if (diff === -1) day = "Yesterday";
  else if (diff > 1 && diff < 7) day = due.toLocaleDateString(undefined, { weekday: "long" });
  else
    day = due.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      year: due.getFullYear() === now.getFullYear() ? undefined : "numeric",
    });
  return time ? `${day} ${time}` : day;
}

export const PRIORITY: Record<Priority, { title: string; icon: Image.ImageLike; color: Color }> = {
  high: { title: "High", icon: { source: Icon.Exclamationmark3, tintColor: Color.Red }, color: Color.Red },
  medium: { title: "Medium", icon: { source: Icon.Exclamationmark2, tintColor: Color.Orange }, color: Color.Orange },
  low: { title: "Low", icon: { source: Icon.Exclamationmark, tintColor: Color.Blue }, color: Color.Blue },
};

export interface TaskTriggers {
  time?: string;
  location?: { location: string; event?: string };
  car?: string;
  wake?: boolean;
}

export function parseTriggers(task: Task): TaskTriggers {
  if (!task.triggers) return {};
  try {
    const t = JSON.parse(task.triggers);
    return t && typeof t === "object" ? t : {};
  } catch {
    return {};
  }
}

/** Human description of a task's triggers, e.g. "When I wake up". */
export function describeTriggers(task: Task): { icon: Icon; text: string }[] {
  const t = parseTriggers(task);
  const out: { icon: Icon; text: string }[] = [];
  if (t.time) {
    const d = new Date(t.time);
    if (!Number.isNaN(d.getTime())) out.push({ icon: Icon.Alarm, text: `Remind ${formatDue(toDueDateString(d))}` });
  }
  if (t.location?.location) {
    const verb = t.location.event === "leave" ? "Leaving" : "Arriving at";
    out.push({ icon: Icon.Pin, text: `${verb} ${t.location.location}` });
  }
  if (t.car) out.push({ icon: Icon.Car, text: t.car === "leave" ? "Getting out of the car" : "Getting in the car" });
  if (t.wake) out.push({ icon: Icon.Sun, text: "When I wake up" });
  return out;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
