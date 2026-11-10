import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';
import worker from './public-demo.mjs';

const secret = createHash('sha256').update('MarketPulse synthetic test fixture').digest('hex');
const url = 'https://portfolio.example';
const ip = '192.0.2.3';
const allowed = { limit: async ({ key }) => ({ success: key === ip }) };
const env = {
  MARKETPULSE_DEMO_ORIGIN_URL: 'https://marketpulse-fixture.onrender.com',
  MARKETPULSE_DEMO_ORIGIN_SECRET: secret,
  CATALOG_RATE_LIMIT: allowed,
  HISTORY_RATE_LIMIT: allowed,
  MARKETPULSE_DEMO_MACRO_ENABLED: 'true',
  ASSETS: { fetch: async () => new Response('static shell') },
};

function request(path, init = {}) {
  return new Request(url + path, { ...init, headers: { 'CF-Connecting-IP': ip, ...init.headers } });
}

test('static assets remain accessible and unauthorized API routes never reach Render', async (t) => {
  const upstream = t.mock.method(globalThis, 'fetch', () => assert.fail('Unexpected origin fetch'));
  assert.equal(await (await worker.fetch(request('/'), env)).text(), 'static shell');
  for (const path of ['/api', '/api/docs', '/api/openapi.json', '/api/assets/4/history', '/api/assets/1/latest']) {
    assert.equal((await worker.fetch(request(path), env)).status, 404);
  }
  for (const enabled of ['true', 'false']) {
    for (const path of ['/api/assets', '/api/assets/1/history?start=2020-01-01&end_exclusive=2020-01-03', '/api/analysis']) {
      for (const method of ['GET', 'POST']) {
        assert.equal((await worker.fetch(request(path, { method }), {
          ...env, MARKETPULSE_DEMO_MACRO_ENABLED: enabled,
        })).status, 404);
      }
    }
  }
  assert.equal(upstream.mock.calls.length, 0);
});

test('allowlisted reads use only a fixed HTTPS origin and replace visitor headers', async (t) => {
  t.mock.method(globalThis, 'fetch', async (target, init) => {
    assert.equal(String(target), env.MARKETPULSE_DEMO_ORIGIN_URL + '/macro/indicators/eur-usd/history?start=2020-01-01&end=2020-01-03');
    assert.deepEqual(init.headers, {
      Accept: 'application/json',
      'X-MarketPulse-Origin': secret,
      'X-MarketPulse-Client-IP': ip,
    });
    assert.equal(init.redirect, 'manual');
    assert.ok(init.signal instanceof AbortSignal);
    return Response.json({ bars: [] }, { headers: { 'Set-Cookie': 'fixture=1', 'X-Debug': 'fixture' } });
  });
  const response = await worker.fetch(request('/api/macro/indicators/eur-usd/history?start=2020-01-01&end=2020-01-03', {
    headers: { Authorization: 'fixture', 'X-MarketPulse-Origin': 'fixture', 'X-MarketPulse-Client-IP': '192.0.2.99' },
  }), env);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { bars: [] });
  assert.equal(response.headers.get('Cache-Control'), 'no-store');
  assert.equal(response.headers.get('Set-Cookie'), null);
  assert.equal(response.headers.get('X-Debug'), null);
});

test('edge limits and origin limits both return 429 with Retry-After', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', async () =>
    Response.json({ detail: 'fixture' }, { status: 429, headers: { 'Retry-After': '15' } }));
  const blocked = await worker.fetch(request('/api/macro/indicators'), {
    ...env, CATALOG_RATE_LIMIT: { limit: async () => ({ success: false }) },
  });
  assert.equal(blocked.status, 429);
  assert.equal(blocked.headers.get('Retry-After'), '60');
  assert.equal(fetch.mock.calls.length, 0);
  const upstreamBlocked = await worker.fetch(request('/api/macro/indicators'), env);
  assert.equal(upstreamBlocked.status, 429);
  assert.equal(upstreamBlocked.headers.get('Retry-After'), '15');
});

test('redirects, oversized bodies, and origin errors become generic service errors', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch');
  const upstreamErrors = [
    () => Response.redirect('https://portfolio.example'),
    () => new Response('<h1>Unavailable</h1>', { status: 503 }),
    () => Response.json({ detail: secret }, { status: 503 }),
    () => new Response(new Uint8Array(3 * 1024 * 1024 + 1), { headers: { 'Content-Type': 'application/json' } }),
  ];
  for (const send of upstreamErrors) {
    fetch.mock.mockImplementationOnce(send);
    const response = await worker.fetch(request('/api/macro/indicators'), env);
    assert.equal(response.status, 503);
    assert.equal((await response.json()).detail, 'Public demo service unavailable');
  }
  fetch.mock.mockImplementationOnce(() => { throw new DOMException('fixture timeout', 'TimeoutError'); });
  assert.equal((await worker.fetch(request('/api/macro/indicators'), env)).status, 504);
});

test('macro reads require opt-in and an exact route, method, query and browser origin', async (t) => {
  const upstream = t.mock.method(globalThis, 'fetch', async () => Response.json([]));
  const enabled = { ...env, MARKETPULSE_DEMO_MACRO_ENABLED: 'true' };
  const catalog = '/api/macro/indicators';
  const path = catalog + '/eur-usd/history';
  const query = '?start=2026-01-01&end=2026-01-31';
  assert.equal((await worker.fetch(request(catalog), { ...env, MARKETPULSE_DEMO_MACRO_ENABLED: 'false' })).status, 404);
  assert.equal((await worker.fetch(request(path + query), { ...env, MARKETPULSE_DEMO_MACRO_ENABLED: 'false' })).status, 404);
  assert.equal((await worker.fetch(request(catalog + '/other/history' + query), enabled)).status, 404);
  assert.equal((await worker.fetch(request('/api/macro/analysis', { method: 'POST' }), enabled)).status, 404);
  assert.equal((await worker.fetch(request(path + query, { method: 'POST' }), enabled)).status, 405);
  assert.equal((await worker.fetch(request(catalog, { headers: { Origin: 'https://foreign.example' } }), enabled)).status, 403);
  assert.equal((await worker.fetch(request(path + '?start=2026-01-01&start=2026-01-31'), enabled)).status, 422);
  assert.equal((await worker.fetch(request(path + query + '&extra=1'), enabled)).status, 422);
  assert.equal((await worker.fetch(request(path + '?start=2026-02-30&end=2026-03-04'), enabled)).status, 422);
  assert.equal((await worker.fetch(request(path + '?start=2010-01-01&end=2026-01-31'), enabled)).status, 422);
  assert.equal(upstream.mock.calls.length, 0);

  const response = await worker.fetch(request(path + query, { headers: { Origin: url } }), enabled);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('Access-Control-Allow-Origin'), null);
  assert.equal(response.headers.get('Cache-Control'), 'no-store');
  const [target, init] = upstream.mock.calls[0].arguments;
  assert.equal(String(target), enabled.MARKETPULSE_DEMO_ORIGIN_URL + '/macro/indicators/eur-usd/history' + query);
  assert.equal(init.headers['X-MarketPulse-Origin'], secret);
  assert.equal(init.headers.Origin, undefined);
  const quota = { limit: async () => ({ success: false }) };
  assert.equal((await worker.fetch(request(path + query), { ...enabled, HISTORY_RATE_LIMIT: quota })).status, 429);
  assert.equal((await worker.fetch(request(catalog), { ...enabled, CATALOG_RATE_LIMIT: quota })).status, 429);
  assert.equal(upstream.mock.calls.length, 1);
});

test('the PWA service worker never intercepts macro API requests for offline caching', () => {
  const listeners = new Map();
  runInNewContext(readFileSync(new URL('../pwa/service-worker.js', import.meta.url), 'utf8'), {
    self: {
      registration: { scope: url + '/' },
      addEventListener: (type, handler) => listeners.set(type, handler),
    },
    URL, version: 'fixture', shell: [],
  });
  for (const path of ['/api', '/api/macro/indicators', '/api/macro/indicators/hicp-yoy/history', '/api/macro/analysis']) {
    listeners.get('fetch')({
      request: request(path),
      respondWith: () => assert.fail('API must bypass offline interception'),
    });
  }
});
