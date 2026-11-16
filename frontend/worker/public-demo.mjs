import { boundedBody } from './bounded-body.mjs';
import { attestResearch, consumeOrigin, consumeOriginPath, originAttestationConfig } from './origin-attestation.mjs';
import { publicCache, publicCacheHeader, publicCacheVersion, readPublicCache, writePublicCache } from './public-cache.mjs';
import { researchErrorType, researchEvent } from './research-logging.mjs';
import { researchLeaseMs } from './research-quota.mjs';
import {
  adminCheckHeader, adminHeader, adminQuotaConfig, authenticateAdmin, configuredAdminSecret, maxResearchBody,
  publicResearchHeader, quotaReady, readResearchPayload, releaseResearch,
  researchAttemptLimiter, researchConfig, researchEnabled, reserveAdminResearch, reserveResearch, siteKeyHeader,
} from './research.mjs';

export { ResearchQuota } from './research-quota.mjs';

const maxResponse = 3 * 1024 * 1024;
const macroHistory = /^\/api\/macro\/indicators\/(?:eur-usd|m3-stock|deposit-rate|real-gdp-qoq|hicp-yoy)\/history$/;
const date = /^\d{4}-\d{2}-\d{2}$/;
const maxMacroDays = 3660;
const dayMs = 86_400_000;
const allowedStatuses = new Set([200, 400, 404, 405, 408, 413, 415, 422, 429, 503, 504]);

function error(status, detail, retry, code) {
  return Response.json({ detail, ...(code && { code }) }, {
    status,
    headers: { 'Cache-Control': 'no-store', ...(retry && { 'Retry-After': String(retry) }) },
  });
}

function originUrl(env) {
  const raw = env.MARKETPULSE_DEMO_ORIGIN_URL ?? '';
  return /^https:\/\/[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.onrender\.com\/?$/.test(raw) ? new URL(raw) : null;
}

function readOrigin(env) {
  const secret = env.MARKETPULSE_DEMO_ORIGIN_SECRET ?? '';
  if (!/^[a-f0-9]{64}$/.test(secret) || new Set(secret).size < 12 || /^(.{1,32})\1+$/.test(secret)) return null;
  return originUrl(env);
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
    if (url.pathname === consumeOriginPath) return consumeOrigin(request, env, url);
    if (url.pathname !== '/api' && !url.pathname.startsWith('/api/')) return env.ASSETS.fetch(request);
    if (url.protocol !== 'https:') return error(403, 'Forbidden');
    const macroEnabled = env.MARKETPULSE_DEMO_MACRO_ENABLED === 'true';
    const macroCatalog = macroEnabled && url.pathname === '/api/macro/indicators';
    const macro = macroEnabled && macroHistory.test(url.pathname);
    const catalog = macroCatalog;
    if (url.pathname === '/api/macro/analysis') {
      if (macroEnabled) return publicResearch(request, env, ctx, url);
      researchEvent(crypto.randomUUID(), 'worker.configuration', performance.now(), 'MacroDisabled', 503);
      return error(503, 'Public Research is unavailable');
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

    const base = readOrigin(env);
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
      const cacheEntry = await publicCache(request, env, base, catalog);
      let cached = await readPublicCache(cacheEntry);
      const started = Date.now();
      timeout = AbortSignal.timeout(20_000);
      const originRead = (method) => fetch(target, {
        method,
        headers: {
          Accept: 'application/json',
          'X-MarketPulse-Origin': env.MARKETPULSE_DEMO_ORIGIN_SECRET,
          'X-MarketPulse-Client-IP': ip,
        },
        cache: 'no-store',
        redirect: 'manual',
        signal: timeout,
      });
      let upstream = cached && !catalog ? new Response(cached, { headers: { 'Content-Type': 'application/json' } })
        : await originRead(cached ? 'HEAD' : 'GET');
      if (cached && catalog && ([404, 405].includes(upstream.status)
        || (upstream.ok && upstream.headers.get(publicCacheHeader) !== publicCacheVersion))) {
        // An older origin or one that withdrew opt-in must supply a fresh catalog.
        void upstream.body?.cancel().catch(() => {});
        cached = null;
        upstream = await originRead('GET');
      }
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
      const body = cached ?? await boundedBody(upstream.body, maxResponse, timeout);
      if (!body) return error(503, 'MacroPulse service unavailable');
      if (!cached) {
        const write = writePublicCache(cacheEntry, body, headers, started);
        if (ctx) ctx.waitUntil(write);
        else await write;
      }
      const responseHeaders = new Headers({
        'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
      });
      if (catalog && headers.get(publicResearchHeader) === 'true') {
        const config = researchConfig(env, url);
        if (config && originAttestationConfig(env) && await quotaReady(config)) responseHeaders.set(siteKeyHeader, config.sitekey);
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
const researchTimeoutMs = 70_000;
const leaseHeadroomMs = 2_000;

async function publicResearch(request, env, ctx, url) {
  if (request.method !== 'POST') return error(405, 'Method not allowed');
  if (request.headers.get('Origin') !== url.origin) return error(403, 'Forbidden');
  if (url.search) return error(400, 'Invalid request');
  if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get('Content-Type') ?? '')
    || request.headers.has('Content-Encoding')) return error(415, 'Expected JSON');
  const length = request.headers.get('Content-Length');
  if (length !== null && !/^\d{1,10}$/.test(length)) return error(400, 'Invalid request');
  if (Number(length) > maxResearchBody) return error(413, 'Request too large');
  const researchId = crypto.randomUUID();
  const started = performance.now();
  const response = await serveResearch(request, env, ctx, url, researchId);
  const outcome = response.status === 504 ? 'TimeoutError' : response.ok ? undefined : 'ResearchRejected';
  researchEvent(researchId, 'worker.research_total', started, outcome, response.status);
  return response;
}

async function serveResearch(request, env, ctx, url, researchId) {
  let stage = 'worker.configuration';
  let started = performance.now();
  function begin(name) {
    stage = name;
    started = performance.now();
  }
  function record(errorType, status) {
    researchEvent(researchId, stage, started, errorType, status);
  }
  const base = originUrl(env);
  const ip = request.headers.get('CF-Connecting-IP');
  let timeout;
  let config;
  let id;
  let dispatched = false;
  function releaseLease() {
    const release = releaseResearch(config, id, researchId).catch(() => {});
    if (ctx) ctx.waitUntil(release);
    return ctx ? undefined : release;
  }
  try {
    if (!researchEnabled(env, url) || !base || !ip || !/^[0-9a-fA-F:.]{1,45}$/.test(ip)) {
      record('InvalidResearchConfiguration', 503);
      return error(503, 'Public Research is unavailable');
    }
    const credential = request.headers.get(adminHeader);
    const check = request.headers.get(adminCheckHeader);
    const adminRequested = credential !== null || check !== null;
    config = adminRequested ? null : researchConfig(env, url, researchId);
    if (!adminRequested && !config) {
      record('InvalidAdmissionConfiguration', 503);
      return error(503, 'Public Research is unavailable');
    }
    begin('worker.attempt_limit');
    const limiter = researchAttemptLimiter(env);
    if (!limiter) return error(503, 'Public Research is unavailable');
    const attempt = await limiter.limit({ key: `${adminRequested ? 'admin' : 'public'}:${ip}` });
    if (attempt?.success === false) {
      return error(429, 'Research attempt limit reached', 60, 'research_attempt_limit');
    }
    if (attempt?.success !== true) return error(503, 'Public Research is unavailable');
    begin('worker.request_body');
    const bytes = await boundedBody(request.body, maxResearchBody, AbortSignal.timeout(3000));
    if (!bytes) return error(413, 'Request too large');
    const adminSecret = configuredAdminSecret(env);
    let expires;
    if (adminRequested) {
      begin('worker.admin_auth');
      const auth = await authenticateAdmin(env, ip, adminSecret, credential ?? '');
      if (auth.code === 'locked') {
        return error(429, 'Administrator verification temporarily limited', auth.retry, 'admin_auth_limit');
      }
      if (auth.code !== 'ok') return error(401, 'Administrator credentials rejected');
      record();
      if (check !== null) {
        if (check !== 'true' || new TextDecoder().decode(bytes) !== '{}') return error(422, 'Invalid request parameters');
        return new Response(null, { status: 204, headers: { 'Cache-Control': 'no-store' } });
      }
      config = adminQuotaConfig(env);
    }
    const attestation = originAttestationConfig(env);
    if (!attestation) return error(503, 'Public Research is unavailable');
    let body;
    try {
      body = new TextEncoder().encode(readResearchPayload(bytes, adminSecret));
    } catch {
      return error(422, 'Invalid request parameters');
    }
    if (config) {
      begin('worker.admission');
      const reservation = adminRequested ? await reserveAdminResearch(config)
        : await reserveResearch(config, request.headers.get('X-MacroPulse-Turnstile'), ip, researchId);
      if (reservation.code !== 'ok') {
        const reason = Object.hasOwn(researchErrors, reservation.code) && researchErrors[reservation.code];
        if (reservation.code === 'busy') record('ResearchBusy', 503);
        else if (!reason) record('InvalidAdmissionResponse', 503);
        if (!reason) return error(503, 'Public Research is unavailable');
        if (adminRequested && reason[0] === 429) {
          return error(429, 'Administrator research limit reached', reservation.retry, 'admin_research_limit');
        }
        return error(...reason, reservation.retry);
      }
      ({ id, expires } = reservation);
      const remaining = expires - Date.now();
      if (!/^[a-f0-9-]{36}$/.test(id ?? '') || !Number.isSafeInteger(expires)
        || remaining < researchTimeoutMs + leaseHeadroomMs || remaining > researchLeaseMs) {
        record('InvalidResearchLease', 503);
        return error(503, 'Public Research is unavailable');
      }
      record();
    }
    begin('worker.origin_attestation');
    let headers;
    try {
      headers = await attestResearch(attestation, config.quota, {
        id, expires, scope: adminRequested ? 'admin' : 'public', researchId, ip, body,
      });
    } catch (cause) {
      record(researchErrorType(cause), 503);
      return error(503, 'Public Research is unavailable');
    }
    if (expires - Date.now() < researchTimeoutMs + leaseHeadroomMs) {
      record('InvalidResearchLease', 503);
      return error(503, 'Public Research is unavailable');
    }
    record();
    timeout = AbortSignal.timeout(researchTimeoutMs);
    begin('worker.render_fetch');
    dispatched = true;
    const upstream = await fetch(new URL('/macro/analysis', base), {
      method: 'POST', body, redirect: 'manual', cache: 'no-store', signal: timeout,
      headers: { Accept: 'application/json', ...headers },
    });
    if (timeout.aborted) {
      void upstream.body?.cancel().catch(() => {});
      timeout.throwIfAborted();
    }
    record(upstream.status === 200 ? undefined : 'UnexpectedUpstreamStatus', upstream.status);
    const knownCompleted = [200, 400, 403, 405, 408, 413, 415, 422].includes(upstream.status)
      && upstream.headers.get('X-MacroPulse-Research-Complete') === 'true';
    if (knownCompleted) await releaseLease();
    if (upstream.status !== 200) {
      void upstream.body?.cancel().catch(() => {});
      if (upstream.status === 504) return error(504, 'Public Research timed out');
      if (upstream.status === 422) return error(422, 'Invalid request parameters');
      if (upstream.status === 503 && upstream.headers.get('Retry-After') === '1') {
        return error(503, 'Public Research is busy', 1);
      }
      return error(503, 'Public Research is unavailable');
    }
    begin('worker.render_response');
    if (!knownCompleted || !/^application\/json(?:;|$)/i.test(upstream.headers.get('Content-Type') ?? '')
      || Number(upstream.headers.get('Content-Length')) > 65_536) {
      void upstream.body?.cancel().catch(() => {});
      record('InvalidUpstreamResponse', upstream.status);
      return error(503, 'Public Research is unavailable');
    }
    const result = await boundedBody(upstream.body, 65_536, timeout);
    timeout.throwIfAborted();
    if (!result) {
      record('ResponseTooLarge', upstream.status);
      return error(503, 'Public Research is unavailable');
    }
    record(undefined, upstream.status);
    return new Response(result, {
      headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' },
    });
  } catch (cause) {
    const timedOut = timeout?.aborted || cause?.name === 'TimeoutError';
    record(timedOut ? 'TimeoutError' : researchErrorType(cause), timedOut ? 504 : 503);
    if (timedOut) return error(504, 'Public Research timed out');
    return error(503, 'Public Research is unavailable');
  } finally {
    if (id && config && !dispatched) await releaseLease();
  }
}
