import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { readdirSync, readFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const { Miniflare, Response: RuntimeResponse, convertV4MiniflareOptions } = await import(pathToFileURL(process.env.MACROPULSE_MINIFLARE_ENTRY));
const fixture = JSON.parse(readFileSync(new URL('./origin-attestation-v1.json', import.meta.url)));
const host = 'https://demo.example';
const callback = fixture.vectors[0].callback.path;
const render = 'https://fixture.onrender.com/macro/analysis';
const digest = (value) => createHash('sha256').update(value).digest('hex');
const wrangler = JSON.parse(readFileSync('frontend/wrangler.jsonc'));
const root = resolve('frontend/worker');
const dependencies = readdirSync(root).filter((file) => file.endsWith('.mjs')
  && file !== 'public-demo.mjs' && !file.endsWith('.node-test.mjs'));

let captured;
function startRuntime() {
  return new Miniflare(convertV4MiniflareOptions({
    name: 'security-fixture',
    modulesRoot: root,
    modules: ['public-demo.mjs', ...dependencies].map((file) => ({ type: 'ESModule', path: resolve(root, file) })),
    compatibilityDate: wrangler.compatibility_date,
    durableObjects: { RESEARCH_QUOTA: { className: 'ResearchQuota', useSQLite: true } },
    durableObjectsPersist: resolve(process.argv[2]),
    ratelimits: Object.fromEntries(wrangler.ratelimits.map(({ name, ...config }) => [name, config])),
    bindings: {
      MARKETPULSE_DEMO_MACRO_ENABLED: 'true',
      MARKETPULSE_DEMO_ORIGIN_URL: 'https://fixture.onrender.com',
      MACROPULSE_RESEARCH_ENABLED: 'true',
      MACROPULSE_RESEARCH_HOSTNAME: 'demo.example',
      MACROPULSE_RESEARCH_ORIGIN_KEYS: JSON.stringify(fixture.origin_keys),
      MACROPULSE_RESEARCH_ORIGIN_ACTIVE_KID: fixture.origin_keys[0][0],
      MACROPULSE_RESEARCH_VERIFY_KEYS: JSON.stringify(fixture.verify_keys),
      MACROPULSE_RESEARCH_HASH_KEY: digest('synthetic test visitor hashing key'),
      MACROPULSE_ADMIN_SECRET: digest('synthetic test admin key'),
      MACROPULSE_TURNSTILE_SITE_KEY: 'synthetic-workerd-sitekey',
      MACROPULSE_TURNSTILE_SECRET: 'synthetic-workerd-turnstile-secret',
    },
    async outboundService(request) {
      if (request.url === 'https://challenges.cloudflare.com/turnstile/v0/siteverify') {
        const form = new URLSearchParams(await request.text());
        return RuntimeResponse.json({
          success: form.get('response') !== 'reject', hostname: 'demo.example',
          action: 'research', challenge_ts: new Date().toISOString(),
        });
      }
      assert.equal(request.url, render);
      captured = {
        headers: Object.fromEntries([...request.headers].filter(([name]) =>
          name === 'content-type' || name.startsWith('x-macropulse-') || name === 'x-marketpulse-client-ip')),
        body: Buffer.from(await request.arrayBuffer()).toString('base64'),
      };
      // A lost upstream response keeps the lease alive while the fixture replays these exact bytes.
      return new RuntimeResponse(null, { status: 503 });
    },
  }));
}

let runtime = startRuntime();
const server = createServer(async (request, reply) => {
  try {
    if (request.method !== 'POST') return reply.writeHead(405).end();
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const input = Buffer.concat(chunks);
    if (request.url === '/restart') {
      await runtime.dispose();
      runtime = startRuntime();
      await runtime.ready;
      return reply.writeHead(204).end();
    }
    if (request.url === '/admit') {
      const { scope, reject } = JSON.parse(input);
      captured = null;
      const response = await runtime.dispatchFetch(host + '/api/macro/analysis', {
        method: 'POST',
        headers: {
          Origin: host, 'Content-Type': 'application/json', 'CF-Connecting-IP': '192.0.2.5',
          ...(scope === 'admin' ? { 'X-MacroPulse-Admin-Secret': digest('synthetic test admin key') }
            : { 'X-MacroPulse-Turnstile': reject ? 'reject' : randomUUID() }),
        },
        body: Buffer.from(fixture.vectors[0].body_base64, 'base64'),
      });
      await response.arrayBuffer();
      return reply.writeHead(200, { 'Content-Type': 'application/json' })
        .end(JSON.stringify({ status: response.status, wire: captured }));
    }
    if (request.url === callback) {
      const response = await runtime.dispatchFetch(host + callback, {
        method: 'POST', body: input,
        headers: {
          Authorization: request.headers.authorization,
          'X-MacroPulse-Verify-Key-Id': request.headers['x-macropulse-verify-key-id'],
          'Content-Type': request.headers['content-type'],
          'CF-Connecting-IP': '192.0.2.20',
        },
      });
      await response.arrayBuffer();
      return reply.writeHead(response.status).end();
    }
    return reply.writeHead(404).end();
  } catch (error) {
    console.error(error.name, error.stack);
    reply.writeHead(500).end();
  }
});
await runtime.ready;
server.listen(0, '127.0.0.1', () => console.log(JSON.stringify({ fixturePort: server.address().port })));
process.stdin.resume();
process.stdin.on('end', async () => {
  server.close();
  await runtime.dispose();
});
