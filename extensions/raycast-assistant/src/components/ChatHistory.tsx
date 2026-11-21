import { Action, ActionPanel, Alert, Color, confirmAlert, Icon, Keyboard, List, showToast, Toast } from "@raycast/api";
import { useCachedPromise } from "@raycast/utils";
import * as api from "../lib/api";
import { errorMessage, parseServerTime, relativeTime } from "../lib/format";
import type { SessionSummary } from "../lib/types";
import { Chat } from "./Chat";

const STATUS: Record<string, { icon: Icon; color: Color; tooltip: string }> = {
  running: { icon: Icon.CircleProgress, color: Color.Blue, tooltip: "Working" },
  waiting_approval: { icon: Icon.Shield, color: Color.Orange, tooltip: "Waiting for approval" },
  awaiting_selection: { icon: Icon.QuestionMarkCircle, color: Color.Blue, tooltip: "Waiting for your choice" },
  iterations_exceeded: { icon: Icon.Pause, color: Color.Yellow, tooltip: "Paused" },
  error: { icon: Icon.Warning, color: Color.Red, tooltip: "Error" },
};

function section(date: Date | null): string {
  if (!date) return "Older";
  const days = (Date.now() - date.getTime()) / 86_400_000;
  const today = new Date();
  if (date.toDateString() === today.toDateString()) return "Today";
  if (days < 7) return "Previous 7 Days";
  if (days < 30) return "Previous 30 Days";
  return "Older";
}

const SECTION_ORDER = ["Today", "Previous 7 Days", "Previous 30 Days", "Older"];

/**
 * Every conversation with Dana (from Raycast, the web, the phone and Telegram).
 * With `onSelect`, choosing a chat hands it back to the caller instead of opening it.
 */
export function ChatHistory({
  currentSessionId,
  onSelect,
}: {
  currentSessionId?: string;
  onSelect?: (sessionId: string) => void;
}) {
  const { data, isLoading, mutate, revalidate } = useCachedPromise(api.getSessions, [], {
    keepPreviousData: true,
    failureToastOptions: { title: "Couldn't load chats" },
  });

  const grouped = new Map<string, SessionSummary[]>();
  for (const s of data ?? []) {
    const key = section(parseServerTime(s.updated_at));
    grouped.set(key, [...(grouped.get(key) ?? []), s]);
  }

  async function remove(s: SessionSummary) {
    const ok = await confirmAlert({
      title: `Delete “${s.title}”?`,
      message: "The conversation is removed from Dana for every device.",
      icon: { source: Icon.Trash, tintColor: Color.Red },
      primaryAction: { title: "Delete", style: Alert.ActionStyle.Destructive },
    });
    if (!ok) return;
    const toast = await showToast({ style: Toast.Style.Animated, title: "Deleting chat" });
    try {
      await mutate(api.deleteSession(s.session_id), {
        optimisticUpdate: (list) => list?.filter((x) => x.session_id !== s.session_id),
      });
      toast.style = Toast.Style.Success;
      toast.title = "Chat deleted";
    } catch (error) {
      toast.style = Toast.Style.Failure;
      toast.title = "Couldn't delete chat";
      toast.message = errorMessage(error);
    }
  }

  return (
    <List isLoading={isLoading} searchBarPlaceholder="Search chats…">
      <List.EmptyView icon={Icon.Bubble} title="No chats yet" description="Start one with Ask Dana." />
      {SECTION_ORDER.filter((k) => grouped.has(k)).map((key) => (
        <List.Section key={key} title={key}>
          {grouped.get(key)!.map((s) => {
            const status = STATUS[s.status];
            const accessories: List.Item.Accessory[] = [];
            if (s.session_id === currentSessionId) accessories.push({ tag: { value: "Current", color: Color.Green } });
            if (status)
              accessories.push({ icon: { source: status.icon, tintColor: status.color }, tooltip: status.tooltip });
            const updated = parseServerTime(s.updated_at);
            accessories.push({ text: relativeTime(updated), tooltip: updated?.toLocaleString() });
            return (
              <List.Item
                key={s.session_id}
                icon={Icon.Bubble}
                title={s.title}
                subtitle={s.snippet !== s.title ? s.snippet : undefined}
                keywords={[s.snippet]}
                accessories={accessories}
                actions={
                  <ActionPanel>
                    {onSelect ? (
                      <Action title="Open Chat" icon={Icon.ArrowRight} onAction={() => onSelect(s.session_id)} />
                    ) : (
                      <Action.Push
                        title="Open Chat"
                        icon={Icon.ArrowRight}
                        target={<Chat sessionId={s.session_id} title={s.title} />}
                        onPop={revalidate}
                      />
                    )}
                    <Action.CopyToClipboard title="Copy Chat ID" content={s.session_id} />
                    <Action
                      title="Refresh"
                      icon={Icon.ArrowClockwise}
                      shortcut={Keyboard.Shortcut.Common.Refresh}
                      onAction={revalidate}
                    />
                    <Action
                      title="Delete Chat"
                      icon={Icon.Trash}
                      style={Action.Style.Destructive}
                      shortcut={Keyboard.Shortcut.Common.Remove}
                      onAction={() => remove(s)}
                    />
                  </ActionPanel>
                }
              />
            );
          })}
        </List.Section>
      ))}
    </List>
  );
}
