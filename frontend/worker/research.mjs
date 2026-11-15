import { boundedBody } from './bounded-body.mjs';
import { researchErrorType, researchEvent, researchStage } from './research-logging.mjs';

const eventWindows = new Map([
  ['covid', ['2020-02-01', '2020-12-31']],
  ['ukraine', ['2022-02-01', '2024-02-29']],
  ['generative-ai', ['2023-09-01', '2026-09-30']],
]);
const indicators = new Set(['eur-usd', 'm3-stock', 'deposit-rate', 'real-gdp-qoq', 'hicp-yoy']);
const requiredKeys = ['question', 'research_version', 'start', 'end'];
const allowedKeys = new Set([...requiredKeys, 'indicator_id', 'event_id']);
const isoDate = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/;
const maxWindowMs = 3660 * 86_400_000;

function calendarDay(value) {
  const stamp = isoDate.test(value) ? Date.parse(value) : NaN;
  return Number.isFinite(stamp) && !value.startsWith('0000-')
    && new Date(stamp).toISOString().slice(0, 10) === value ? stamp : NaN;
}
const tokenPattern = /^[a-zA-Z0-9_.-]{1,2048}$/;
const timePattern = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/;
const encoder = new TextEncoder();

export const publicResearchHeader = 'X-MacroPulse-Public-Research';
export const siteKeyHeader = 'X-MacroPulse-Turnstile-Sitekey';
export const maxResearchBody = 4096;
export const adminHeader = 'X-MacroPulse-Admin-Secret';
export const adminCheckHeader = 'X-MacroPulse-Admin-Check';
const adminSecretPattern = /^[a-f0-9]{64}$/;

export function researchAttemptLimiter(env) {
  const limiter = env.RESEARCH_ATTEMPT_RATE_LIMIT;
  return typeof limiter?.limit === 'function' ? limiter : null;
}

export function configuredAdminSecret(env) {
  const secret = env.MACROPULSE_ADMIN_SECRET;
  if (typeof secret !== 'string' || !adminSecretPattern.test(secret)
    || new Set(secret).size < 12 || /^(.{1,32})\1+$/.test(secret)
    || secret === env.MARKETPULSE_DEMO_ORIGIN_SECRET || secret === env.MACROPULSE_RESEARCH_HASH_KEY
    || secret === env.MACROPULSE_TURNSTILE_SECRET) return null;
  return secret;
}

export async function authenticateAdmin(env, ip, secret, credential) {
  if (!secret) return { code: 'invalid' };
  // Fixed-size buffers keep the credential comparison in the native crypto implementation.
  const expected = encoder.encode(secret);
  const valid = adminSecretPattern.test(credential)
    && crypto.subtle.timingSafeEqual(expected, encoder.encode(credential));
  const key = await crypto.subtle.importKey('raw', expected, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const hash = await crypto.subtle.sign('HMAC', key, encoder.encode(ip));
  const visitor = Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, '0')).join('');
  // This instance stores only authentication failures. The public admission instance is untouched.
  const auth = env.RESEARCH_QUOTA.getByName('macropulse-research-admin-auth-v1');
  const response = await quotaRequest(auth, '/admin-auth', { visitor, valid });
  if (!response.ok) throw new Error('Administrator verification unavailable');
  return response.json();
}

export function researchEnabled(env, url) {
  const hostname = env.MACROPULSE_RESEARCH_HOSTNAME ?? '';
  return env.MACROPULSE_RESEARCH_ENABLED === 'true' && url.protocol === 'https:' && !url.port
    && hostname === url.hostname && /^[a-z0-9-]+(?:\.[a-z0-9-]+)+$/.test(hostname);
}

export function researchConfig(env, url, researchId = null) {
  const hostname = env.MACROPULSE_RESEARCH_HOSTNAME ?? '';
  const sitekey = env.MACROPULSE_TURNSTILE_SITE_KEY ?? '';
  const secret = env.MACROPULSE_TURNSTILE_SECRET ?? '';
  const hashKey = env.MACROPULSE_RESEARCH_HASH_KEY ?? '';
  if (!researchEnabled(env, url)
    || !/^[A-Za-z0-9_-]{12,80}$/.test(sitekey) || !/^[A-Za-z0-9_-]{20,160}$/.test(secret)
    || !/^[a-f0-9]{64}$/.test(hashKey) || new Set(hashKey).size < 12
    || /^(.{1,32})\1+$/.test(hashKey) || hashKey === env.MARKETPULSE_DEMO_ORIGIN_SECRET
    || !env.RESEARCH_QUOTA || !researchAttemptLimiter(env)) return null;
  const started = performance.now();
  try {
    return {
      hostname, sitekey, secret, hashKey,
      quota: env.RESEARCH_QUOTA.getByName('macropulse-research-global-v1'),
    };
  } catch (cause) {
    if (researchId) researchEvent(researchId, 'worker.quota_binding', started, researchErrorType(cause));
    return null;
  }
}

export function readResearchPayload(bytes, adminSecret) {
  const raw = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  const value = JSON.parse(raw);
  // The schema only allows top-level strings. Count JSON member names
  // lexically as well as parsed keys to reject duplicate member names.
  const entries = Array.from(raw.matchAll(/"(?:[^"\\]|\\.)*"\s*(:)?/gs))
    .filter((match) => match[1]).length;
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).length !== entries
    || requiredKeys.some((key) => !Object.hasOwn(value, key))
    || Object.entries(value).some(([key, field]) => !allowedKeys.has(key) || typeof field !== 'string')) {
    throw new Error('Invalid request');
  }
  const window = eventWindows.get(value.event_id);
  const days = calendarDay(value.end) - calendarDay(value.start);
  const questionLength = Array.from(value.question.trim()).length;
  if (!(days >= 0 && days <= maxWindowMs)
    || (value.event_id !== undefined && (!window || value.start !== window[0] || value.end !== window[1]))
    || (value.indicator_id !== undefined && !indicators.has(value.indicator_id)) || value.research_version !== 'v2'
    || questionLength < 5 || Array.from(value.question).length > 500
    || /[\u0000-\u001f]/.test(value.question)
    || (adminSecret && value.question.includes(adminSecret))) throw new Error('Invalid request');
  return raw;
}

function quotaRequest(quota, path, payload) {
  return quota.fetch(`https://research-quota.internal${path}`, {
    method: 'POST', body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' },
    signal: AbortSignal.timeout(2500),
  });
}

// A fixed identity makes the administrator budget independent of source IP or secret rotation.
// Use a separate instance so public usage and authentication failures cannot exhaust this budget.
export function adminQuotaConfig(env) {
  return { quota: env.RESEARCH_QUOTA.getByName('macropulse-research-admin-budget-v1') };
}

export async function reserveAdminResearch(config) {
  const token = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',
    encoder.encode(crypto.randomUUID()))), (byte) => byte.toString(16).padStart(2, '0')).join('');
  const response = await quotaRequest(config.quota, '/admin-admit', {
    visitor: '0'.repeat(64), token, challengeTime: Date.now(),
  });
  if (!response.ok) throw new Error('Administrator budget unavailable');
  return response.json();
}

export async function quotaReady(config) {
  try {
    return (await quotaRequest(config.quota, '/ready', {})).status === 204;
  } catch {
    return false;
  }
}

async function verifyTurnstile(config, token) {
  const signal = AbortSignal.timeout(4000);
  const reply = await fetch('https://challenges.cloudflare.com/turnstile/v0/siteverify', {
    method: 'POST', redirect: 'manual', signal,
    body: new URLSearchParams({ secret: config.secret, response: token }),
  });
  if (!reply.ok || Number(reply.headers.get('Content-Length')) > 4096) {
    void reply.body?.cancel().catch(() => {});
    throw new Error('Siteverify unavailable');
  }
  const bytes = await boundedBody(reply.body, 4096, signal);
  if (!bytes) throw new Error('Unexpected Siteverify response');
  const result = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  if (result.success !== true) {
    if (result['error-codes']?.some((code) => ['internal-error', 'invalid-input-secret', 'missing-input-secret'].includes(code))) {
      throw new Error('Siteverify unavailable');
    }
    return null;
  }
  const challengeTime = timePattern.test(result.challenge_ts ?? '') ? Date.parse(result.challenge_ts) : NaN;
  if (result.hostname !== config.hostname || result.action !== 'research' || !Number.isSafeInteger(challengeTime)
    || challengeTime > Date.now() || Date.now() - challengeTime >= 300_000) {
    return null;
  }
  return challengeTime;
}

export async function reserveResearch(config, token, visitor, researchId) {
  if (!tokenPattern.test(token ?? '')) return { code: 'challenge' };
  const challengeTime = await researchStage(researchId, 'worker.turnstile', () => verifyTurnstile(config, token));
  if (challengeTime === null) return { code: 'challenge' };
  return researchStage(researchId, 'worker.quota_admit', () => admitResearch(config, token, visitor, challengeTime));
}

async function admitResearch(config, token, visitor, challengeTime) {
  const key = await crypto.subtle.importKey('raw', encoder.encode(config.hashKey), { name: 'HMAC', hash: 'SHA-256' },
    false, ['sign']);
  const digest = async (value) => Array.from(new Uint8Array(await crypto.subtle.sign('HMAC', key, encoder.encode(value))),
    (byte) => byte.toString(16).padStart(2, '0')).join('');
  const response = await quotaRequest(config.quota, '/admit', {
    visitor: await digest(`ip:${visitor}`),
    token: Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode(token))),
      (byte) => byte.toString(16).padStart(2, '0')).join(''),
    challengeTime,
  });
  if (!response.ok) throw new Error('Quota unavailable');
  return response.json();
}

export async function releaseResearch(config, id, researchId) {
  const started = performance.now();
  try {
    const response = await quotaRequest(config.quota, '/release', { id });
    researchEvent(researchId, 'worker.quota_release', started,
      response.ok ? undefined : 'QuotaReleaseHTTPError', response.status);
  } catch (cause) {
    researchEvent(researchId, 'worker.quota_release', started, researchErrorType(cause));
    throw cause;
  }
}
