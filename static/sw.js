/* Service Worker: haelt die App-Huelle offline verfuegbar.
   Registriert wird er nur in sicheren Kontexten (HTTPS oder localhost) - im
   heimischen WLAN ueber http bleibt die Seite trotzdem nutzbar, dann greift
   der localStorage-Zwischenspeicher im Frontend. */
const CACHE = 'sc-dashboard-v1';
const SHELL = [
  '/',
  '/static/js/app.js',
  '/static/css/tailwind.css',
  '/static/icons/icon-192.png',
  '/manifest.webmanifest',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Daten immer frisch versuchen; die Anzeige faellt sonst auf localStorage zurueck.
  if (url.pathname.startsWith('/api/')) return;

  event.respondWith(
    fetch(request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(request, copy)).catch(() => {});
        return response;
      })
      .catch(() => caches.match(request).then((hit) => hit || caches.match('/')))
  );
});
