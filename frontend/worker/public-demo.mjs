import { boundedBody } from './bounded-body.mjs';
import {
  maxResearchBody, publicResearchHeader, quotaReady, readResearchPayload, releaseResearch,
  researchConfig, reserveResearch, siteKeyHeader,
} from './research.mjs';

export { ResearchQuota } from './research-quota.mjs';

const maxResponse = 3 * 1024 * 1024;
const macroHistory = /^\/api\/macro\/indicators\/(?:eur-usd|m3-stock|deposit-rate|real-gdp-qoq|hicp-yoy)\/history$/;
const date = /^\d{4}-\d{2}-\d{2}$/;
const maxMacroDays = 3660;
const dayMs = 86_400_000;
const allowedStatuses = new Set([200, 400, 404, 405, 408, 413, 415, 422, 429, 503, 504]);

function error(status, detail, retry) {
  return Response.json({ detail }, {
    status,
    headers: { 'Cache-Control': 'no-store', ...(retry && { 'Retry-After': String(retry) }) },
  });
}

function origin(env) {
  const raw = env.MARKETPULSE_DEMO_ORIGIN_URL ?? '';
  const secret = env.MARKETPULSE_DEMO_ORIGIN_SECRET ?? '';
  if (!/^https:\/\/[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.onrender\.com\/?$/.test(raw)
    || !/^[a-f0-9]{64}$/.test(secret) || new Set(secret).size < 12 || /^(.{1,32})\1+$/.test(secret)) return null;
  return new URL(raw);
}

function macroWindow(query) {
  const start = query.get('start');
  const end = query.get('end');
  const first = Date.parse(start);
  const last = Date.parse(end);
  return Number.isFinite(first) && Number.isFinite(last)
    && new Date(first).toISOString().slice(0, 10) === start
    && new Date(last).toISOString().slice(0, 10) === end
    && last >= first && last - first <= maxMacroDays * dayMs;
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname !== '/api' && !url.pathname.startsWith('/api/')) return env.ASSETS.fetch(request);
    if (url.protocol !== 'https:') return error(403, 'Forbidden');
    const macroEnabled = env.MARKETPULSE_DEMO_MACRO_ENABLED === 'true';
    const macroCatalog = macroEnabled && url.pathname === '/api/macro/indicators';
    const macro = macroEnabled && macroHistory.test(url.pathname);
    const catalog = macroCatalog;
    if (url.pathname === '/api/macro/analysis') {
      return macroEnabled ? publicResearch(request, env, ctx, url) : error(503, 'Public Research is unavailable');
    }
    if (!catalog && !macro) return error(404, 'Not found');
    if (request.method !== 'GET') return error(405, 'Method not allowed');
    const originHeader = request.headers.get('Origin');
    if ((macroCatalog || macro) && originHeader !== null && originHeader !== url.origin) {
      return error(403, 'Forbidden');
    }
    const historical = macro;
    if (url.search.length > 257 || (!historical && url.search)) return error(400, 'Invalid request');
    if (historical && (url.searchParams.size !== 2 || !date.test(url.searchParams.get('start') ?? '')
      || !date.test(url.searchParams.get('end') ?? ''))) {
      return error(422, 'Invalid request parameters');
    }
    if (macro && !macroWindow(url.searchParams)) return error(422, 'Invalid request parameters');
    if (request.body) return error(413, 'Request too large');

    const base = origin(env);
    const ip = request.headers.get('CF-Connecting-IP');
    const limiter = catalog ? env.CATALOG_RATE_LIMIT : env.HISTORY_RATE_LIMIT;
    if (!base || !ip || !/^[0-9a-fA-F:.]{1,45}$/.test(ip) || !limiter) {
      return error(503, 'MacroPulse service unavailable');
    }
    let timeout;
    try {
      if (!(await limiter.limit({ key: ip })).success) return error(429, 'Rate limit exceeded', 60);
      const target = new URL(base);
      target.pathname = url.pathname.slice(4);
      target.search = url.search;
      timeout = AbortSignal.timeout(20_000);
      const upstream = await fetch(target, {
        headers: {
          Accept: 'application/json',
          'X-MarketPulse-Origin': env.MARKETPULSE_DEMO_ORIGIN_SECRET,
          'X-MarketPulse-Client-IP': ip,
        },
        cache: 'no-store',
        redirect: 'manual',
        signal: timeout,
      });
      const { status, headers } = upstream;
      if (!allowedStatuses.has(status) || !/^application\/json(?:;|$)/i.test(headers.get('Content-Type') ?? '')
        || Number(headers.get('Content-Length')) > maxResponse) {
        void upstream.body?.cancel().catch(() => {});
        return error(503, 'MacroPulse service unavailable');
      }
      if (status !== 200) {
        void upstream.body?.cancel().catch(() => {});
        const retry = headers.get('Retry-After');
        if (status === 429) return error(429, 'Rate limit exceeded',
          /^[1-9]\d?$/.test(retry ?? '') && Number(retry) <= 60 ? retry : 60);
        return error(status, status === 503 ? 'MacroPulse service unavailable' : 'Invalid request');
      }
      const body = await boundedBody(upstream.body, maxResponse, timeout);
      if (!body) return error(503, 'MacroPulse service unavailable');
      const responseHeaders = new Headers({
        'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
      });
      if (catalog && headers.get(publicResearchHeader) === 'true') {
        const config = researchConfig(env, url);
        if (config && await quotaReady(config)) responseHeaders.set(siteKeyHeader, config.sitekey);
      }
      return new Response(body, { status, headers: responseHeaders });
    } catch (cause) {
      if (timeout?.aborted || cause?.name === 'TimeoutError') return error(504, 'MacroPulse request timed out');
      return error(503, 'MacroPulse service unavailable');
    }
  },
};


const researchErrors = {
  challenge: [403, 'Complete a new Turnstile check'],
  quota: [429, 'Public Research quota reached'],
  budget: [429, 'Public Research budget reached'],
  busy: [503, 'Public Research is busy'],
};

async function publicResearch(request, env, ctx, url) {
  if (request.method !== 'POST') return error(405, 'Method not allowed');
  if (request.headers.get('Origin') !== url.origin) return error(403, 'Forbidden');
  if (url.search) return error(400, 'Invalid request');
  if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get('Content-Type') ?? '')
    || request.headers.has('Content-Encoding')) return error(415, 'Expected JSON');
  const length = request.headers.get('Content-Length');
  if (length !== null && !/^\d{1,10}$/.test(length)) return error(400, 'Invalid request');
  if (Number(length) > maxResearchBody) return error(413, 'Request too large');
  const base = origin(env);
  const ip = request.headers.get('CF-Connecting-IP');
  let timeout;
  try {
    const config = researchConfig(env, url);
    if (!config || !base || !ip || !/^[0-9a-fA-F:.]{1,45}$/.test(ip)) {
      return error(503, 'Public Research is unavailable');
    }
    const bytes = await boundedBody(request.body, maxResearchBody, AbortSignal.timeout(3000));
    if (!bytes) return error(413, 'Request too large');
    let body;
    try {
      body = readResearchPayload(bytes);
    } catch {
      return error(422, 'Invalid request parameters');
    }
    const reservation = await reserveResearch(config, request.headers.get('X-MacroPulse-Turnstile'), ip);
    if (reservation.code !== 'ok') {
      const reason = Object.hasOwn(researchErrors, reservation.code) && researchErrors[reservation.code];
      return reason ? error(...reason, reservation.retry) : error(503, 'Public Research is unavailable');
    }
    const { id, expires } = reservation;
    if (!/^[a-f0-9-]{36}$/.test(id ?? '') || !Number.isSafeInteger(expires)
      || expires - Date.now() < 30_000 || expires - Date.now() > 60_000) {
      return error(503, 'Public Research is unavailable');
    }
    timeout = AbortSignal.timeout(28_000);
    const upstream = await fetch(new URL('/macro/analysis', base), {
      method: 'POST', body, redirect: 'manual', cache: 'no-store', signal: timeout,
      headers: {
        Accept: 'application/json', 'Content-Type': 'application/json',
        'X-MarketPulse-Origin': env.MARKETPULSE_DEMO_ORIGIN_SECRET,
        'X-MarketPulse-Client-IP': ip,
        'X-MacroPulse-Research-Expires': String(expires),
      },
    });
    const knownCompleted = [200, 400, 403, 405, 408, 413, 415, 422].includes(upstream.status)
      && upstream.headers.get('X-MacroPulse-Research-Complete') === 'true';
    if (knownCompleted) {
      const release = releaseResearch(config, id).catch(() => {});
      if (ctx) ctx.waitUntil(release);
      else await release;
    }
    if (upstream.status !== 200) {
      void upstream.body?.cancel().catch(() => {});
      if (upstream.status === 504) return error(504, 'Public Research timed out');
      if (upstream.status === 422) return error(422, 'Invalid request parameters');
      if (upstream.status === 503 && upstream.headers.get('Retry-After') === '1') {
        return error(503, 'Public Research is busy', 1);
      }
      return error(503, 'Public Research is unavailable');
    }
    if (!knownCompleted || !/^application\/json(?:;|$)/i.test(upstream.headers.get('Content-Type') ?? '')
      || Number(upstream.headers.get('Content-Length')) > 65_536) {
      void upstream.body?.cancel().catch(() => {});
      return error(503, 'Public Research is unavailable');
    }
    const result = await boundedBody(upstream.body, 65_536, timeout);
    if (!result) return error(503, 'Public Research is unavailable');
    return new Response(result, {
      headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' },
    });
  } catch (cause) {
    if (timeout?.aborted || cause?.name === 'TimeoutError') return error(504, 'Public Research timed out');
    return error(503, 'Public Research is unavailable');
  }
}
