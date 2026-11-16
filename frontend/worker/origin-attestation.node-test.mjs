import assert from 'node:assert/strict';
import { timingSafeEqual } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import worker from './public-demo.mjs';
import { attestResearch, consumeOriginPath, originAttestationConfig } from './origin-attestation.mjs';

const fixture = JSON.parse(readFileSync(new URL('../../tests/fixtures/security/origin-attestation-v1.json', import.meta.url)));
const [sample] = fixture.vectors;
const now = sample.durable_object.issuedAt;
const host = 'demo.example';
const base = 'https://' + host;
const originKey = fixture.origin_keys[0][0];
const testEnvironment = () => ({
  MACROPULSE_RESEARCH_ENABLED: 'true',
  MARKETPULSE_DEMO_MACRO_ENABLED: 'true',
  MARKETPULSE_DEMO_ORIGIN_URL: 'https://fixture.onrender.com',
  MACROPULSE_RESEARCH_HOSTNAME: host,
  MACROPULSE_RESEARCH_ORIGIN_KEYS: JSON.stringify(fixture.origin_keys),
  MACROPULSE_RESEARCH_ORIGIN_ACTIVE_KID: originKey,
  MACROPULSE_RESEARCH_VERIFY_KEYS: JSON.stringify(fixture.verify_keys),
  MACROPULSE_RESEARCH_HASH_KEY: '1234567890abcdef'.repeat(3) + 'fdecba0987654321',
  MACROPULSE_TURNSTILE_SITE_KEY: 'test_public_site_key',
  MACROPULSE_TURNSTILE_SECRET: 'test_synthetic_turnstile_secret',
  RESEARCH_ATTEMPT_RATE_LIMIT: { limit: async () => ({ success: true }) },
  RESEARCH_VERIFY_RATE_LIMIT: { limit: async () => ({ success: true }) },
  RESEARCH_QUOTA: { getByName: () => assert.fail('unexpected DO call') },
});

function callback(headers = sample.callback.headers, init = {}, path = consumeOriginPath, address = base) {
  return new Request(address + path, { method: 'POST', body: sample.callback.body_ascii, ...init,
    headers: { ...headers, 'CF-Connecting-IP': '192.0.2.5' },
  });
}

test('Worker signing reproduces both shared vectors after binding their exact wire bytes', async (t) => {
  t.mock.method(Date, 'now', () => now);
  for (const vector of fixture.vectors) {
    const headers = vector.headers;
    const env = { ...testEnvironment(), MACROPULSE_RESEARCH_ORIGIN_ACTIVE_KID: headers['X-MacroPulse-Origin-Key-Id'] };
    const config = originAttestationConfig(env);
    const quota = { fetch: async (url, init) => {
      assert.equal(new URL(url).pathname, '/bind-origin');
      assert.deepEqual(JSON.parse(init.body), vector.durable_object);
      return new Response(null, { status: 204 });
    } };
    assert.deepEqual(await attestResearch(config, quota, {
      id: vector.durable_object.id, expires: vector.durable_object.expires,
      scope: headers['X-MacroPulse-Origin-Scope'], ip: headers['X-MarketPulse-Client-IP'],
      researchId: headers['X-MacroPulse-Research-Id'], body: Buffer.from(vector.body_base64, 'base64'),
    }), headers);
  }
});

test('callback authenticates an independent rotating key and exposes only consumption', async (t) => {
  t.mock.method(Date, 'now', () => now);
  Object.defineProperty(crypto.subtle, 'timingSafeEqual', { value: timingSafeEqual, configurable: true });
  t.after(() => { delete crypto.subtle.timingSafeEqual; });
  const env = testEnvironment();
  const limits = [];
  const calls = [];
  env.RESEARCH_VERIFY_RATE_LIMIT.limit = async ({ key }) => {
    limits.push(key);
    return { success: true };
  };
  env.RESEARCH_QUOTA.getByName = (name) => ({ fetch: async (url, init) => {
    calls.push([name, new URL(url).pathname, JSON.parse(init.body)]);
    return new Response(null, { status: 204 });
  } });

  assert.equal((await worker.fetch(callback(), env)).status, 204);
  const admin = fixture.vectors[1];
  assert.equal((await worker.fetch(callback(admin.callback.headers, { body: admin.callback.body_ascii }), env)).status, 204);
  assert.deepEqual(calls, [
    ['macropulse-research-global-v1', '/consume-origin', sample.durable_object],
    ['macropulse-research-admin-budget-v1', '/consume-origin', admin.durable_object],
  ]);
  for (const headers of [{}, { ...sample.callback.headers, Authorization: 'Bearer ' + fixture.origin_keys[0][1] },
    { ...sample.callback.headers, 'X-MacroPulse-Verify-Key-Id': 'unknown' },
    { ...sample.callback.headers, Authorization: sample.callback.headers.Authorization + ', duplicate' }]) {
    assert.equal((await worker.fetch(callback(headers), env)).status, 403);
  }
  for (const path of ['/api/internal/research/admit', '/api/internal/research/bind-origin', '/api/internal/research/release']) {
    assert.equal((await worker.fetch(callback(undefined, {}, path), env)).status, 404);
  }
  assert.equal((await worker.fetch(callback(undefined, { method: 'GET', body: undefined }), env)).status, 405);
  assert.equal((await worker.fetch(callback(undefined, {}, consumeOriginPath + '?x=1'), env)).status, 400);
  assert.equal((await worker.fetch(callback(undefined, {}, consumeOriginPath, 'https://wrong.example'), env)).status, 403);
  assert.equal((await worker.fetch(callback(undefined, { body: 'x'.repeat(193) }), env)).status, 413);
  assert.equal((await worker.fetch(callback(undefined, { body: sample.callback.body_ascii.replace(sample.durable_object.id, 'not-uuid') }), env)).status, 400);
  assert.equal((await worker.fetch(callback(undefined, { body: sample.callback.body_ascii.replace(String(now), String(now + 2001)) }), env)).status, 403);

  const retired = { ...env, MACROPULSE_RESEARCH_VERIFY_KEYS: JSON.stringify(fixture.verify_keys.slice(1)) };
  assert.equal((await worker.fetch(callback(), retired)).status, 403);
  assert.equal(calls.length, 2);
  assert.deepEqual(limits.slice(0, 4), ['ip:192.0.2.5', 'kid:test-verify-a', 'ip:192.0.2.5', 'kid:test-verify-b']);

  for (const envOverride of [
    { RESEARCH_VERIFY_RATE_LIMIT: { limit: async () => ({ success: false }) } },
    { RESEARCH_VERIFY_RATE_LIMIT: { limit: async ({ key }) => ({ success: key.startsWith('ip:') }) } },
  ]) assert.equal((await worker.fetch(callback(), { ...env, ...envOverride })).status, 429);
  assert.equal(calls.length, 2);
  env.RESEARCH_QUOTA.getByName = () => { throw new Error('synthetic outage'); };
  assert.equal((await worker.fetch(callback(), env)).status, 503);
});

test('only successful admission binds and signs, with no legacy header or payload changes on the wire', async (t) => {
  t.mock.method(Date, 'now', () => now);
  const env = testEnvironment();
  const paths = [];
  let quotaOutcome = 'quota';
  let turnstile = false;
  env.RESEARCH_QUOTA.getByName = () => ({ fetch: async (url) => {
    const path = new URL(url).pathname;
    paths.push(path);
    if (path === '/admit') {
      return Response.json({ code: quotaOutcome, retry: 60, id: sample.durable_object.id, expires: sample.durable_object.expires });
    }
    return new Response(null, { status: 204 });
  } });
  const body = Buffer.from(sample.body_base64, 'base64');
  const request = () => new Request(base + '/api/macro/analysis', {
    method: 'POST', body, headers: { Origin: base, 'Content-Type': 'application/json',
      'X-MacroPulse-Turnstile': 'test-token', 'CF-Connecting-IP': '192.0.2.3' },
  });
  let originCalls = 0;
  const status = t.mock.method(globalThis, 'fetch', async (url, init) => {
    if (new URL(url).hostname === 'challenges.cloudflare.com') {
      return Response.json({ success: turnstile, hostname: host, action: 'research', challenge_ts: new Date(now).toISOString() });
    }
    originCalls++;
    assert.deepEqual(Buffer.from(init.body), body);
    assert.equal(init.headers['X-MarketPulse-Origin'], undefined);
    assert.equal(init.headers['X-MacroPulse-Origin-Nonce'], sample.durable_object.id);
    assert.match(init.headers['X-MacroPulse-Origin-Signature'], /^[0-9a-f]{64}$/);
    return Response.json({}, { headers: { 'X-MacroPulse-Research-Complete': 'true' } });
  });
  assert.equal((await worker.fetch(request(), env)).status, 403);
  assert.deepEqual(paths, []);
  turnstile = true;
  assert.equal((await worker.fetch(request(), env)).status, 429);
  assert.deepEqual(paths, ['/admit']);
  assert.equal((await worker.fetch(request(), { ...env, MACROPULSE_RESEARCH_ORIGIN_KEYS: '' })).status, 503);
  assert.equal(status.mock.callCount(), 2);
  assert.equal(originCalls, 0);
  quotaOutcome = 'ok';
  assert.equal((await worker.fetch(request(), env)).status, 200);
  assert.equal(originCalls, 1);
  assert.deepEqual(paths, ['/admit', '/admit', '/bind-origin', '/release']);
});
