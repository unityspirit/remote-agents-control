const CACHE_NAME = 'global-spirit-group-pwa-v24.0';
const APP_SHELL = [
  './manifest.webmanifest',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/maskable-512.png',
  './icons/apple-touch-icon.png',
  './icons/favicon-64.png'
];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)).catch(() => {})
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
  if (event.data && event.data.type === 'PURGE_CACHE') {
    caches.keys().then((keys) => Promise.all(keys.map((k) => caches.delete(k))));
  }
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  // NEVER cache HTML or API calls - ALWAYS fetch fresh from network
  if (
    req.mode === 'navigate' ||
    url.pathname.endsWith('.html') ||
    url.pathname.endsWith('/') ||
    url.origin !== self.location.origin ||
    url.hostname === '127.0.0.1' ||
    url.hostname === 'localhost' ||
    url.hostname === 'api.github.com' ||
    url.hostname === 'raw.githubusercontent.com' ||
    url.hostname === 'api.telegram.org'
  ) {
    return; // Let browser fetch directly from network without caching
  }

  // Stale-while-revalidate for static icons & manifest
  event.respondWith(
    caches.match(req).then((cached) => {
      const net = fetch(req)
        .then((res) => {
          const clone = res.clone();
          caches.open(CACHE_NAME).then((c) => c.put(req, clone)).catch(() => {});
          return res;
        })
        .catch(() => cached);
      return cached || net;
    })
  );
});
