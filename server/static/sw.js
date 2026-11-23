/* The web app's service worker (registered by static/app.js; served by core/pages_api.py, which puts the
 * release in RELEASE: a digest of the shell's files and of every loaded addon's web/ files, so any change
 * to either is a new worker with a new cache).
 *
 * Kept, one cache per release: the shell (SHELL, fetched fresh when a release installs) and each addon
 * page's files under /addons/<name>/web/ (cached when first loaded). The page itself is network-first,
 * falling back to the last good copy offline. Everything else (the API, live streams, other pages) always
 * goes to the network: nobody's data is kept on the device. A new release's worker takes over at once and
 * deletes the older caches; the page then reloads onto it (app.js).
 */
const RELEASE = "__DANA_RELEASE__";
const CACHE_NAME = `dana-${RELEASE}`;
// Pages ("/", "/shopping") are not pre-cached: they require sign-in, and an unauthenticated install would
// cache the login redirect in their place.
const SHELL = [
  "/static/styles.css",
  "/static/kit.css",
  "/static/review.css",
  "/static/usage.css",
  "/static/attention.css",
  "/static/addons.css",
  "/static/devices.css",
  "/static/household.css",
  "/static/zones.css",
  "/static/llm.css",
  "/static/backups.css",
  "/static/dana.js",
  "/static/app-core.js",
  "/static/app-places.js",
  "/static/app-nav.js",
  "/static/app-panels.js",
  "/static/app-stream.js",
  "/static/app-thread.js",
  "/static/app-chat.js",
  "/static/app-voice.js",
  "/static/app-settings.js",
  "/static/app-debug.js",
  "/static/app-now-core.js",
  "/static/app-now.js",
  "/static/app.js",
  "/static/review.js",
  "/static/usage.js",
  "/static/attention.js",
  "/static/addons.js",
  "/static/profile.js",
  "/static/devices.js",
  "/static/dashboard-editor.js",
  "/static/household.js",
  "/static/demo.js",
  "/static/zones.js",
  "/static/llm.js",
  "/static/backups.js",
  "/static/favicon.png",
  "/static/icons/icon-192x192.png",
  "/static/vendor/lucide.min.js",
  "/static/vendor/marked.min.js",
  "/static/vendor/purify.min.js",
  "/static/vendor/qrcode.js",
  "/manifest.json"
];

// Only successful, non-redirected answers are kept, so a 401 or a redirect to /login never replaces good content.
const cacheable = (response) => response && response.ok && !response.redirected && response.type === "basic";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(SHELL.map((url) => new Request(url, { cache: "reload" }))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(names.filter((name) => name !== CACHE_NAME).map((name) => caches.delete(name))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // The page: network-first so sign-in redirects and new releases apply at once; the last good copy offline.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (cacheable(response)) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => caches.match(request))
    );
    return;
  }

  // The shell and the addon pages' files: from this release's cache, else the network (then kept).
  const shell = SHELL.includes(url.pathname);
  const addonPage = /^\/addons\/[a-z0-9_]+\/web\//.test(url.pathname);
  if (!shell && !addonPage) return;
  event.respondWith(
    caches.open(CACHE_NAME).then((cache) =>
      cache.match(request).then((hit) => hit || fetch(request).then((response) => {
        if (cacheable(response)) cache.put(request, response.clone());
        return response;
      })))
  );
});
