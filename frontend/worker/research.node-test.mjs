import { DatabaseSync } from 'node:sqlite';
import { createHash, timingSafeEqual } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker, { ResearchQuota } from './public-demo.mjs';
import { adminCheckHeader, adminHeader, readResearchPayload } from './research.mjs';
import { researchIdHeader } from './research-logging.mjs';

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
  assert.equal(first[0].expires, now + 90_000);
  quota = fixture.instance();
  assert.equal((await attempt('1')).code, 'challenge');
  assert.equal((await attempt('3')).code, 'challenge');
  assert.equal((await attempt('4')).code, 'busy');
  assert.equal((await internal(quota, '/release', { id: first[0].id })).status, 204);
  assert.equal((await attempt('5')).code, 'ok');
  now += 89_999;
  assert.equal((await attempt('still-active')).code, 'busy');
  now += 1;
  const reclaimed = await attempt('6');
  assert.equal(reclaimed.code, 'ok');
  await internal(quota, '/release', { id: reclaimed.id });
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
  let now = Date.UTC(2026, 10, 12);
  const start = now;
  t.mock.method(Date, 'now', () => now);
  let token = 0;
  const admit = async (id = visitor) => {
    const response = await internal(quota, '/admit', { visitor: id, token: digest(String(token++)), challengeTime: now });
    const result = await response.json();
    if (result.id) await internal(quota, '/release', { id: result.id });
    return result;
  };
  for (let count = 0; count < 20; count++) assert.equal((await admit()).code, 'ok');
  now += 9 * minute;
  assert.deepEqual(await admit(), { code: 'quota', retry: 60 });
  assert.equal((await admit(digest('another visitor'))).code, 'ok');
  now += minute;
  for (let count = 0; count < 20; count++) assert.equal((await admit()).code, 'ok');
  now += 10 * minute;
  for (let count = 0; count < 10; count++) assert.equal((await admit()).code, 'ok');
  assert.deepEqual(await admit(), { code: 'quota', retry: 85_200 });
  for (let count = 51; count < 300; count++) assert.equal((await admit(digest(String(count)))).code, 'ok');
  assert.deepEqual(await admit(digest('fresh visitor')), { code: 'quota', retry: 85_200 });

  now = start + day;
  quota = fixture.instance();
  await quota.alarm();
  assert.equal(fixture.db.prepare('SELECT count(*) AS count FROM attempts').get().count, 0);
  for (const admissions of [300, 300, 100]) {
    for (let count = 0; count < admissions; count++) assert.equal((await admit(digest(String(count)))).code, 'ok');
    if (admissions === 300) now += day;
  }
  const nextMonth = Date.UTC(2026, 11, 1);
  quota = fixture.instance();
  assert.deepEqual(await admit(), { code: 'budget', retry: (nextMonth - now) / 1000 });
  now = nextMonth;
  assert.equal((await admit()).code, 'ok');
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
    RESEARCH_ATTEMPT_RATE_LIMIT: { limit: async () => ({ success: true }) },
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
  const timeouts = t.mock.method(AbortSignal, 'timeout');
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
  assert.ok(timeouts.mock.calls.some(({ arguments: [duration] }) => duration === 70_000));
  assert.ok(timeouts.mock.calls.some(({ arguments: [duration] }) => duration === 20_000));
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

for (const slowStage of ['worker.render_fetch', 'worker.render_response']) {
  test(`70 s edge deadline applies to ${slowStage} and discards late results`, async (t) => {
    const fixture = sqliteFixture(t);
    const env = fixtureEnv(fixture.instance());
    const records = [];
    t.mock.method(console, 'warn', (event) => records.push(event));
    t.mock.timers.enable({ apis: ['setTimeout'] });
    const controller = new AbortController();
    const originalTimeout = AbortSignal.timeout;
    t.mock.method(AbortSignal, 'timeout', (ms) => {
      if ([2_500, 3_000, 4_000].includes(ms)) return originalTimeout(ms);
      setTimeout(() => controller.abort(new DOMException('Synthetic deadline', 'TimeoutError')), ms);
      return controller.signal;
    });
    const entered = Promise.withResolvers();
    const late = Promise.withResolvers();
    let streamCancelled = false;
    t.mock.method(globalThis, 'fetch', async (url, init) => {
      if (String(url).startsWith('https://challenges.cloudflare.com/')) {
        return Response.json({
          success: true, hostname: 'demo.example', action: 'research', challenge_ts: new Date().toISOString(),
        });
      }
      assert.equal(init.signal, controller.signal);
      if (slowStage === 'worker.render_fetch') {
        entered.resolve();
        return late.promise;
      }
      return new Response(new ReadableStream({
        pull() { entered.resolve(); },
        cancel() { streamCancelled = true; },
      }, { highWaterMark: 0 }), {
        headers: { 'Content-Type': 'application/json', 'X-MacroPulse-Research-Complete': 'true' },
      });
    });

    const pending = worker.fetch(post('timeout'), env);
    await entered.promise;
    t.mock.timers.tick(69_999);
    assert.equal(controller.signal.aborted, false);
    t.mock.timers.tick(1);
    assert.equal(controller.signal.aborted, true);
    late.resolve(Response.json({}, { headers: { 'X-MacroPulse-Research-Complete': 'true' } }));
    const reply = await pending;
    assert.equal(reply.status, 504);
    assert.deepEqual(await reply.json(), { detail: 'Public Research timed out' });
    assert.equal(fixture.db.prepare('SELECT count(*) AS count FROM leases').get().count,
      slowStage === 'worker.render_fetch' ? 1 : 0);
    assert.equal(streamCancelled, slowStage === 'worker.render_response');
    const event = records.find(({ stage }) => stage === slowStage);
    assert.equal(event.error_type, 'TimeoutError');
    assert.equal(event.http_status, 504);
    assert.ok(records.some(({ stage, research_id }) => stage === 'worker.research_total'
      && research_id === event.research_id));
  });
}

test('Research logs distinguish Turnstile, quota and Render failures with safe fields', async (t) => {
  const fixture = sqliteFixture(t);
  const quota = fixture.instance();
  const privateMarker = 'synthetic private detail';
  const visitorSuppliedId = 'cb31e1d0-41f6-4a82-8436-30f47564c5cb';
  const records = [];
  for (const method of ['info', 'warn']) {
    t.mock.method(console, method, (event) => records.push(event));
  }
  let failure = 'turnstile';
  const env = fixtureEnv({
    fetch(request) {
      if (failure === 'quota') throw new TypeError(privateMarker);
      return quota.fetch(request);
    },
  });
  let originCalls = 0;
  let forwardedId;
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    if (String(url).startsWith('https://challenges.cloudflare.com/')) {
      if (failure === 'turnstile') throw new TypeError(privateMarker);
      return Response.json({
        success: true, hostname: 'demo.example', action: 'research', challenge_ts: new Date().toISOString(),
      });
    }
    originCalls++;
    forwardedId = init.headers[researchIdHeader];
    return new Response(privateMarker, { status: 502 });
  });

  for (const [phase, stage, errorType] of [
    ['turnstile', 'worker.turnstile', 'TypeError'],
    ['quota', 'worker.quota_admit', 'TypeError'],
    ['origin', 'worker.render_fetch', 'UnexpectedUpstreamStatus'],
  ]) {
    failure = phase;
    records.length = 0;
    const response = await worker.fetch(post('synthetic-token', { headers: { [researchIdHeader]: visitorSuppliedId } }), env);
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { detail: 'Public Research is unavailable' });
    assert.equal(response.headers.get(researchIdHeader), null);
    const entry = records.find((record) => record.stage === stage);
    assert.equal(entry.error_type, errorType);
    assert.equal(entry.outcome, 'error');
    assert.ok(records.every((record) => record.research_id === entry.research_id && record.duration_ms >= 0));
    assert.notEqual(entry.research_id, visitorSuppliedId);
    if (phase === 'origin') {
      assert.equal(entry.research_id, forwardedId);
      assert.equal(entry.http_status, 502);
    }
    for (const sensitive of [privateMarker, payload.question, secret, env.MACROPULSE_TURNSTILE_SECRET,
      env.MACROPULSE_RESEARCH_HASH_KEY, 'synthetic-token', '192.0.2.1', render]) {
      assert.ok(!JSON.stringify(records).includes(sensitive));
    }
  }
  assert.equal(originCalls, 1);
  assert.equal(fixture.db.prepare('SELECT count(*) AS count FROM leases').get().count, 1);
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

test('rolling quota persists across UTC midnight and frees slots as individual attempts age out', async (t) => {
  const quota = sqliteFixture(t).instance();
  const midnight = Date.UTC(2026, 10, 13);
  let now = midnight - 8 * minute;
  t.mock.method(Date, 'now', () => now);
  let token = 0;
  const admit = async () => {
    const result = await (await internal(quota, '/admit', { visitor, token: digest(String(token++)), challengeTime: now })).json();
    if (result.id) await internal(quota, '/release', { id: result.id });
    return result;
  };
  assert.equal((await admit()).code, 'ok');
  now += minute;
  for (let count = 0; count < 19; count++) assert.equal((await admit()).code, 'ok');
  now = midnight;
  await quota.alarm();
  assert.deepEqual(await admit(), { code: 'quota', retry: 120 });
  now += 2 * minute;
  assert.equal((await admit()).code, 'ok');
  assert.deepEqual(await admit(), { code: 'quota', retry: 60 });
});

function adminFixture(t) {
  Object.defineProperty(crypto.subtle, 'timingSafeEqual', { value: timingSafeEqual, configurable: true });
  t.after(() => { delete crypto.subtle.timingSafeEqual; });
  const publicQuota = sqliteFixture(t);
  const adminAuth = sqliteFixture(t);
  const adminBudget = sqliteFixture(t);
  const budget = adminBudget.instance();
  const admin = adminAuth.instance();
  const env = fixtureEnv(publicQuota.instance());
  env.MACROPULSE_ADMIN_SECRET = digest('synthetic administrator credential');
  const getPublic = env.RESEARCH_QUOTA.getByName;
  const publicAccess = t.mock.fn(getPublic);
  env.RESEARCH_QUOTA.getByName = (name) => name === 'macropulse-research-admin-auth-v1'
    ? { fetch: (url, init) => admin.fetch(new Request(url, init)) }
    : name === 'macropulse-research-admin-budget-v1'
      ? { fetch: (url, init) => budget.fetch(new Request(url, init)) } : publicAccess(name);
  return { env, publicQuota, adminAuth, adminBudget, publicAccess };
}

function adminPost(credential, { check = false, headers = {}, ...overrides } = {}) {
  return edgeRequest(analyze, {
    method: 'POST', body: check ? '{}' : JSON.stringify(payload), ...overrides,
    headers: { 'Content-Type': 'application/json', ...headers,
      ...(credential !== undefined && { [adminHeader]: credential }), ...(check && { [adminCheckHeader]: 'true' }),
    },
  });
}

test('public and administrator Research share Render validation and separate admissions', async (t) => {
  const { env, publicQuota, publicAccess } = adminFixture(t);
  const credential = env.MACROPULSE_ADMIN_SECRET;
  const limits = publicQuota.db.prepare('SELECT count(*) AS count FROM attempts');
  let verified = 0;
  let generated = 0;
  const hybridResult = {
    research_v2: true, status: 'insufficient_evidence', support: 'insufficient',
    insufficiency_reason: 'unsupported_operation_or_series',
    general_fallback: {
      triggered: true, verified: false, reason: 'unsupported_operation_or_series', status: 'answered',
      model_id: 'gemini-3.6-flash', answer: 'A currency shock could affect the cost of imports.',
    },
  };
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    if (String(url).includes('siteverify')) {
      verified++;
      return Response.json({
        success: true, hostname: 'demo.example', action: 'research', challenge_ts: new Date().toISOString(),
      });
    }
    assert.equal(String(url), render + '/macro/analysis');
    assert.equal(init.redirect, 'manual');
    assert.equal(init.headers['X-MarketPulse-Origin'], secret);
    assert.equal(init.headers['X-MarketPulse-Client-IP'], '192.0.2.1');
    assert.equal(init.headers[adminHeader], undefined);
    assert.equal(init.headers[adminCheckHeader], undefined);
    assert.equal(init.headers.Authorization, undefined);
    assert.equal(init.headers['X-MacroPulse-Turnstile'], undefined);
    assert.equal(JSON.stringify(init.headers).includes(credential), false);
    assert.ok(Number(init.headers['X-MacroPulse-Research-Expires']) - Date.now() >= 72_000);
    assert.deepEqual(JSON.parse(init.body), payload);
    generated++;
    return Response.json(hybridResult, { headers: { 'X-MacroPulse-Research-Complete': 'true' } });
  });

  const verify = await worker.fetch(adminPost(credential, { check: true }), env);
  assert.equal(verify.status, 204);
  assert.equal(verify.headers.get('Cache-Control'), 'no-store');
  assert.equal(generated, 0);
  assert.equal(publicAccess.mock.callCount(), 0);
  const publicResult = await worker.fetch(post('public-token'), env);
  assert.equal(publicResult.status, 200);
  assert.deepEqual(await publicResult.json(), hybridResult);
  assert.equal(limits.get().count, 1);
  assert.equal(publicQuota.db.prepare('SELECT total FROM monthly_reservations').get().total, 1);

  const now = new Date();
  const month = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1);
  publicQuota.db.prepare('UPDATE monthly_reservations SET total = 1000 WHERE month = ?').run(month);
  const exhausted = await worker.fetch(post('another-public-token'), env);
  assert.equal(exhausted.status, 429);
  assert.ok(Number(exhausted.headers.get('Retry-After')) > 0);
  const publicCalls = publicAccess.mock.callCount();

  const adminOnly = { ...env, MACROPULSE_TURNSTILE_SECRET: undefined };
  for (let count = 0; count < 7; count++) {
    const result = await worker.fetch(adminPost(credential), adminOnly);
    assert.equal(result.status, 200);
    assert.deepEqual(await result.json(), hybridResult);
  }
  assert.equal(verified, 2);
  assert.equal(generated, 8);
  assert.equal(publicAccess.mock.callCount(), publicCalls);
  assert.equal(limits.get().count, 1);
  assert.equal(publicQuota.db.prepare('SELECT total FROM monthly_reservations').get().total, 1000);
  assert.equal(publicQuota.db.prepare('SELECT count(*) AS count FROM tokens').get().count, 2);
  assert.equal(publicQuota.db.prepare('SELECT count(*) AS count FROM leases').get().count, 0);

  for (const [request, status] of [
    [adminPost(credential, { headers: { Origin: 'https://forged.example' } }), 403],
    [adminPost(credential, { method: 'GET', body: undefined }), 405],
    [adminPost(credential, { headers: { 'Content-Type': 'text/plain' } }), 415],
    [adminPost(credential, { body: 'x'.repeat(4097) }), 413],
    [adminPost(credential, { body: JSON.stringify({ ...payload, secret: credential }) }), 422],
    [adminPost(credential, { body: JSON.stringify({ ...payload, question: credential }) }), 422],
    [post('secret-in-question', { body: JSON.stringify({ ...payload, question: credential }) }), 422],
    [adminPost(credential, { check: true, body: JSON.stringify(payload) }), 422],
  ]) assert.equal((await worker.fetch(request, env)).status, status);
  assert.equal((await worker.fetch(adminPost(credential), { ...env, MACROPULSE_RESEARCH_ENABLED: 'false' })).status, 503);
  assert.equal((await worker.fetch(adminPost(credential), { ...env, MARKETPULSE_DEMO_ORIGIN_SECRET: '' })).status, 503);
  assert.equal(generated, 8);
});

test('invalid administrator credentials fail closed and lock the visitor until expiry', async (t) => {
  const { env, adminAuth, publicAccess } = adminFixture(t);
  const credential = env.MACROPULSE_ADMIN_SECRET;
  const wrong = digest('wrong synthetic administrator credential');
  const fetch = t.mock.method(globalThis, 'fetch', async () => { throw new Error('Unexpected fetch'); });
  let now = Date.UTC(2026, 10, 12);
  t.mock.method(Date, 'now', () => now);

  for (const disabled of [undefined, 'a'.repeat(64), secret, env.MACROPULSE_RESEARCH_HASH_KEY]) {
    const result = await worker.fetch(adminPost(credential), { ...env, MACROPULSE_ADMIN_SECRET: disabled });
    assert.equal(result.status, 401);
    assert.deepEqual(await result.json(), { detail: 'Administrator credentials rejected' });
  }
  assert.equal((await worker.fetch(adminPost(undefined, { check: true }), env)).status, 401);
  for (let count = 0; count < 4; count++) {
    assert.equal((await worker.fetch(adminPost(wrong, { headers: { 'X-MacroPulse-Turnstile': 'would-fall-back' } }), env)).status, 401);
  }
  const limited = await worker.fetch(adminPost(credential, { check: true }), env);
  assert.equal(limited.status, 429);
  assert.equal(limited.headers.get('Retry-After'), '60');
  assert.equal((await limited.json()).code, 'admin_auth_limit');
  assert.equal((await worker.fetch(adminPost(credential, {
    check: true, headers: { 'CF-Connecting-IP': '192.0.2.2' },
  }), env)).status, 204);
  assert.equal(adminAuth.db.prepare('SELECT count(*) AS count FROM admin_failures').get().count, 1);
  now += minute;
  await adminAuth.instance().alarm();
  assert.equal(adminAuth.db.prepare('SELECT count(*) AS count FROM admin_failures').get().count, 0);
  assert.equal((await worker.fetch(adminPost(credential, { check: true }), env)).status, 204);
  assert.equal(fetch.mock.callCount(), 0);
  assert.equal(publicAccess.mock.callCount(), 0);
});

test('early limiter blocks bursts before Siteverify, isolates IPs and tolerates legitimate retries', async (t) => {
  for (const method of ['info', 'warn']) t.mock.method(console, method, () => {});
  const config = JSON.parse(readFileSync(new URL('../wrangler.jsonc', import.meta.url), 'utf8'));
  const binding = config.ratelimits.find(({ name }) => name === 'RESEARCH_ATTEMPT_RATE_LIMIT');
  assert.deepEqual(binding.simple, { limit: 30, period: 60 });
  const env = fixtureEnv(sqliteFixture(t).instance());
  let now = Date.UTC(2026, 10, 14);
  t.mock.method(Date, 'now', () => now);
  const counts = new Map();
  // Deterministic native-binding stub, NOT a claim of strict Cloudflare enforcement.
  env.RESEARCH_ATTEMPT_RATE_LIMIT = { limit: async ({ key }) => {
    const bucket = `${Math.floor(now / minute)}:${key}`;
    const count = (counts.get(bucket) ?? 0) + 1;
    counts.set(bucket, count);
    return { success: count <= binding.simple.limit };
  } };
  let accepted = false;
  let verifies = 0;
  let origins = 0;
  t.mock.method(globalThis, 'fetch', async (url) => {
    if (String(url).includes('siteverify')) {
      verifies++;
      return Response.json({ success: accepted, hostname: 'demo.example', action: 'research',
        challenge_ts: new Date(now).toISOString() });
    }
    origins++;
    return Response.json({}, { headers: { 'X-MacroPulse-Research-Complete': 'true' } });
  });
  assert.equal((await worker.fetch(post('valid', { body: '{"question":false}' }), env)).status, 422);
  assert.equal(verifies, 0);
  counts.clear();
  const burst = await Promise.all(Array.from({ length: 31 }, (_, index) => worker.fetch(post(`token-${index}`, {
    headers: { 'X-Forwarded-For': `192.0.2.${index + 2}`, 'X-MarketPulse-Client-IP': `192.0.2.${index + 2}` },
  }), env)));
  assert.equal(burst.filter(({ status }) => status === 403).length, 30);
  assert.equal(burst.filter(({ status }) => status === 429).length, 1);
  assert.equal(burst.find(({ status }) => status === 429).headers.get('Retry-After'), '60');
  assert.equal(verifies, 30);
  assert.equal(origins, 0);
  accepted = true;
  assert.equal((await worker.fetch(post('other-ip', { headers: { 'CF-Connecting-IP': '192.0.2.2' } }), env)).status, 200);
  now += minute;
  for (let attempt = 0; attempt < 3; attempt++) {
    assert.equal((await worker.fetch(post(`legitimate-${attempt}`), env)).status, 200);
  }
  assert.equal(origins, 4);
});

test('missing or failed early protection and untrusted forwarding headers cannot bypass admission', async (t) => {
  for (const method of ['info', 'warn']) t.mock.method(console, method, () => {});
  const env = fixtureEnv(sqliteFixture(t).instance());
  const fetch = t.mock.method(globalThis, 'fetch', () => assert.fail('No external verification or origin request'));
  for (const binding of [undefined, {}, { limit: async () => ({}) },
    { limit: async () => { throw new Error('Unavailable'); } }]) {
    assert.equal((await worker.fetch(post('token'), { ...env, RESEARCH_ATTEMPT_RATE_LIMIT: binding })).status, 503);
  }
  assert.equal((await worker.fetch(post('token', { headers: {
    'CF-Connecting-IP': '', 'X-Forwarded-For': '192.0.2.1', 'X-MarketPulse-Client-IP': '192.0.2.1',
  } }), env)).status, 503);
  assert.equal(fetch.mock.callCount(), 0);
});

test('catalog does not advertise public Research without required bindings', async (t) => {
  const env = fixtureEnv(sqliteFixture(t).instance());
  t.mock.method(globalThis, 'fetch', async () => Response.json([], {
    headers: { 'X-MacroPulse-Public-Research': 'true' },
  }));
  for (const overrides of [{ RESEARCH_ATTEMPT_RATE_LIMIT: undefined }, { RESEARCH_ATTEMPT_RATE_LIMIT: {} },
    { RESEARCH_QUOTA: undefined }]) {
    const reply = await worker.fetch(edgeRequest(catalog), { ...env, ...overrides });
    assert.equal(reply.status, 200);
    assert.equal(reply.headers.get('X-MacroPulse-Turnstile-Sitekey'), null);
  }
});

test('administrator reservations enforce atomic concurrency, rolling/day/month caps and persist across restart', async (t) => {
  const fixture = sqliteFixture(t);
  let quota = fixture.instance();
  let now = Date.UTC(2026, 10, 14);
  t.mock.method(Date, 'now', () => now);
  let token = 0;
  const admit = async () => (await internal(quota, '/admin-admit', {
    visitor: '0'.repeat(64), token: digest(`admin-${token++}`), challengeTime: now,
  })).json();
  const burst = await Promise.all([admit(), admit(), admit()]);
  assert.equal(burst.filter(({ code }) => code === 'ok').length, 1);
  assert.equal(burst.filter(({ code }) => code === 'busy').length, 2);
  quota = fixture.instance();
  assert.equal((await admit()).code, 'busy');
  now += 90_000; // uncertain completion retains a lease until expiry
  const next = await admit();
  assert.equal(next.code, 'ok');
  await internal(quota, '/release', { id: next.id });
  const reserveAndRelease = async () => {
    const result = await admit();
    if (result.id) await internal(quota, '/release', { id: result.id });
    return result;
  };
  for (let i = 2; i < 20; i++) assert.equal((await reserveAndRelease()).code, 'ok');
  assert.equal((await reserveAndRelease()).code, 'quota');
  now += 10 * minute;
  for (let i = 20; i < 50; i++) {
    if (i === 40) now += 10 * minute;
    assert.equal((await reserveAndRelease()).code, 'ok');
  }
  assert.equal((await reserveAndRelease()).code, 'quota');
  for (let dayIndex = 1; dayIndex < 6; dayIndex++) {
    now = Date.UTC(2026, 10, 14 + dayIndex);
    quota = fixture.instance();
    for (let i = 0; i < 50; i++) {
      if (i && i % 20 === 0) now += 10 * minute;
      assert.equal((await reserveAndRelease()).code, 'ok');
    }
  }
  now += day;
  quota = fixture.instance();
  assert.deepEqual(await admit(), { code: 'budget', retry: (Date.UTC(2026, 11, 1) - now) / 1000 });
  now = Date.UTC(2026, 11, 1);
  assert.equal((await admit()).code, 'ok');
});

test('a valid leaked admin credential cannot bypass its shared budget by changing IP and cannot consume public quota', async (t) => {
  for (const method of ['info', 'warn']) t.mock.method(console, method, () => {});
  const { env, adminBudget, publicQuota } = adminFixture(t);
  const now = Date.UTC(2026, 10, 14);
  t.mock.method(Date, 'now', () => now);
  adminBudget.db.prepare('INSERT INTO monthly_reservations VALUES (?, ?)').run(Date.UTC(2026, 10, 1), 299);
  let origins = 0;
  t.mock.method(globalThis, 'fetch', async (url) => {
    if (String(url).includes('siteverify')) return Response.json({ success: true, hostname: 'demo.example',
      action: 'research', challenge_ts: new Date(now).toISOString() });
    origins++;
    return Response.json({}, { headers: { 'X-MacroPulse-Research-Complete': 'true' } });
  });
  assert.equal((await worker.fetch(adminPost(env.MACROPULSE_ADMIN_SECRET, { body: '{}' }), env)).status, 422);
  assert.equal(adminBudget.db.prepare('SELECT total FROM monthly_reservations').get().total, 299);
  assert.equal((await worker.fetch(adminPost(env.MACROPULSE_ADMIN_SECRET), env)).status, 200);
  for (const ip of ['192.0.2.1', '192.0.2.2', '2001:db8::1']) {
    const reply = await worker.fetch(adminPost(env.MACROPULSE_ADMIN_SECRET, { headers: { 'CF-Connecting-IP': ip } }), env);
    assert.equal(reply.status, 429);
    assert.equal(reply.headers.get('Retry-After'), String((Date.UTC(2026, 11, 1) - now) / 1000));
    assert.equal((await reply.json()).code, 'admin_research_limit');
  }
  assert.equal(origins, 1);
  assert.equal((await worker.fetch(adminPost(env.MACROPULSE_ADMIN_SECRET, { check: true }), env)).status, 204);
  assert.equal(publicQuota.db.prepare('SELECT count(*) AS count FROM attempts').get().count, 0);
  assert.equal((await worker.fetch(post('public-still-works'), env)).status, 200);
  assert.equal(origins, 2);
});

test('Research never uses cache and admin budget failures fail closed', async (t) => {
  for (const method of ['info', 'warn']) t.mock.method(console, method, () => {});
  const { env } = adminFixture(t);
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'caches');
  const cacheAccess = t.mock.fn(() => { throw new Error('Unexpected cache access'); });
  Object.defineProperty(globalThis, 'caches', { configurable: true, get: cacheAccess });
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, 'caches', previous);
    else delete globalThis.caches;
  });
  const origin = t.mock.method(globalThis, 'fetch', async (url, init) => {
    if (String(url).includes('siteverify')) return Response.json({ success: true, hostname: 'demo.example',
      action: 'research', challenge_ts: new Date().toISOString() });
    assert.equal(init.cache, 'no-store');
    return Response.json({}, { headers: { 'X-MacroPulse-Research-Complete': 'true' } });
  });
  const publicReply = await worker.fetch(post('public-token'), env);
  assert.equal(publicReply.status, 200);
  assert.equal(publicReply.headers.get('Cache-Control'), 'no-store');
  assert.equal((await worker.fetch(adminPost(env.MACROPULSE_ADMIN_SECRET), env)).status, 200);
  const get = env.RESEARCH_QUOTA.getByName;
  env.RESEARCH_QUOTA.getByName = (name) => name.endsWith('budget-v1')
    ? { fetch: async () => { throw new Error('Unavailable'); } } : get(name);
  assert.equal((await worker.fetch(adminPost(env.MACROPULSE_ADMIN_SECRET), env)).status, 503);
  assert.equal(origin.mock.callCount(), 3);
  assert.equal(cacheAccess.mock.callCount(), 0);
});
