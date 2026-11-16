import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, rmdir, unlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { test } from 'node:test';
import { ResearchQuota } from './research-quota.mjs';

const hex = (value) => createHash('sha256').update(value).digest('hex');
const start = Date.UTC(2026, 10, 12);
const missingId = '48fe9360-ef69-4279-9b87-8af100f589c2';

async function persistedQuota(t) {
  const directory = await mkdtemp(join(tmpdir(), 'macropulse-quota-'));
  const path = join(directory, 'quota.sqlite');
  let db;
  let alarm = null;
  let rollback = false;
  t.after(async () => {
    db?.close();
    await unlink(path);
    await rmdir(directory);
  });
  const storage = {
    sql: { exec(query, ...args) {
      if (query.trim().startsWith('CREATE TABLE')) return db.exec(query);
      const rows = db.prepare(query).all(...args);
      return { toArray: () => rows, one: () => rows[0] };
    } },
    transactionSync(action) {
      db.exec('BEGIN IMMEDIATE');
      try {
        const result = action();
        if (rollback) throw new Error('synthetic write failure');
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
  let instance;
  function restart() {
    db?.close();
    db = new DatabaseSync(path);
    instance = new ResearchQuota({ storage });
  }
  restart();
  return {
    restart,
    failWrites(enabled) { rollback = enabled; },
    request(path, payload) {
      return instance.fetch(new Request('https://quota.test' + path, {
        method: 'POST', body: JSON.stringify(payload),
      }));
    },
    grantCount: () => db.prepare('SELECT count(*) AS total FROM origin_grants').get().total,
    alarm: () => instance.alarm(),
  };
}

async function reserve(quota, now, token, path = '/admit') {
  return (await quota.request(path, { visitor: hex('visitor'), token: hex(token), challengeTime: now })).json();
}

function grant({ id, expires }, now) {
  return { id, expires, issuedAt: now, commitment: hex('request commitment') };
}

test('a lease binds once and authorizes exactly one concurrent consumer across SQLite reopen', async (t) => {
  t.mock.method(Date, 'now', () => start);
  const quota = await persistedQuota(t);
  const lease = await reserve(quota, start, 'first');
  assert.equal(lease.code, 'ok');
  const payload = grant(lease, start);

  assert.equal((await quota.request('/consume-origin', payload)).status, 403);
  assert.equal((await quota.request('/bind-origin', { ...payload, id: missingId })).status, 403);
  assert.equal((await quota.request('/bind-origin', payload)).status, 204);
  assert.equal((await quota.request('/bind-origin', { ...payload, commitment: hex('different') })).status, 403);
  assert.equal((await quota.request('/consume-origin', { ...payload, commitment: hex('different') })).status, 403);
  assert.equal((await quota.request('/consume-origin', { ...payload, expires: payload.expires - 1 })).status, 403);
  assert.equal((await quota.request('/consume-origin', { ...payload, issuedAt: start + 1 })).status, 403);
  quota.restart();
  const concurrent = await Promise.all(Array.from({ length: 12 }, () => quota.request('/consume-origin', payload)));
  assert.equal(concurrent.filter((response) => response.status === 204).length, 1);
  assert.equal(concurrent.filter((response) => response.status === 403).length, 11);
  quota.restart();
  assert.equal((await quota.request('/consume-origin', payload)).status, 403);
  assert.equal((await reserve(quota, start, 'second')).code, 'ok');
  assert.equal((await reserve(quota, start, 'third')).code, 'busy');
  assert.equal((await quota.request('/release', { id: payload.id })).status, 204);
  assert.equal(quota.grantCount(), 0);
  assert.equal((await quota.request('/consume-origin', payload)).status, 403);
  assert.equal((await quota.request('/bind-origin', payload)).status, 403);
});

test('commit failure rolls back, response loss cannot reactivate a grant, and expiry cleans storage', async (t) => {
  let now = start;
  t.mock.method(Date, 'now', () => now);
  const quota = await persistedQuota(t);
  const payload = grant(await reserve(quota, now, 'admin', '/admin-admit'), now);
  quota.failWrites(true);
  await assert.rejects(quota.request('/bind-origin', payload), /synthetic write failure/);
  quota.failWrites(false);
  assert.equal(quota.grantCount(), 0);
  assert.equal((await quota.request('/bind-origin', payload)).status, 204);
  quota.failWrites(true);
  await assert.rejects(quota.request('/consume-origin', payload), /synthetic write failure/);
  quota.failWrites(false);
  quota.restart();
  // Discard the success response as if the callback connection were lost.
  await quota.request('/consume-origin', payload);
  quota.restart();
  assert.equal((await quota.request('/consume-origin', payload)).status, 403);
  assert.equal((await reserve(quota, now, 'another-admin', '/admin-admit')).code, 'busy');

  now = payload.expires;
  assert.equal((await quota.request('/consume-origin', { ...payload, issuedAt: now - 1 })).status, 403);
  await quota.alarm();
  assert.equal(quota.grantCount(), 0);
  assert.equal((await reserve(quota, now, 'expired-admin', '/admin-admit')).code, 'ok');
});

test('a released, stale, invalid or wrong-instance grant cannot be consumed', async (t) => {
  let now = start;
  t.mock.method(Date, 'now', () => now);
  const quota = await persistedQuota(t);
  const other = await persistedQuota(t);
  const payload = grant(await reserve(quota, now, 'public'), now);
  for (const invalid of [null, [], { ...payload, extra: true }, { ...payload, id: '00' }, { ...payload, issuedAt: '0' }]) {
    assert.equal((await quota.request('/bind-origin', invalid)).status, 400);
  }
  assert.equal((await other.request('/bind-origin', payload)).status, 403);
  assert.equal((await quota.request('/bind-origin', { ...payload, issuedAt: now + 2001 })).status, 403);
  assert.equal((await quota.request('/bind-origin', payload)).status, 204);
  now += 10_001;
  assert.equal((await quota.request('/consume-origin', payload)).status, 403);
  const next = grant(await reserve(quota, now, 'new-public'), now);
  assert.equal((await quota.request('/bind-origin', next)).status, 204);
  await quota.request('/release', { id: next.id });
  assert.equal((await quota.request('/consume-origin', next)).status, 403);
  assert.equal(quota.grantCount(), 1);
});
