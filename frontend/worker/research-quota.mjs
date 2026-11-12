const MINUTE = 60_000;
const DAY = 24 * 60 * MINUTE;
const TEN_MINUTES = 10 * MINUTE;
const TOKEN_LIFETIME = 5 * MINUTE;
const LEASE = 60_000;
const MAX_ACTIVE = 2;
// USD 0.10 reserved per admitted investigation, even on timeout or failure.
// This covers 2 × (8k input at $1.50/M + 2.4k output at $7.50/M) = $0.06.
const MAX_MONTHLY_RESERVATIONS = 100;
const HASH = /^[a-f0-9]{64}$/;
const ID = /^[a-f0-9-]{36}$/;

function seconds(ms) {
  return Math.max(1, Math.ceil(ms / 1000));
}

/** All callers address the same instance. SQL changes are committed atomically. */
export class ResearchQuota {
  constructor(ctx) {
    this.storage = ctx.storage;
    this.sql = this.storage.sql;
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS attempts (
        id TEXT PRIMARY KEY, visitor TEXT NOT NULL, admitted INTEGER NOT NULL
      );
      CREATE INDEX IF NOT EXISTS attempts_time ON attempts(admitted);
      CREATE INDEX IF NOT EXISTS attempts_visitor ON attempts(visitor, admitted);
      CREATE TABLE IF NOT EXISTS monthly_reservations (
        month INTEGER PRIMARY KEY, total INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS tokens (
        digest TEXT PRIMARY KEY, expires INTEGER NOT NULL
      );
      CREATE INDEX IF NOT EXISTS token_expiry ON tokens(expires);
      CREATE TABLE IF NOT EXISTS leases (
        id TEXT PRIMARY KEY, expires INTEGER NOT NULL
      );
    `);
  }

  async fetch(request) {
    const path = new URL(request.url).pathname;
    if (request.method !== 'POST') return new Response(null, { status: 405 });
    if (path === '/ready') {
      this.sql.exec('SELECT total FROM monthly_reservations LIMIT 1').toArray();
      return new Response(null, { status: 204 });
    }
    const payload = await request.json();
    if (path === '/release' && ID.test(payload.id ?? '')) {
      this.sql.exec('DELETE FROM leases WHERE id = ?', payload.id);
      return new Response(null, { status: 204 });
    }
    if (path !== '/admit' || !HASH.test(payload.visitor ?? '') || !HASH.test(payload.token ?? '')
      || !Number.isSafeInteger(payload.challengeTime)) {
      return new Response(null, { status: 400 });
    }
    const result = this.storage.transactionSync(() => this.admit(payload, Date.now()));
    if (await this.storage.getAlarm() === null) await this.storage.setAlarm(Date.now() + TOKEN_LIFETIME);
    return Response.json(result);
  }

  async alarm() {
    const now = Date.now();
    this.storage.transactionSync(() => this.clean(now));
    const count = this.sql.exec('SELECT count(*) AS total FROM attempts').one().total;
    const firstToken = this.sql.exec('SELECT min(expires) AS first FROM tokens').one().first;
    const firstLease = this.sql.exec('SELECT min(expires) AS first FROM leases').one().first;
    const day = Math.floor(now / DAY) * DAY;
    const firstAttempt = count > 0 && day + (now < day + TEN_MINUTES ? TEN_MINUTES : DAY + TEN_MINUTES);
    const pending = [firstToken, firstLease, firstAttempt].filter(Boolean);
    if (pending.length) await this.storage.setAlarm(Math.min(...pending));
  }

  clean(now) {
    const day = Math.floor(now / DAY) * DAY;
    this.sql.exec('DELETE FROM leases WHERE expires <= ?', now);
    this.sql.exec('DELETE FROM tokens WHERE expires <= ?', now);
    this.sql.exec('DELETE FROM attempts WHERE admitted < ? AND admitted <= ?', day, now - TEN_MINUTES);
  }

  admit({ visitor, token, challengeTime }, now) {
    const date = new Date(now);
    const day = Math.floor(now / DAY) * DAY;
    const month = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1);
    const nextMonth = Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1);
    const shortWindow = now - TEN_MINUTES;
    const tokenExpires = challengeTime + TOKEN_LIFETIME;
    if (challengeTime > now || tokenExpires <= now) return { code: 'challenge' };
    this.clean(now);
    this.sql.exec('DELETE FROM monthly_reservations WHERE month < ?', month);
    if (this.sql.exec('SELECT count(*) AS total FROM tokens').one().total >= 10_000) {
      return { code: 'busy', retry: 60 };
    }
    if (!this.sql.exec('INSERT OR IGNORE INTO tokens VALUES (?, ?) RETURNING digest', token, tokenExpires).toArray().length) {
      return { code: 'challenge' };
    }
    const local = this.sql.exec(`
      SELECT count(*) FILTER (WHERE admitted > ?) AS recent,
             count(*) FILTER (WHERE admitted >= ?) AS daily,
             min(admitted) FILTER (WHERE admitted > ?) AS oldest
      FROM attempts WHERE visitor = ? AND admitted >= ?
    `, shortWindow, day, shortWindow, visitor, Math.min(day, shortWindow)).one();
    if (local.daily >= 10) return { code: 'quota', retry: seconds(day + DAY - now) };
    if (local.recent >= 3) return { code: 'quota', retry: seconds(local.oldest + TEN_MINUTES - now) };
    const daily = this.sql.exec('SELECT count(*) AS total FROM attempts WHERE admitted >= ?', day).one().total;
    if (daily >= 100) return { code: 'quota', retry: seconds(day + DAY - now) };
    const budget = this.sql.exec('SELECT total FROM monthly_reservations WHERE month = ?', month).toArray()[0];
    if ((budget?.total ?? 0) >= MAX_MONTHLY_RESERVATIONS) {
      return { code: 'budget', retry: seconds(nextMonth - now) };
    }
    const active = this.sql.exec('SELECT count(*) AS total, min(expires) AS first FROM leases').one();
    if (active.total >= MAX_ACTIVE) return { code: 'busy', retry: Math.min(15, seconds(active.first - now)) };
    const id = crypto.randomUUID();
    const expires = now + LEASE;
    this.sql.exec('INSERT INTO attempts VALUES (?, ?, ?)', id, visitor, now);
    this.sql.exec('INSERT INTO leases VALUES (?, ?)', id, expires);
    this.sql.exec(`
      INSERT INTO monthly_reservations VALUES (?, 1)
      ON CONFLICT(month) DO UPDATE SET total = total + 1
    `, month);
    return { code: 'ok', id, expires };
  }
}
