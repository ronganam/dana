import { Tool } from "@raycast/api";
import { addShopping } from "../lib/api";

type Item = {
  /**
   * The product as the user said it, without the amount (e.g. "milk"). Keep the user's language.
   */
  name: string;
  /**
   * How much, when said: "2", "1 kg".
   */
  quantity?: string;
  /**
   * A detail, when said: "3%", "lactose-free".
   */
  note?: string;
};

type Input = {
  /**
   * The items to add, one entry per product, e.g. [{ "name": "milk", "quantity": "2", "note": "3%" }, { "name": "eggs" }].
   */
  items: Item[];
};

/**
 * Add items to the user's shopping list. A product already on the list is not added twice.
 */
export default async function tool(input: Input) {
  const items = input.items
    .map((i) => ({ name: i.name.trim(), quantity: i.quantity?.trim() || "", note: i.note?.trim() || "" }))
    .filter((i) => i.name);
  const result = await addShopping({ items });
  return {
    results: result.results.map((r) => ({ name: r.name, status: r.status })),
    toBuy: result.counts.to_buy,
  };
}

const describe = (i: Item) => `${i.name}${i.quantity ? ` (${i.quantity})` : ""}${i.note ? ` — ${i.note}` : ""}`;

export const confirmation: Tool.Confirmation<Input> = async (input) => ({
  message:
    input.items.length === 1
      ? `Add “${describe(input.items[0])}” to the shopping list?`
      : "Add these to the shopping list?",
  info:
    input.items.length > 1 ? input.items.map((item, i) => ({ name: `${i + 1}`, value: describe(item) })) : undefined,
});
