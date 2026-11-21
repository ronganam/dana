import { getShopping } from "../lib/api";

type Input = {
  /**
   * Include items already bought. Defaults to false.
   */
  includeBought?: boolean;
};

/**
 * List the items on the user's shopping list, each with its quantity, note and supermarket aisle.
 */
export default async function tool(input: Input) {
  const screen = await getShopping();
  const aisles = new Map(screen.categories.map((c) => [c.key, c.name]));
  return {
    list: screen.list.access === "owner" ? "your own" : `shared by ${screen.list.owner.name}`,
    items: screen.items
      .filter((i) => input.includeBought || !i.checked)
      .map((i) => ({
        name: i.name,
        quantity: i.quantity || undefined,
        note: i.note || undefined,
        aisle: aisles.get(i.category) ?? i.category,
        bought: i.checked,
      })),
  };
}
