/* The Calls page's pure part (globalThis.DanaCallsCore): what the page shows from GET /api/calls. No DOM;
 * tested by addons/phone/tests/test_web_calls.mjs.
 */
(() => {
  "use strict";
  // The named hues of the web tokens (styles.css), ember left out: it is the accent.
  const HUES = ["rose", "sky", "sage", "plum", "amber", "teal"];

  /** The other side of a call: the receiver when the caller is "Me", else the caller ("Unknown" for none). */
  function contactOf(call) {
    return call.caller === "Me" ? (call.receiver || "Unknown") : (call.caller || "Unknown");
  }

  /** A steady hue name for a name (the same name, the same hue): the avatar is var(--<hue>-tint) / -text. */
  function avatarColor(name) {
    let hash = 0;
    for (let i = 0; i < (name || "").length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
    return HUES[Math.abs(hash) % HUES.length];
  }

  /** The calls whose caller, receiver, title or summary contain `q` (any case); all of them for none. */
  function filterCalls(calls, q) {
    const query = String(q || "").toLowerCase().trim();
    if (!query) return [...(calls || [])];
    return (calls || []).filter((c) => [c.caller, c.receiver, c.title, c.summary].some((v) => (v || "").toLowerCase().includes(query)));
  }

  /** Seconds as m:ss ("0:00" for none). */
  function duration(sec) {
    if (!sec || isNaN(sec)) return "0:00";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s < 10 ? "0" : ""}${s}`;
  }

  /** Whether text is mostly right-to-left (Hebrew / Arabic letters in it). */
  const isRtl = (text) => /[֐-׿؀-ۿיִ-﷿ﹰ-﻿]/.test(String(text || ""));

  globalThis.DanaCallsCore = { contactOf, avatarColor, filterCalls, duration, isRtl };
})();
