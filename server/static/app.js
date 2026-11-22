/* The web app (static/index.html) — starts it: opens Chat, lists the user's addon pages, registers the
 * service worker.
 *
 * The shell is classic scripts sharing one global scope, no build step, one area each:
 *   dana.js          the SDK addon pages are written against (Dana.panel; tested by test_web_panels.mjs)
 *   app-core.js      shared state and helpers
 *   app-places.js    the navigation's places, pure: primary / rest / core, what fits, More's badge
 *                    (tested by test_web_places.mjs)
 *   app-nav.js       the rail / bottom bar / More sheet and Edit, switchTab, the core screens and the
 *                    core pieces pages host
 *   app-panels.js    addons' pages: the navigation from GET /api/addons, loading, mount / unmount
 *                    (its pure part is tested by test_web_panels.mjs)
 *   app-stream.js    the assistant stream client (its run model is pure, tested by test_web_stream.mjs)
 *   app-thread.js    the shared conversation's memory line (pure, tested by test_web_thread.mjs)
 *   app-chat.js      the Chat screen and its chats sidebar
 *   app-voice.js     the orb, voice messages, realtime calls
 *   app-settings.js, app-debug.js   one screen each
 *   app.js           this file, last
 * index.html loads them in this order, `defer`, before the self-contained core screens (review.js,
 * usage.js, addons.js, profile.js): Chat is open and window.danaSwitchTab is set before any of those asks
 * for its own tab (`#review`, `#addons`). While it loads, a script only uses what the scripts before it
 * declared; functions may call any of them later. Addon pages are not listed here: each addon ships its
 * own from its web/ folder (server/docs/writing-an-addon.md, "Pages"). A new shell file goes in
 * index.html and in sw.js's SHELL list.
 */

// ══════════════════════════════════════════════════════════════════════
// INITIALIZATION
// ══════════════════════════════════════════════════════════════════════
switchTab("chat");
refreshIcons();
startPanels();

// The service worker (sw.js) keeps the shell and the addon pages' files for offline use, one cache per
// release. When a new release's worker takes over a page that an older one served, the page reloads so
// the shell and the pages it loads are of one release; while someone is typing it only offers to.
if ("serviceWorker" in navigator) {
    const hadWorker = Boolean(navigator.serviceWorker.controller);
    navigator.serviceWorker.addEventListener("controllerchange", () => {
        if (!hadWorker) return;
        const typing = document.activeElement && /^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName)
            && document.activeElement.value;
        if (!typing) { location.reload(); return; }
        const toast = document.getElementById("app-toast");
        if (!toast) return;
        toast.innerHTML = `Dana was updated <button type="button">Reload</button>`;
        toast.querySelector("button").addEventListener("click", () => location.reload());
        toast.classList.remove("hidden");
    });
    navigator.serviceWorker.register("/sw.js").catch(err => console.warn("[dana] no service worker", err));
}
