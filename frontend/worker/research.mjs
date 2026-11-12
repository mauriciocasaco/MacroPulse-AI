import { boundedBody } from './bounded-body.mjs';

const eventWindows = new Map([
  ['covid', ['2020-02-01', '2020-12-31']],
  ['ukraine', ['2022-02-01', '2024-02-29']],
  ['generative-ai', ['2023-09-01', '2026-09-30']],
]);
const indicators = new Set(['eur-usd', 'm3-stock', 'deposit-rate', 'real-gdp-qoq', 'hicp-yoy']);
const keys = ['question', 'indicator_id', 'event_id', 'research_version', 'start', 'end'];
const tokenPattern = /^[a-zA-Z0-9_.-]{1,2048}$/;
const timePattern = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/;
const encoder = new TextEncoder();

export const publicResearchHeader = 'X-MacroPulse-Public-Research';
export const siteKeyHeader = 'X-MacroPulse-Turnstile-Sitekey';
export const maxResearchBody = 4096;

export function researchConfig(env, url) {
  const hostname = env.MACROPULSE_RESEARCH_HOSTNAME ?? '';
  const sitekey = env.MACROPULSE_TURNSTILE_SITE_KEY ?? '';
  const secret = env.MACROPULSE_TURNSTILE_SECRET ?? '';
  const hashKey = env.MACROPULSE_RESEARCH_HASH_KEY ?? '';
  if (env.MACROPULSE_RESEARCH_ENABLED !== 'true' || url.protocol !== 'https:' || url.port
    || hostname !== url.hostname || !/^[a-z0-9-]+(?:\.[a-z0-9-]+)+$/.test(hostname)
    || !/^[A-Za-z0-9_-]{12,80}$/.test(sitekey) || !/^[A-Za-z0-9_-]{20,160}$/.test(secret)
    || !/^[a-f0-9]{64}$/.test(hashKey) || new Set(hashKey).size < 12
    || /^(.{1,32})\1+$/.test(hashKey) || hashKey === env.MARKETPULSE_DEMO_ORIGIN_SECRET
    || !env.RESEARCH_QUOTA) return null;
  try {
    return {
      hostname, sitekey, secret, hashKey,
      quota: env.RESEARCH_QUOTA.getByName('macropulse-research-global-v1'),
    };
  } catch {
    return null;
  }
}

export function readResearchPayload(bytes) {
  const raw = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  const value = JSON.parse(raw);
  // The schema only allows top-level strings. Count JSON member names
  // lexically as well as parsed keys to reject duplicate member names.
  const entries = Array.from(raw.matchAll(/"(?:[^"\\]|\\.)*"\s*(:)?/gs))
    .filter((match) => match[1]).length;
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).length !== keys.length || entries !== keys.length
    || keys.some((key) => typeof value[key] !== 'string')) throw new Error('Invalid request');
  const window = eventWindows.get(value.event_id);
  const questionLength = Array.from(value.question.trim()).length;
  if (!window || value.start !== window[0] || value.end !== window[1]
    || !indicators.has(value.indicator_id) || value.research_version !== 'v2'
    || questionLength < 5 || Array.from(value.question).length > 500
    || /[\u0000-\u001f]/.test(value.question)) throw new Error('Invalid request');
  return raw;
}

function quotaRequest(quota, path, payload) {
  return quota.fetch(`https://research-quota.internal${path}`, {
    method: 'POST', body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' },
    signal: AbortSignal.timeout(2500),
  });
}

export async function quotaReady(config) {
  try {
    return (await quotaRequest(config.quota, '/ready', {})).status === 204;
  } catch {
    return false;
  }
}

export async function reserveResearch(config, token, visitor) {
  if (!tokenPattern.test(token ?? '')) return { code: 'challenge' };
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
    return { code: 'challenge' };
  }
  const challengeTime = timePattern.test(result.challenge_ts ?? '') ? Date.parse(result.challenge_ts) : NaN;
  if (result.hostname !== config.hostname || result.action !== 'research' || !Number.isSafeInteger(challengeTime)
    || challengeTime > Date.now() || Date.now() - challengeTime >= 300_000) {
    return { code: 'challenge' };
  }
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

export async function releaseResearch(config, id) {
  await quotaRequest(config.quota, '/release', { id });
}
