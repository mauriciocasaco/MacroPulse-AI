import { boundedBody } from './bounded-body.mjs';

export const consumeOriginPath = '/api/internal/research/consume-v1';
const researchPath = '/macro/analysis';
const keyId = /^[a-z0-9][a-z0-9_-]{0,31}$/;
const hexKey = /^[a-f0-9]{64}$/;
const uuid4 = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const epochMs = /^[1-9][0-9]{12}$/;
const hostname = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const encoder = new TextEncoder();
const maxCallbackBody = 192;

function fromHex(value) {
  return Uint8Array.from(value.match(/../g), (byte) => Number.parseInt(byte, 16));
}

function toHex(bytes) {
  return Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

async function sha256(bytes) {
  return toHex(await crypto.subtle.digest('SHA-256', bytes));
}

function parseKeys(raw) {
  if (typeof raw !== 'string' || raw.length > 512) return null;
  try {
    const entries = JSON.parse(raw);
    if (!Array.isArray(entries) || entries.length < 1 || entries.length > 2
      || entries.some((entry) => !Array.isArray(entry) || entry.length !== 2
        || typeof entry[0] !== 'string' || !keyId.test(entry[0])
        || typeof entry[1] !== 'string' || !hexKey.test(entry[1]))
      || new Set(entries.map(([id]) => id)).size !== entries.length
      || new Set(entries.map(([, key]) => key)).size !== entries.length) return null;
    return new Map(entries);
  } catch {
    return null;
  }
}

export function originAttestationConfig(env) {
  const originKeys = parseKeys(env.MACROPULSE_RESEARCH_ORIGIN_KEYS);
  const verifyKeys = parseKeys(env.MACROPULSE_RESEARCH_VERIFY_KEYS);
  const kid = env.MACROPULSE_RESEARCH_ORIGIN_ACTIVE_KID;
  const host = env.MACROPULSE_RESEARCH_HOSTNAME;
  if (!originKeys?.has(kid) || !verifyKeys || typeof host !== 'string' || host.length > 253 || !hostname.test(host)
    || typeof env.RESEARCH_VERIFY_RATE_LIMIT?.limit !== 'function'
    || typeof env.RESEARCH_QUOTA?.getByName !== 'function') return null;
  const material = [...originKeys.values(), ...verifyKeys.values()];
  const legacy = [env.MARKETPULSE_DEMO_ORIGIN_SECRET, env.MACROPULSE_ADMIN_SECRET,
    env.MACROPULSE_RESEARCH_HASH_KEY, env.MACROPULSE_TURNSTILE_SECRET];
  if (new Set(material).size !== material.length || material.some((key) => legacy.includes(key))) return null;
  return { kid, signingKey: originKeys.get(kid), verifyKeys, host };
}

function quotaOrigin(quota, path, grant, signal) {
  return quota.fetch('https://research-quota.internal' + path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(grant), signal,
  });
}

export async function attestResearch(config, quota, { id, expires, scope, researchId, ip, body }) {
  const { kid, signingKey } = config;
  const bodyHash = await sha256(body);
  const issuedAt = Date.now();
  if (!uuid4.test(id) || !uuid4.test(researchId) || !epochMs.test(String(issuedAt))
    || !epochMs.test(String(expires)) || expires <= issuedAt) throw new Error('Invalid Research lease');
  const canonical = encoder.encode([
    'macropulse/render-research/v1', kid, 'POST', researchPath, '', 'application/json',
    bodyHash, issuedAt, id, scope, expires, researchId, ip,
  ].join('\n'));
  const commitment = await sha256(canonical);
  const signal = AbortSignal.timeout(2500);
  const bound = await quotaOrigin(quota, '/bind-origin', { id, commitment, issuedAt, expires }, signal);
  signal.throwIfAborted();
  if (bound.status !== 204) throw new Error('Research lease binding failed');

  const key = await crypto.subtle.importKey('raw', fromHex(signingKey),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const signature = toHex(await crypto.subtle.sign('HMAC', key, canonical));
  return {
    'Content-Type': 'application/json',
    'X-MacroPulse-Origin-Key-Id': kid,
    'X-MacroPulse-Origin-Timestamp': String(issuedAt),
    'X-MacroPulse-Origin-Nonce': id,
    'X-MacroPulse-Origin-Scope': scope,
    'X-MacroPulse-Origin-Signature': signature,
    'X-MacroPulse-Research-Expires': String(expires),
    'X-MacroPulse-Research-Id': researchId,
    'X-MarketPulse-Client-IP': ip,
  };
}

function callbackResponse(status) {
  return new Response(null, { status, headers: { 'Cache-Control': 'no-store' } });
}

async function readGrant(request) {
  const signal = AbortSignal.timeout(500);
  const bytes = await boundedBody(request.body, maxCallbackBody, signal);
  signal.throwIfAborted();
  if (!bytes) return { status: 413 };
  if (bytes.some((byte) => byte > 127)) return { status: 400 };
  const fields = new TextDecoder().decode(bytes).split('\n');
  const [scope, id, commitment, timestamp, expiration] = fields;
  if (fields.length !== 5 || (scope !== 'public' && scope !== 'admin') || !uuid4.test(id)
    || !hexKey.test(commitment) || !epochMs.test(timestamp) || !epochMs.test(expiration)) return { status: 400 };
  const issuedAt = Number(timestamp);
  const expires = Number(expiration);
  const now = Date.now();
  if (issuedAt >= expires || issuedAt < now - 10_000 || issuedAt > now + 2_000 || expires <= now) {
    return { status: 403 };
  }
  return { scope, grant: { id, commitment, issuedAt, expires } };
}

export async function consumeOrigin(request, env, url) {
  if (url.protocol !== 'https:' || url.port || url.hostname !== env.MACROPULSE_RESEARCH_HOSTNAME
    || request.headers.has('Origin')) return callbackResponse(403);
  if (request.method !== 'POST') return callbackResponse(405);
  if (url.search) return callbackResponse(400);

  const config = originAttestationConfig(env);
  const ip = request.headers.get('CF-Connecting-IP');
  if (!config || !ip || !/^[0-9a-fA-F:.]{1,45}$/.test(ip)) return callbackResponse(503);
  try {
    const limiter = env.RESEARCH_VERIFY_RATE_LIMIT;
    const ipLimit = await limiter.limit({ key: 'ip:' + ip });
    if (ipLimit?.success !== true) return callbackResponse(ipLimit?.success === false ? 429 : 503);

    const kid = request.headers.get('X-MacroPulse-Verify-Key-Id');
    const bearer = /^Bearer ([a-f0-9]{64})$/.exec(request.headers.get('Authorization') ?? '');
    const key = config.verifyKeys.get(kid);
    if (!key || !bearer || !crypto.subtle.timingSafeEqual(fromHex(key), fromHex(bearer[1]))) {
      return callbackResponse(403);
    }
    const keyLimit = await limiter.limit({ key: 'kid:' + kid });
    if (keyLimit?.success !== true) return callbackResponse(keyLimit?.success === false ? 429 : 503);
    if (request.headers.get('Content-Type') !== 'text/plain' || request.headers.has('Content-Encoding')) {
      return callbackResponse(415);
    }
    const length = request.headers.get('Content-Length');
    if (length !== null && !/^[0-9]{1,10}$/.test(length)) return callbackResponse(400);
    if (Number(length) > maxCallbackBody) return callbackResponse(413);

    const { scope, grant, status } = await readGrant(request);
    if (status) return callbackResponse(status);
    const name = scope === 'public' ? 'macropulse-research-global-v1' : 'macropulse-research-admin-budget-v1';
    const signal = AbortSignal.timeout(1500);
    const verdict = await quotaOrigin(env.RESEARCH_QUOTA.getByName(name), '/consume-origin', grant, signal);
    signal.throwIfAborted();
    return callbackResponse([204, 403].includes(verdict.status) ? verdict.status : 503);
  } catch {
    return callbackResponse(503);
  }
}
