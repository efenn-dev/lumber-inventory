/* Service worker for Fencerow Field.
 *
 * Strategy: precache the field shell (the one page that must work at the mill
 * with zero signal), serve it cache-first, and pass every other request
 * straight to the network -- the rest of the app is server-rendered and must
 * never be served stale.
 *
 * Bump CACHE_VERSION whenever the shell files change; activation deletes the
 * old cache, so the next online visit picks up the new shell.
 */
const CACHE_VERSION = "fencerow-field-v5";

const SHELL = [
  "/field",
  "/static/style.css",
  "/static/field.js",
  "/static/logcalc.js",
  "/manifest.webmanifest",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return; // never intercept other hosts
  const isShell = SHELL.includes(url.pathname);
  if (!isShell) return; // dynamic pages and APIs go straight to the network

  // Cache-first with background refresh: instant offline, quietly current online.
  event.respondWith(
    caches.match(event.request).then((cached) => {
      const refresh = fetch(event.request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE_VERSION).then((cache) => cache.put(event.request, copy));
          }
          return response;
        })
        .catch(() => cached);
      return cached || refresh;
    })
  );
});
