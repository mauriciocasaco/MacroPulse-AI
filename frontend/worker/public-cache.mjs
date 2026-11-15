import { boundedBody } from './bounded-body.mjs';

export const publicCacheHeader = 'X-MacroPulse-Public-Cache';
const storedAtHeader = 'X-MacroPulse-Cache-Stored-At';
const version = 'official-v1';
const maxBody = 3 * 1024 * 1024;
export const catalogTTL = 60;
export const historyTTL = 300;

// Cache is an optional optimization. Neither a rejected nor a stalled operation gates origin access.
async function optional(operation) {
  let timer;
  try {
    return await Promise.race([
      Promise.resolve().then(operation),
      new Promise((resolve) => { timer = setTimeout(() => resolve(null), 250); }),
    ]);
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

export async function publicCache(request, env, base, catalog) {
  if (env.MACROPULSE_PUBLIC_CACHE_ENABLED === 'false' || request.method !== 'GET'
    || ['Authorization', 'Cookie', 'X-MacroPulse-Admin-Secret', 'X-MacroPulse-Admin-Check']
      .some((name) => request.headers.has(name))) return null;
  let cache;
  try { cache = globalThis.caches?.default; } catch { return null; }
  if (!cache) return null;
  const url = new URL(request.url);
  // Partition by origin configuration without exposing its authentication secret in a URL.
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(
    `${base.origin}\n${env.MARKETPULSE_DEMO_ORIGIN_SECRET}`));
  const partition = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  const key = new URL(`/__macropulse_cache/${version}${url.pathname}`, url.origin);
  key.searchParams.set('origin', partition);
  if (!catalog) {
    key.searchParams.set('start', url.searchParams.get('start'));
    key.searchParams.set('end', url.searchParams.get('end'));
  }
  return { cache, key: new Request(key), ttl: catalog ? catalogTTL : historyTTL, url, catalog };
}

function validBody(body, entry) {
  try {
    const value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(body));
    return entry.catalog ? Array.isArray(value)
      : value?.indicator?.id === entry.url.pathname.split('/')[4]
        && value.start === entry.url.searchParams.get('start') && value.end === entry.url.searchParams.get('end')
        && Array.isArray(value.observations);
  } catch { return false; }
}

export async function readPublicCache(entry) {
  if (!entry) return null;
  return optional(async () => {
    const response = await entry.cache.match(entry.key);
    if (!response) return null;
    const stored = Number(response.headers.get(storedAtHeader));
    if (response.status !== 200 || response.headers.get(publicCacheHeader) !== version
      || !Number.isSafeInteger(stored) || stored <= 0 || stored > Date.now()
      || Date.now() - stored >= entry.ttl * 1000) {
      void response.body?.cancel().catch(() => {});
      return null;
    }
    const body = await boundedBody(response.body, maxBody, AbortSignal.timeout(200));
    return body && validBody(body, entry) ? body : null;
  });
}

export async function writePublicCache(entry, body, headers, started) {
  if (!entry || headers.get(publicCacheHeader) !== version || headers.has('Set-Cookie')
    || headers.has('Vary') || /(?:^|,)\s*private(?:\s|,|=|$)/i.test(headers.get('Cache-Control') ?? '')
    || body.byteLength > maxBody || !validBody(body, entry)) return;
  // The origin explicitly authorizes ONLY these public payloads for internal edge caching.
  // Browser-facing responses retain no-store. Never store dynamic Research/Turnstile headers.
  const remaining = entry.ttl - Math.ceil((Date.now() - started) / 1000);
  if (remaining <= 0) return;
  await optional(() => entry.cache.put(entry.key, new Response(body, { headers: {
    'Content-Type': 'application/json', 'Cache-Control': `public, max-age=${remaining}`,
    [publicCacheHeader]: version, [storedAtHeader]: String(started),
  } })));
}
