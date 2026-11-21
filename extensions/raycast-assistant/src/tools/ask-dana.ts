import { askDana } from "../lib/ai";

type Input = {
  /**
   * The request for Dana, in the user's own words (any language). Include all context she needs,
   * e.g. "Turn off the lights in the living room" or "What's on my calendar tomorrow?".
   */
  message: string;
};

/**
 * Ask Dana, the user's personal assistant. She has access to the user's smart home, calendar,
 * WhatsApp, phone calls and notifications, memories and notes. Returns her reply. If she needs
 * permission for an action or wants the user to choose, `status` says so and `note` explains what to do next.
 */
export default async function tool(input: Input) {
  return askDana(input.message);
}
