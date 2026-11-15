import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import worker from './public-demo.mjs';
import { catalogTTL, historyTTL, publicCacheHeader } from './public-cache.mjs';

const host = 'https://demo.example';
const catalog = '/api/macro/indicators';
const history = catalog + '/eur-usd/history?start=2020-01-01&end=2020-01-31';
const digest = (value) => createHash('sha256').update(value).digest('hex');
const allowed = { limit: async () => ({ success: true }) };
const env = {
  MARKETPULSE_DEMO_ORIGIN_URL: 'https://fixture.onrender.com',
  MARKETPULSE_DEMO_ORIGIN_SECRET: digest('synthetic cache origin'),
  MARKETPULSE_DEMO_MACRO_ENABLED: 'true',
  CATALOG_RATE_LIMIT: allowed, HISTORY_RATE_LIMIT: allowed,
};
const optIn = { [publicCacheHeader]: 'official-v1', 'Cache-Control': 'no-store' };
function request(path = history, headers = {}, method = 'GET') {
  return new Request(host + path, { method, headers: { 'CF-Connecting-IP': '192.0.2.1', ...headers } });
}
function historyBody(target) {
  const url = new URL(target);
  return { indicator: { id: url.pathname.split('/').at(-2), attribution: 'Synthetic official fixture' },
    start: url.searchParams.get('start'), end: url.searchParams.get('end'),
    updated_at: '2026-11-14T00:00:00Z', observations: [] };
}
function cacheFixture(t) {
  const entries = new Map();
  const cache = {
    // Intentionally ignore Cache-Control expiry to exercise the Worker's independent age check.
    match: t.mock.fn(async (key) => entries.get(key.url)?.clone()),
    put: t.mock.fn(async (key, response) => { entries.set(key.url, response.clone()); }),
  };
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'caches');
  Object.defineProperty(globalThis, 'caches', { configurable: true, value: { default: cache } });
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, 'caches', previous);
    else delete globalThis.caches;
  });
  return { entries, cache };
}

test('history cache hit preserves source payload, bounds freshness and never serves stale on error', async (t) => {
  const { entries } = cacheFixture(t);
  let now = Date.UTC(2026, 10, 14);
  t.mock.method(Date, 'now', () => now);
  let fail = false;
  const origin = t.mock.method(globalThis, 'fetch', async (target) => fail
    ? Response.json({}, { status: 503 }) : Response.json(historyBody(target), { headers: optIn }));
  const first = await worker.fetch(request(), env);
  const body = await first.json();
  now += historyTTL * 1000 - 1;
  const hit = await worker.fetch(request(), env);
  assert.deepEqual(await hit.json(), body);
  assert.equal(hit.headers.get('Cache-Control'), 'no-store');
  assert.equal(hit.headers.get(publicCacheHeader), null);
  assert.equal(origin.mock.callCount(), 1);
  assert.equal(entries.size, 1);
  now++;
  assert.equal((await worker.fetch(request(), env)).status, 200);
  assert.equal(origin.mock.callCount(), 2);
  now += historyTTL * 1000;
  fail = true;
  assert.equal((await worker.fetch(request(), env)).status, 503);
  assert.equal(origin.mock.callCount(), 3);
});

test('cache keys canonicalize dates and isolate endpoint, window, public host and origin configuration', async (t) => {
  const { entries } = cacheFixture(t);
  const origin = t.mock.method(globalThis, 'fetch', async (target) => Response.json(historyBody(target), { headers: optIn }));
  await worker.fetch(request(), env);
  await worker.fetch(request(catalog + '/eur-usd/history?end=2020-01-31&start=2020-01-01'), env);
  assert.equal(origin.mock.callCount(), 1);
  await worker.fetch(request(history.replace('eur-usd', 'm3-stock')), env);
  await worker.fetch(request(history.replace('2020-01-31', '2020-02-01')), env);
  await worker.fetch(request(), { ...env, MARKETPULSE_DEMO_ORIGIN_URL: 'https://other.onrender.com' });
  await worker.fetch(request(), { ...env, MARKETPULSE_DEMO_ORIGIN_SECRET: digest('rotated fixture') });
  await worker.fetch(new Request('https://other.example' + history, { headers: { 'CF-Connecting-IP': '192.0.2.1' } }), env);
  assert.equal(entries.size, 6);
  assert.equal(origin.mock.callCount(), 6);
  for (const key of entries.keys()) {
    assert.ok(!key.includes(env.MARKETPULSE_DEMO_ORIGIN_SECRET));
    assert.ok(!key.includes('192.0.2.1'));
  }
});

test('warm entries do not bypass origin, route, parameter or IP rate validation', async (t) => {
  const { cache } = cacheFixture(t);
  const origin = t.mock.method(globalThis, 'fetch', async (target) => Response.json(historyBody(target), { headers: optIn }));
  await worker.fetch(request(), env);
  for (const [req, config, status] of [
    [request(history, { Origin: 'https://foreign.example' }), env, 403],
    [request(history + '&extra=1'), env, 422],
    [request(history + '&start=2020-01-01'), env, 422],
    [request(history.replace('eur-usd', 'private')), env, 404],
    [request(history, {}, 'POST'), env, 405],
    [request(history, { 'CF-Connecting-IP': '' }), env, 503],
    [request(), { ...env, HISTORY_RATE_LIMIT: { limit: async () => ({ success: false }) } }, 429],
  ]) assert.equal((await worker.fetch(req, config)).status, status);
  assert.equal(cache.match.mock.callCount(), 1);
  assert.equal(origin.mock.callCount(), 1);
});

test('catalog caches only its body and refreshes upstream and Worker Research settings on every hit', async (t) => {
  const { entries } = cacheFixture(t);
  let now = Date.UTC(2026, 10, 14);
  t.mock.method(Date, 'now', () => now);
  let upstreamEnabled = true;
  let ready = true;
  const config = { ...env, MACROPULSE_RESEARCH_ENABLED: 'true', MACROPULSE_RESEARCH_HOSTNAME: 'demo.example',
    MACROPULSE_TURNSTILE_SITE_KEY: 'synthetic-site-key', MACROPULSE_TURNSTILE_SECRET: 'synthetic-turnstile-secret',
    MACROPULSE_RESEARCH_HASH_KEY: digest('synthetic hash'),
    RESEARCH_QUOTA: { getByName: () => ({ fetch: async () => new Response(null, { status: ready ? 204 : 503 }) }) },
  };
  const methods = [];
  t.mock.method(globalThis, 'fetch', async (_target, init) => {
    methods.push(init.method);
    const headers = { ...optIn, 'Content-Type': 'application/json',
      ...(upstreamEnabled && { 'X-MacroPulse-Public-Research': 'true' }) };
    return init.method === 'HEAD' ? new Response(null, { headers }) : Response.json([], { headers });
  });
  const get = (overrides = {}) => worker.fetch(request(catalog), { ...config, ...overrides });
  assert.equal((await get()).headers.get('X-MacroPulse-Turnstile-Sitekey'), config.MACROPULSE_TURNSTILE_SITE_KEY);
  upstreamEnabled = false;
  assert.equal((await get()).headers.get('X-MacroPulse-Turnstile-Sitekey'), null);
  upstreamEnabled = true;
  assert.equal((await get({ MACROPULSE_TURNSTILE_SITE_KEY: 'rotated-site-key' })).headers.get('X-MacroPulse-Turnstile-Sitekey'), 'rotated-site-key');
  ready = false;
  assert.equal((await get()).headers.get('X-MacroPulse-Turnstile-Sitekey'), null);
  ready = true;
  assert.equal((await get({ MACROPULSE_RESEARCH_ENABLED: 'false' })).headers.get('X-MacroPulse-Turnstile-Sitekey'), null);
  assert.deepEqual(methods, ['GET', 'HEAD', 'HEAD', 'HEAD', 'HEAD']);
  const stored = [...entries.values()][0];
  assert.equal(stored.headers.get('X-MacroPulse-Public-Research'), null);
  assert.equal(stored.headers.get('X-MacroPulse-Turnstile-Sitekey'), null);
  now += catalogTTL * 1000;
  await get();
  assert.equal(methods.at(-1), 'GET');
});

test('cache never stores errors, private responses, varying responses, cookies, malformed or mismatched JSON', async (t) => {
  const { cache } = cacheFixture(t);
  let send;
  const origin = t.mock.method(globalThis, 'fetch', async (target) => send(target));
  const cases = [
    ...[401, 403, 404, 429, 503, 504].map((status) => () => Response.json({}, { status, headers: optIn })),
    ...[{}, { ...optIn, 'Cache-Control': 'private, no-store' }, { ...optIn, 'Set-Cookie': 'fixture=1' },
      { ...optIn, Vary: 'Accept' }].map((headers) => (target) => Response.json(historyBody(target), { headers })),
    () => new Response('not JSON', { headers: { ...optIn, 'Content-Type': 'application/json' } }),
    (target) => Response.json({ ...historyBody(target), end: '2025-01-01' }, { headers: optIn }),
    () => new Response('x'.repeat(3 * 1024 * 1024 + 1), { headers: { ...optIn, 'Content-Type': 'application/json' } }),
  ];
  for (send of cases) {
    await worker.fetch(request(), env);
    await worker.fetch(request(), env);
  }
  assert.equal(cache.put.mock.callCount(), 0);
  assert.equal(origin.mock.callCount(), cases.length * 2);
});

test('credential-bearing reads bypass both cache lookup and insertion', async (t) => {
  const { cache } = cacheFixture(t);
  t.mock.method(globalThis, 'fetch', async (target) => Response.json(historyBody(target), { headers: optIn }));
  await worker.fetch(request(), env);
  for (const name of ['Authorization', 'Cookie', 'X-MacroPulse-Admin-Secret', 'X-MacroPulse-Admin-Check']) {
    assert.equal((await worker.fetch(request(history, { [name]: 'synthetic' }), env)).status, 200);
  }
  assert.equal(cache.match.mock.callCount(), 1);
  assert.equal(cache.put.mock.callCount(), 1);
});

test('absent, rejected, stalled and disabled caches fall back safely to origin', async (t) => {
  const { cache } = cacheFixture(t);
  const origin = t.mock.method(globalThis, 'fetch', async (target) => Response.json(historyBody(target), { headers: optIn }));
  for (const method of ['match', 'put']) t.mock.method(cache, method, async () => { throw new Error('Cache unavailable'); });
  assert.equal((await worker.fetch(request(), env)).status, 200);
  // Deterministic immediate cache deadline, without waiting on a real timeout.
  t.mock.method(globalThis, 'setTimeout', (callback) => { queueMicrotask(callback); return 0; });
  for (const method of ['match', 'put']) cache[method].mock.mockImplementation(() => new Promise(() => {}));
  assert.equal((await worker.fetch(request(), env)).status, 200);
  assert.equal((await worker.fetch(request(), { ...env, MACROPULSE_PUBLIC_CACHE_ENABLED: 'false' })).status, 200);
  delete globalThis.caches;
  assert.equal((await worker.fetch(request(), env)).status, 200);
  assert.equal(origin.mock.callCount(), 4);
});

test('catalog cache hits retain upstream errors and do not advertise stale Research capability', async (t) => {
  cacheFixture(t);
  let status = 200;
  t.mock.method(globalThis, 'fetch', async (_target, init) => init.method === 'GET'
    ? Response.json([], { headers: optIn })
    : new Response(null, { status, headers: { 'Content-Type': 'application/json', 'Retry-After': '10' } }));
  await worker.fetch(request(catalog), env);
  for (status of [429, 503, 200]) {
    const response = await worker.fetch(request(catalog), env);
    assert.equal(response.status, status === 200 ? 503 : status);
    assert.equal(response.headers.get('X-MacroPulse-Turnstile-Sitekey'), null);
    if (status === 429) assert.equal(response.headers.get('Retry-After'), '10');
  }
});
