import { DatabaseSync } from 'node:sqlite';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker, { ResearchQuota } from './public-demo.mjs';
import { readResearchPayload } from './research.mjs';

const host = 'https://demo.example';
const analyze = '/api/macro/analysis';
const catalog = '/api/macro/indicators';
const render = 'https://macropulse-fixture.onrender.com';
const digest = (value) => createHash('sha256').update(value).digest('hex');
const secret = digest('synthetic origin');
const visitor = digest('synthetic visitor');
const minute = 60_000;
const day = 24 * 60 * minute;
const payload = {
  question: 'What happened during COVID-19?', indicator_id: 'eur-usd', event_id: 'covid', research_version: 'v2',
  start: '2020-02-01', end: '2020-12-31',
};

test('Research admits custom windows and retains strict event and date validation', () => {
  const bytes = (body) => new TextEncoder().encode(JSON.stringify(body));
  const { indicator_id: indicator, event_id: event, ...custom } = payload;
  const request = { ...custom, start: '2017-01-01', end: '2026-01-01' };
  assert.deepEqual(JSON.parse(readResearchPayload(bytes(request))), request);
  assert.throws(() => readResearchPayload(bytes({ ...request, event_id: event, indicator_id: indicator })));
  assert.throws(() => readResearchPayload(bytes({ ...request, start: '2017-02-30' })));
  assert.throws(() => readResearchPayload(bytes({ ...request, start: '2010-01-01' })));
});

function sqliteFixture(t) {
  const db = new DatabaseSync(':memory:');
  t.after(() => db.close());
  let alarm = null;
  const storage = {
    sql: { exec(query, ...args) {
      if (!args.length && query.trim().startsWith('CREATE TABLE')) {
        db.exec(query);
        return;
      }
      const rows = db.prepare(query).all(...args);
      return { toArray: () => rows, one: () => {
        assert.equal(rows.length, 1);
        return rows[0];
      } };
    } },
    transactionSync(fn) {
      db.exec('BEGIN');
      try {
        const result = fn();
        db.exec('COMMIT');
        return result;
      } catch (cause) {
        db.exec('ROLLBACK');
        throw cause;
      }
    },
    async getAlarm() { return alarm; },
    async setAlarm(value) { alarm = value; },
  };
  return { db, storage, instance: () => new ResearchQuota({ storage }) };
}

function internal(quota, path, body) {
  return quota.fetch(new Request('https://quota.test' + path, { method: 'POST', body: JSON.stringify(body) }));
}

test('SQLite admissions are atomic and survive instance recreation, timeout and cancellation', async (t) => {
  const fixture = sqliteFixture(t);
  let quota = fixture.instance();
  let now = Date.UTC(2026, 10, 12, 10);
  t.mock.method(Date, 'now', () => now);
  const attempt = async (token, id = visitor, challengeTime = now) =>
    (await internal(quota, '/admit', { visitor: id, token: digest(token), challengeTime })).json();
  const first = await Promise.all(['1', '2', '3'].map((token) => attempt(token)));
  assert.equal(first.filter((result) => result.code === 'ok').length, 2);
  assert.equal(first.filter((result) => result.code === 'busy').length, 1);
  quota = fixture.instance();
  assert.equal((await attempt('1')).code, 'challenge');
  assert.equal((await attempt('3')).code, 'challenge');
  assert.equal((await attempt('4')).code, 'busy');
  assert.equal((await internal(quota, '/release', { id: first[0].id })).status, 204);
  assert.equal((await attempt('5')).code, 'ok');
  now += minute;
  assert.equal((await attempt('6')).code, 'quota');
  assert.equal((await attempt('stale', visitor, now - 5 * minute)).code, 'challenge');
  assert.equal((await attempt('7', digest('other visitor'))).code, 'ok');
  now += 9 * minute;
  assert.equal((await attempt('8')).code, 'ok');
  await quota.alarm();
  assert.equal(fixture.db.prepare('SELECT count(*) AS count FROM tokens').get().count, 1);
});

test('visitor day, UTC global day and monthly budget survive restart and data cleanup', async (t) => {
  const fixture = sqliteFixture(t);
  let quota = fixture.instance();
  let now = Date.UTC(2026, 10, 12, 1);
  t.mock.method(Date, 'now', () => now);
  const admit = async (index, id) => {
    const response = await internal(quota, '/admit', { visitor: id, token: digest(String(index)), challengeTime: now });
    const result = await response.json();
    if (result.id) await internal(quota, '/release', { id: result.id });
    return result;
  };
  for (let index = 0; index < 10; index++) {
    assert.equal((await admit(index, visitor)).code, 'ok');
    now += 10 * minute;
  }
  assert.equal((await admit(10, visitor)).code, 'quota');
  for (let index = 11; index <= 100; index++) assert.equal((await admit(index, digest(String(index)))).code, 'ok');
  assert.equal((await admit(101, digest('other'))).code, 'quota');
  now += day;
  quota = fixture.instance();
  await quota.alarm();
  assert.equal(fixture.db.prepare('SELECT count(*) AS count FROM attempts').get().count, 0);
  assert.equal((await admit(102, digest('other'))).code, 'budget');
  now = Date.UTC(2026, 11, 1, 0, 1);
  assert.equal((await admit(103, visitor)).code, 'ok');
});

function fixtureEnv(quota) {
  return {
    ASSETS: { fetch: async () => new Response('MacroPulse AI') },
    MARKETPULSE_DEMO_ORIGIN_URL: render,
    MARKETPULSE_DEMO_ORIGIN_SECRET: secret,
    MARKETPULSE_DEMO_MACRO_ENABLED: 'true',
    MACROPULSE_RESEARCH_ENABLED: 'true',
    MACROPULSE_RESEARCH_HOSTNAME: 'demo.example',
    MACROPULSE_RESEARCH_HASH_KEY: digest('synthetic hash key'),
    MACROPULSE_TURNSTILE_SITE_KEY: 'synthetic-site-key',
    MACROPULSE_TURNSTILE_SECRET: 'synthetic-secret-for-tests',
    RESEARCH_QUOTA: { getByName: (name) => {
      assert.equal(name, 'macropulse-research-global-v1');
      return { fetch: (url, init) => quota.fetch(new Request(url, init)) };
    } },
    CATALOG_RATE_LIMIT: { limit: async () => ({ success: true }) },
  };
}

function edgeRequest(path, init = {}) {
  return new Request(host + path, {
    ...init, headers: { 'CF-Connecting-IP': '192.0.2.1', Origin: host, ...init.headers },
  });
}

function post(token, overrides = {}) {
  return edgeRequest(analyze, { method: 'POST', body: JSON.stringify(payload), ...overrides, headers: {
    'Content-Type': 'application/json', 'X-MacroPulse-Turnstile': token, ...overrides.headers,
  } });
}

test('edge verifies same-origin Turnstile before origin, strips secrets, and keeps charts with Research off', async (t) => {
  const fixture = sqliteFixture(t);
  const env = fixtureEnv(fixture.instance());
  let verify = 0;
  let generated = 0;
  let verification = { success: true, hostname: 'demo.example', action: 'research', challenge_ts: new Date().toISOString() };
  const upstream = t.mock.method(globalThis, 'fetch', async (url, init) => {
    if (String(url).startsWith('https://challenges.cloudflare.com/')) {
      verify++;
      assert.equal(init.redirect, 'manual');
      assert.equal(init.body.get('secret'), env.MACROPULSE_TURNSTILE_SECRET);
      return Response.json(verification);
    }
    if (String(url) === render + '/macro/indicators') {
      return Response.json([], { headers: { 'X-MacroPulse-Public-Research': 'true' } });
    }
    assert.equal(String(url), render + '/macro/analysis');
    generated++;
    assert.equal(init.headers['X-MarketPulse-Origin'], secret);
    assert.equal(init.headers['X-MarketPulse-Client-IP'], '192.0.2.1');
    assert.equal(init.headers['X-MacroPulse-Turnstile'], undefined);
    assert.equal(init.headers.Authorization, undefined);
    assert.ok(Number(init.headers['X-MacroPulse-Research-Expires']) > Date.now());
    assert.deepEqual(JSON.parse(init.body), payload);
    return Response.json({ research_v2: true }, { headers: { 'X-MacroPulse-Research-Complete': 'true' } });
  });
  const result = await worker.fetch(edgeRequest(catalog), env);
  assert.equal(result.headers.get('X-MacroPulse-Turnstile-Sitekey'), env.MACROPULSE_TURNSTILE_SITE_KEY);
  assert.equal(result.headers.get('X-MacroPulse-Public-Research'), null);
  assert.equal((await worker.fetch(post('valid', { headers: { Origin: 'https://forged.example' } }), env)).status, 403);
  assert.equal((await worker.fetch(post('valid', { body: '{"duplicate":"x","duplicate":"y"}' }), env)).status, 422);
  assert.equal((await worker.fetch(post('valid', { body: 'x'.repeat(4097) }), env)).status, 413);
  assert.equal(verify, 0);
  assert.equal((await worker.fetch(post('valid', { headers: {
    Authorization: 'spoof', 'X-MarketPulse-Origin': 'spoof', 'X-MarketPulse-Client-IP': '192.0.2.2',
  } }), env)).status, 200);
  assert.equal((await worker.fetch(post('valid'), env)).status, 403);
  for (const changes of [
    { action: 'wrong' }, { hostname: 'wrong.example' }, { challenge_ts: '2020-01-01T12:00:00Z' }, { success: false },
  ]) {
    const previous = verification;
    verification = { ...previous, ...changes };
    assert.equal((await worker.fetch(post('different'), env)).status, 403);
    verification = previous;
  }
  assert.equal(generated, 1);
  assert.equal(fixture.db.prepare('SELECT count(*) AS count FROM leases').get().count, 0);
  const off = { ...env, MACROPULSE_RESEARCH_ENABLED: 'false' };
  assert.equal((await worker.fetch(post('another'), off)).status, 503);
  const reads = await worker.fetch(edgeRequest(catalog), off);
  assert.equal(reads.status, 200);
  assert.equal(reads.headers.get('X-MacroPulse-Turnstile-Sitekey'), null);
  assert.equal((await worker.fetch(edgeRequest('/api/assets'), env)).status, 404);
  assert.equal(upstream.mock.calls.length, 9);
});

test('origin 504 keeps lease, and Siteverify redirect or outage fail closed', async (t) => {
  const fixture = sqliteFixture(t);
  const env = fixtureEnv(fixture.instance());
  let failure = true;
  let origins = 0;
  t.mock.method(globalThis, 'fetch', async (url) => {
    if (String(url).startsWith('https://challenges.cloudflare.com/')) {
      if (failure) return Response.redirect('https://untrusted.example');
      return Response.json({
        success: true, hostname: 'demo.example', action: 'research', challenge_ts: new Date().toISOString(),
      });
    }
    origins++;
    return Response.json({}, { status: 504, headers: { 'X-MacroPulse-Research-Complete': 'true' } });
  });
  assert.equal((await worker.fetch(post('redirect'), env)).status, 503);
  assert.equal(origins, 0);
  failure = false;
  assert.equal((await worker.fetch(post('one'), env)).status, 504);
  assert.equal((await worker.fetch(post('two'), env)).status, 504);
  const busy = await worker.fetch(post('three'), env);
  assert.equal(busy.status, 503);
  assert.ok(busy.headers.has('Retry-After'));
  assert.equal(origins, 2);
});

test('Siteverify rejects oversized declared and streamed responses before admission', async (t) => {
  const fixture = sqliteFixture(t);
  const env = fixtureEnv(fixture.instance());
  let declared = true;
  const pulls = [];
  const cancellations = [];
  t.mock.method(globalThis, 'fetch', async (url) => {
    assert.match(String(url), /^https:\/\/challenges\.cloudflare\.com\//);
    let chunks = 0;
    const body = new ReadableStream({
      pull(controller) {
        chunks++;
        controller.enqueue(new Uint8Array(2049));
      },
      cancel() { cancellations.push(chunks); },
    }, { highWaterMark: 0 });
    pulls.push(() => chunks);
    return new Response(body, { headers: declared ? { 'Content-Length': '4097' } : {} });
  });
  assert.equal((await worker.fetch(post('declared-size'), env)).status, 503);
  declared = false;
  assert.equal((await worker.fetch(post('streamed-size'), env)).status, 503);
  assert.deepEqual(pulls.map((read) => read()), [0, 2]);
  assert.deepEqual(cancellations, [0, 2]);
  assert.equal(fixture.db.prepare('SELECT count(*) AS count FROM attempts').get().count, 0);
});

test('Siteverify applies the fetch deadline while reading the response stream', async (t) => {
  const fixture = sqliteFixture(t);
  const env = fixtureEnv(fixture.instance());
  const deadline = new AbortController();
  const makeTimeout = AbortSignal.timeout;
  t.mock.method(AbortSignal, 'timeout', (ms) => ms === 4000 ? deadline.signal : makeTimeout(ms));
  let beginRead;
  const reading = new Promise((resolve) => { beginRead = resolve; });
  let cancelled = false;
  t.mock.method(globalThis, 'fetch', async (_url, init) => {
    assert.equal(init.signal, deadline.signal);
    return new Response(new ReadableStream({
      pull() { beginRead(); },
      cancel() { cancelled = true; },
    }, { highWaterMark: 0 }));
  });
  const response = worker.fetch(post('slow-response'), env);
  await reading;
  deadline.abort(new DOMException('Synthetic deadline', 'TimeoutError'));
  assert.equal((await response).status, 504);
  assert.equal(cancelled, true);
  assert.equal(fixture.db.prepare('SELECT count(*) AS count FROM attempts').get().count, 0);
});
