const maxBody = 8192;
const maxResponse = 3 * 1024 * 1024;
const history = /^\/api\/assets\/[1-3]\/history$/;
const macroHistory = /^\/api\/macro\/indicators\/(?:eur-usd|m3-stock|deposit-rate|real-gdp-qoq|hicp-yoy)\/history$/;
const date = /^\d{4}-\d{2}-\d{2}$/;
const maxMacroDays = 2600;
const dayMs = 86_400_000;
const allowedStatuses = new Set([200, 400, 404, 405, 408, 413, 415, 422, 429, 503]);

function error(status, detail, retry) {
  return Response.json({ detail }, {
    status,
    headers: { 'Cache-Control': 'no-store', ...(retry && { 'Retry-After': String(retry) }) },
  });
}

async function boundedBody(stream, max) {
  if (!stream) return new Uint8Array();
  const reader = stream.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > max) {
        void reader.cancel().catch(() => {});
        return null;
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return result;
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
    && last >= first && last - first < maxMacroDays * dayMs;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== '/api' && !url.pathname.startsWith('/api/')) return env.ASSETS.fetch(request);
    const macroEnabled = env.MARKETPULSE_DEMO_MACRO_ENABLED === 'true';
    const macroCatalog = macroEnabled && url.pathname === '/api/macro/indicators';
    const macro = macroEnabled && macroHistory.test(url.pathname);
    const catalog = url.pathname === '/api/assets' || macroCatalog;
    const prices = history.test(url.pathname);
    const analysis = url.pathname === '/api/analysis';
    if (!catalog && !prices && !macro && !analysis) return error(404, 'Not found');
    if (request.method !== (analysis ? 'POST' : 'GET')) return error(405, 'Method not allowed');
    const originHeader = request.headers.get('Origin');
    if ((macroCatalog || macro) && originHeader !== null && originHeader !== url.origin) {
      return error(403, 'Forbidden');
    }
    const historical = prices || macro;
    if (url.search.length > 257 || (!historical && url.search)) return error(400, 'Invalid request');
    if (historical && (url.searchParams.size !== 2 || !date.test(url.searchParams.get('start') ?? '')
      || !date.test(url.searchParams.get(macro ? 'end' : 'end_exclusive') ?? ''))) {
      return error(422, 'Invalid request parameters');
    }
    if (macro && !macroWindow(url.searchParams)) return error(422, 'Invalid request parameters');
    if (!analysis && request.body) return error(413, 'Request too large');

    // The feature remains unavailable until a strict global analysis quota exists.
    // Consume no visitor body and send no traffic to Render for this endpoint.
    if (analysis) {
      const length = request.headers.get('Content-Length');
      if (length !== null && (!/^\d{1,10}$/.test(length) || Number(length) > maxBody)) {
        return error(413, 'Request too large');
      }
      return error(503, 'Public demo analysis is disabled');
    }

    const base = origin(env);
    const ip = request.headers.get('CF-Connecting-IP');
    const limiter = catalog ? env.CATALOG_RATE_LIMIT : env.HISTORY_RATE_LIMIT;
    if (!base || !ip || !/^[0-9a-fA-F:.]{1,45}$/.test(ip) || !limiter) {
      return error(503, 'Public demo service unavailable');
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
        return error(503, 'Public demo service unavailable');
      }
      if (status !== 200) {
        void upstream.body?.cancel().catch(() => {});
        const retry = headers.get('Retry-After');
        if (status === 429) return error(429, 'Rate limit exceeded',
          /^[1-9]\d?$/.test(retry ?? '') && Number(retry) <= 60 ? retry : 60);
        return error(status, status === 503 ? 'Public demo service unavailable' : 'Invalid request');
      }
      const body = await boundedBody(upstream.body, maxResponse);
      if (!body) return error(503, 'Public demo service unavailable');
      return new Response(body, {
        status,
        headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' },
      });
    } catch (cause) {
      if (timeout?.aborted || cause?.name === 'TimeoutError') return error(504, 'Public demo request timed out');
      return error(503, 'Public demo service unavailable');
    }
  },
};
