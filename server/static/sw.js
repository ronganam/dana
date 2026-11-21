const CACHE_NAME = "dana-hub-v21";
// Pages ("/", "/dashboard", ...) are not pre-cached: they require sign-in, and an
// unauthenticated install would cache the login redirect in their place.
const STATIC_ASSETS = [
  "/static/styles.css",
  "/static/app-core.js",
  "/static/app-nav.js",
  "/static/app-stream.js",
  "/static/app-thread.js",
  "/static/app-chat.js",
  "/static/app-voice.js",
  "/static/app-tasks.js",
  "/static/app-memory.js",
  "/static/app-calls.js",
  "/static/app-settings.js",
  "/static/app-calendar.js",
  "/static/app-debug.js",
  "/static/app.js",
  "/static/shopping-core.js",
  "/static/shopping.js",
  "/static/shopping.css",
  "/static/money-core.js",
  "/static/money-dash.js",
  "/static/money.js",
  "/static/money.css",
  "/static/projects-core.js",
  "/static/projects.js",
  "/static/projects.css",
  "/static/favicon.png",
  "/manifest.json",
  "https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&display=swap",
  "https://fonts.gstatic.com/s/inter/v18/UcCO3FwrK3iLTeHuS_fvQtMwCp50KnMw2boKoduKmMEVuLyfAZJhjp-Ek-_eeAmF.woff2",
  "/static/vendor/lucide.min.js",
  "/static/vendor/marked.min.js",
  "/static/vendor/purify.min.js"
];

// Install Event
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log("[Service Worker] Pre-caching static assets");
      return cache.addAll(STATIC_ASSETS);
    }).then(() => {
      return self.skipWaiting();
    })
  );
});

// Activate Event
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cache) => {
          if (cache !== CACHE_NAME) {
            console.log("[Service Worker] Removing old cache", cache);
            return caches.delete(cache);
          }
        })
      );
    }).then(() => {
      return self.clients.claim();
    })
  );
});

// Fetch Event
self.addEventListener("fetch", (event) => {
  const requestUrl = new URL(event.request.url);

  // Only successful, non-redirected responses are cached, so a 401 or a
  // redirect to /login never replaces good content.
  const cacheable = (response) => response && response.ok && !response.redirected;

  // 1. Pages: network-first so sign-in redirects and new deploys apply
  // immediately; fall back to the last good copy when offline.
  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (cacheable(response)) {
            const responseClone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, responseClone));
          }
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // Live event streams must reach the network untouched — never cached.
  if (requestUrl.pathname.endsWith("/stream") || requestUrl.pathname.endsWith("/voice_stream")) {
    return;
  }

  // The shopping list is live (static/shopping.js): always the server's answer, never a cached copy.
  if (requestUrl.pathname === "/api/shopping" || requestUrl.pathname.startsWith("/api/shopping/")) {
    return;
  }

  // Money (static/money.js) is live and private: always the server's answer, never a cached copy of
  // someone's transactions on the device.
  if (requestUrl.pathname.startsWith("/api/money/")) {
    return;
  }

  // Who is signed in and their addons (static/addons.js): always the server's current
  // answer, never a cached copy (a stale "connected" would be a false claim).
  if (requestUrl.pathname === "/api/me" || requestUrl.pathname === "/api/users"
      || requestUrl.pathname.startsWith("/api/addons")) {
    return;
  }

  // 2. Handle API requests (Network-first, fallback to cache, don't cache non-GET requests)
  if (requestUrl.pathname.startsWith("/api/")) {
    if (event.request.method !== "GET") {
      // Don't cache POST, PUT, DELETE. Just pass through
      event.respondWith(fetch(event.request));
      return;
    }

    event.respondWith(
      fetch(event.request)
        .then((response) => {
          // Put a copy of the response in cache
          if (cacheable(response)) {
            const responseClone = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, responseClone);
            });
          }
          return response;
        })
        .catch(() => {
          // If offline/error, return cached copy
          return caches.match(event.request);
        })
    );
    return;
  }

  // 3. Handle static assets (Cache-first, fallback to network)
  event.respondWith(
    caches.match(event.request).then((cachedResponse) => {
      if (cachedResponse) {
        return cachedResponse;
      }

      return fetch(event.request).then((response) => {
        // Cache newly requested assets
        if (cacheable(response) && response.type === "basic") {
          const responseClone = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseClone);
          });
        }
        return response;
      });
    })
  );
});
