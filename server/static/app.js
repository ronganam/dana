/* The web app (static/index.html) — starts it: opens Chat.
 *
 * The app is classic scripts sharing one global scope, no build step, one area each:
 *   app-core.js      shared state and helpers
 *   app-nav.js       the rail / bottom nav and switchTab
 *   app-stream.js    the assistant stream client (its run model is pure, tested by test_web_stream.mjs)
 *   app-thread.js    the shared conversation's memory line (pure, tested by test_web_thread.mjs)
 *   app-chat.js      the Chat screen and its chats sidebar
 *   app-voice.js     the orb, voice messages, realtime calls
 *   app-tasks.js, app-memory.js, app-calls.js,
 *   app-settings.js, app-calendar.js, app-debug.js   one screen each
 *   app.js           this file, last
 * index.html loads them in this order, `defer`, before the self-contained panel scripts (inbox.js,
 * people.js, review.js, usage.js, integrations.js, shopping-core.js, shopping.js, money-core.js, money-dash.js,
 * money.js, projects-core.js, projects.js): Chat is open and window.danaSwitchTab is set before any of those asks for its own tab
 * (`#inbox`, `/shopping`, `#money`), and
 * switchTab reaches their window.Dana* once they have loaded. While it loads, a script only uses what
 * the scripts before it declared; functions may call any of them later. A new file goes in index.html
 * and in sw.js's STATIC_ASSETS (bump its CACHE_NAME).
 */

// ══════════════════════════════════════════════════════════════════════
// INITIALIZATION
// ══════════════════════════════════════════════════════════════════════
switchTab("chat");
refreshIcons();
