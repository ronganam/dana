import { Action, ActionPanel, Color, Icon, Keyboard, List, showToast, Toast } from "@raycast/api";
import { useCachedPromise } from "@raycast/utils";
import { useState } from "react";
import * as api from "./lib/api";
import { errorMessage } from "./lib/format";
import { mod } from "./lib/shortcuts";
import type { ShoppingChange, ShoppingItem, ShoppingScreen } from "./lib/types";

/** "Milk (2) — 3%": an item as one line, for the copied list. */
function line(item: ShoppingItem): string {
  return `${item.name}${item.quantity ? ` (${item.quantity})` : ""}${item.note ? ` — ${item.note}` : ""}`;
}

export default function ShoppingList() {
  const [searchText, setSearchText] = useState("");
  const { data, isLoading, mutate, revalidate } = useCachedPromise(api.getShopping, [], {
    keepPreviousData: true,
    failureToastOptions: { title: "Couldn't load the shopping list" },
  });

  const items = data?.items ?? [];
  const categories = data?.categories ?? [];
  const canEdit = data ? data.list.access !== "read" : true;
  const query = searchText.trim().toLowerCase();
  const matches = (i: ShoppingItem) =>
    !query || i.name.toLowerCase().includes(query) || i.note.toLowerCase().includes(query);
  const toBuy = items.filter((i) => !i.checked && matches(i));
  const bought = items.filter((i) => i.checked && matches(i));
  const exact = items.some((i) => !i.checked && i.name.trim().toLowerCase() === query);
  const sections = categories
    .map((c) => ({ ...c, items: toBuy.filter((i) => i.category === c.key) }))
    .filter((s) => s.items.length > 0);
  const shared = data && data.list.access !== "owner" ? `${data.list.owner.name}'s list` : undefined;

  /** Run one change: optimistic list, the server's screen after, and Undo on the toast. */
  async function run(
    title: string,
    change: () => Promise<ShoppingChange>,
    optimistic?: (screen: ShoppingScreen) => ShoppingScreen,
  ) {
    const toast = await showToast({ style: Toast.Style.Animated, title });
    try {
      const result = await mutate(
        change(),
        optimistic
          ? { optimisticUpdate: (screen) => (screen ? optimistic(screen) : screen), shouldRevalidateAfter: false }
          : { shouldRevalidateAfter: false },
      );
      toast.style = Toast.Style.Success;
      const entry = result?.undo;
      if (entry) {
        toast.title = entry.label;
        toast.primaryAction = {
          title: "Undo",
          shortcut: mod("z"),
          onAction: async (t) => {
            t.hide();
            await undoChange(entry.id);
          },
        };
      }
    } catch (error) {
      toast.style = Toast.Style.Failure;
      toast.title = "Something went wrong";
      toast.message = errorMessage(error);
      revalidate();
    }
  }

  async function undoChange(id: string) {
    const toast = await showToast({ style: Toast.Style.Animated, title: "Undoing…" });
    try {
      const result = await api.undo(id);
      toast.style = Toast.Style.Success;
      toast.title = `Undone: ${result.label}`;
    } catch (error) {
      toast.style = Toast.Style.Failure;
      toast.title = "Couldn't undo";
      toast.message = errorMessage(error);
    }
    revalidate();
  }

  const add = async (text: string) => {
    if (!text.trim()) return;
    setSearchText("");
    await run("Adding…", () => api.addShopping({ text }));
  };

  const setItem = (item: ShoppingItem, checked: boolean) =>
    run(
      checked ? `Bought ${item.name}` : `${item.name} back on the list`,
      () => api.updateShoppingItem(item.id, { checked }),
      (s) => ({ ...s, items: s.items.map((i) => (i.id === item.id ? { ...i, checked } : i)) }),
    );

  const listActions = (
    <>
      {canEdit && searchText.trim() && !exact && (
        <Action title={`Add “${searchText.trim()}”`} icon={Icon.Plus} onAction={() => add(searchText)} />
      )}
      <ActionPanel.Section>
        {canEdit && items.some((i) => i.checked) && (
          <Action
            title="Clear Bought Items"
            icon={Icon.Eraser}
            style={Action.Style.Destructive}
            shortcut={Keyboard.Shortcut.Common.RemoveAll}
            onAction={() =>
              run(
                "Clearing bought items…",
                () => api.clearBoughtShopping(),
                (s) => ({ ...s, items: s.items.filter((i) => !i.checked) }),
              )
            }
          />
        )}
        <Action.CopyToClipboard
          title="Copy List"
          content={items
            .filter((i) => !i.checked)
            .map((i) => `• ${line(i)}`)
            .join("\n")}
          shortcut={Keyboard.Shortcut.Common.Copy}
        />
        <Action
          title="Refresh"
          icon={Icon.ArrowClockwise}
          shortcut={Keyboard.Shortcut.Common.Refresh}
          onAction={revalidate}
        />
      </ActionPanel.Section>
    </>
  );

  const renderItem = (item: ShoppingItem) => (
    <List.Item
      key={item.id}
      title={item.name}
      subtitle={item.note || undefined}
      icon={item.checked ? { source: Icon.CheckCircle, tintColor: Color.Green } : Icon.Circle}
      accessories={[
        ...(item.quantity ? [{ tag: item.quantity, tooltip: "Quantity" }] : []),
        ...(shared && item.added_by ? [{ text: item.added_by.name, tooltip: "Added by" }] : []),
      ]}
      actions={
        <ActionPanel>
          {canEdit && searchText.trim() && !exact && (
            <Action title={`Add “${searchText.trim()}”`} icon={Icon.Plus} onAction={() => add(searchText)} />
          )}
          {canEdit && (
            <Action
              title={item.checked ? "Back on the List" : "Mark Bought"}
              icon={item.checked ? Icon.Circle : Icon.CheckCircle}
              onAction={() => setItem(item, !item.checked)}
            />
          )}
          {canEdit && (
            <Action
              title="Remove Item"
              icon={Icon.Trash}
              style={Action.Style.Destructive}
              shortcut={Keyboard.Shortcut.Common.Remove}
              onAction={() =>
                run(
                  `Removing ${item.name}…`,
                  () => api.removeShoppingItem(item.id),
                  (s) => ({ ...s, items: s.items.filter((i) => i.id !== item.id) }),
                )
              }
            />
          )}
          {listActions}
        </ActionPanel>
      }
    />
  );

  return (
    <List
      isLoading={isLoading}
      filtering={false}
      searchText={searchText}
      onSearchTextChange={setSearchText}
      navigationTitle={shared ? `Shopping List — ${shared}` : "Shopping List"}
      searchBarPlaceholder={canEdit ? "Search or add items (2 milk, bread)…" : "Search the list (read-only)…"}
    >
      <List.EmptyView
        icon={canEdit && searchText.trim() ? Icon.Plus : Icon.Cart}
        title={canEdit && searchText.trim() ? `Add “${searchText.trim()}”` : "The shopping list is empty"}
        description={
          canEdit ? (searchText.trim() ? "Press ↵ to add it." : "Type items and press ↵ to add them.") : undefined
        }
        actions={<ActionPanel>{listActions}</ActionPanel>}
      />
      {sections.map((s) => (
        <List.Section key={s.key} title={s.name} subtitle={String(s.items.length)}>
          {s.items.map(renderItem)}
        </List.Section>
      ))}
      {bought.length > 0 && (
        <List.Section title="Bought" subtitle={String(bought.length)}>
          {bought.map(renderItem)}
        </List.Section>
      )}
    </List>
  );
}
