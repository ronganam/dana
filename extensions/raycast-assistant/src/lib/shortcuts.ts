import { Keyboard } from "@raycast/api";

/** ⌘ on macOS, Ctrl on Windows (optionally with Shift). */
export function mod(key: Keyboard.KeyEquivalent, shift = false): Keyboard.Shortcut {
  return {
    macOS: { modifiers: shift ? ["cmd", "shift"] : ["cmd"], key },
    Windows: { modifiers: shift ? ["ctrl", "shift"] : ["ctrl"], key },
  };
}
