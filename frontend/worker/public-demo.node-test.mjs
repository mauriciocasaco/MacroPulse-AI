import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
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
  assert.equal((await worker.fetch(request('/api/assets', { method: 'POST' }), env)).status, 405);
  assert.equal((await worker.fetch(request('/api/assets?other=1'), env)).status, 400);
  assert.equal((await worker.fetch(request('/api/assets/1/history?start=' + 'a'.repeat(258)), env)).status, 400);
  assert.equal((await worker.fetch(request('/api/assets/1/history?start=2020-01-01&extra=fixture'), env)).status, 422);
  assert.equal((await worker.fetch(request('/api/assets'), { ...env, MARKETPULSE_DEMO_ORIGIN_URL: url })).status, 503);
  assert.equal((await worker.fetch(new Request(url + '/api/assets'), env)).status, 503);
  const analysis = await worker.fetch(request('/api/analysis', { method: 'POST', body: '{}' }), env);
  assert.equal(analysis.status, 503);
  assert.equal((await analysis.json()).detail, 'Public demo analysis is disabled');
  assert.equal((await worker.fetch(request('/api/analysis', {
    method: 'POST', headers: { 'Content-Length': '8193' }, body: 'x'.repeat(8193),
  }), env)).status, 413);
  assert.equal(upstream.mock.calls.length, 0);
});

test('allowlisted reads use only a fixed HTTPS origin and replace visitor headers', async (t) => {
  t.mock.method(globalThis, 'fetch', async (target, init) => {
    assert.equal(String(target), env.MARKETPULSE_DEMO_ORIGIN_URL + '/assets/1/history?start=2020-01-01&end_exclusive=2020-01-03');
    assert.deepEqual(init.headers, {
      Accept: 'application/json',
      'X-MarketPulse-Origin': secret,
      'X-MarketPulse-Client-IP': ip,
    });
    assert.equal(init.redirect, 'manual');
    assert.ok(init.signal instanceof AbortSignal);
    return Response.json({ bars: [] }, { headers: { 'Set-Cookie': 'fixture=1', 'X-Debug': 'fixture' } });
  });
  const response = await worker.fetch(request('/api/assets/1/history?start=2020-01-01&end_exclusive=2020-01-03', {
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
  const blocked = await worker.fetch(request('/api/assets'), {
    ...env, CATALOG_RATE_LIMIT: { limit: async () => ({ success: false }) },
  });
  assert.equal(blocked.status, 429);
  assert.equal(blocked.headers.get('Retry-After'), '60');
  assert.equal(fetch.mock.calls.length, 0);
  const upstreamBlocked = await worker.fetch(request('/api/assets'), env);
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
    const response = await worker.fetch(request('/api/assets'), env);
    assert.equal(response.status, 503);
    assert.equal((await response.json()).detail, 'Public demo service unavailable');
  }
  fetch.mock.mockImplementationOnce(() => { throw new DOMException('fixture timeout', 'TimeoutError'); });
  assert.equal((await worker.fetch(request('/api/assets'), env)).status, 504);
});
