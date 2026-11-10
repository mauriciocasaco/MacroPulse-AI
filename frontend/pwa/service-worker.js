/* global self, version, shell, caches */
const scope = self.registration.scope;
const index = new URL('index.html', scope).href;
const cachePrefix = `marketpulse-shell:${scope}:`;
const cacheName = cachePrefix + version;
const files = new Map(shell.map(([path, integrity]) => [new URL(path, scope).href, integrity]));

self.addEventListener('install', (event) => {
  // Integrity pins the anonymous response body to this build, even if a server
  // incorrectly routes an app-shell URL to a dynamic page.
  const requests = Array.from(files, ([url, integrity]) => new Request(url, {
    cache: 'reload',
    credentials: 'omit',
    redirect: 'error',
    integrity,
  }));
  event.waitUntil(caches.open(cacheName).then((cache) => cache.addAll(requests)));
});

self.addEventListener('activate', (event) => {
  // The default waiting phase keeps each open client on a coherent release.
  event.waitUntil(caches.keys().then((names) => Promise.all(names
    .filter((name) => name.startsWith(cachePrefix) && name !== cacheName)
    .map((name) => caches.delete(name)))));
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET' || request.headers.has('authorization') || request.headers.has('range')) return;
  const navigation = request.mode === 'navigate' && (request.url === scope || request.url === index);
  if (!navigation && (!request.destination || request.url === index || !files.has(request.url))) return;

  event.respondWith(caches.match(navigation ? index : request.url, {
    cacheName,
    ignoreVary: true,
  }).then((response) => response ?? fetch(request)));
});
