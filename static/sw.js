const CACHE_NAME = 'ziwei-v3';
const STATIC_ASSETS = [
  'https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@400;600;700&family=Noto+Sans+TC:wght@300;400;500;700&display=swap',
  '/static/marked.min.js',
  'https://cdn.jsdelivr.net/npm/dompurify@3.2.3/dist/purify.min.js',
];

// Install: pre-cache CDN assets only (not HTML pages)
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache =>
      Promise.allSettled(STATIC_ASSETS.map(url => cache.add(url).catch(() => {})))
    ).then(() => self.skipWaiting())
  );
});

// Activate: clean old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Push: show daily fortune notification
self.addEventListener('push', event => {
  let data = { title: '紫微AI今日運勢', body: '點擊查看你的今日運勢 ✨', url: '/chat' };
  try { data = Object.assign(data, event.data.json()); } catch {}
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/static/icon-192.png',
      badge: '/static/icon-192.png',
      data: { url: data.url },
      vibrate: [200, 100, 200],
    })
  );
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/chat';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      for (const c of list) {
        if (c.url.includes('/chat') && 'focus' in c) return c.focus();
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});

// Fetch: network-first for API and HTML pages; cache-first for CDN static assets
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Always network-first for API calls and HTML navigation
  if (
    url.pathname.startsWith('/api/') ||
    url.pathname === '/linebot' ||
    event.request.mode === 'navigate'
  ) {
    event.respondWith(fetch(event.request).catch(() => caches.match('/chat') || new Response('Offline', { status: 503 })));
    return;
  }

  // Cache-first for CDN static assets
  event.respondWith(
    caches.match(event.request).then(cached => {
      if (cached) return cached;
      return fetch(event.request).then(response => {
        if (!response || response.status !== 200 || response.type === 'opaque') return response;
        const clone = response.clone();
        caches.open(CACHE_NAME).then(c => c.put(event.request, clone));
        return response;
      });
    })
  );
});
