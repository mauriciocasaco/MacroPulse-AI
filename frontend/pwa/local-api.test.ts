import { createHash } from 'node:crypto';
import { createServer as createHttpServer, request, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { createServer, type ConfigEnv, type ViteDevServer } from 'vite';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import viteConfig from '../vite.config.ts';
import { resolveLanDev } from './local-api.ts';
import { launcherReadHeader } from './local-read.ts';

const host = '192.168.20.10';
const device = '192.168.20.11';
const authority = `${host}:5173`;
const origin = `http://${authority}`;
const token = createHash('sha256').update('synthetic LAN proxy test fixture').digest('hex');
const authHeader = 'x-marketpulse-lan-auth';
const analysis = '/api/macro/analysis';
const json = { 'Content-Type': 'application/json' };

vi.mock('node:os', async (importOriginal) => ({
  ...await importOriginal<typeof import('node:os')>(),
  networkInterfaces: () => ({ WiFi: [{ family: 'IPv4', internal: false, address: host, cidr: `${host}/24` }] }),
}));

function config(overrides: Partial<ConfigEnv> = {}) {
  if (typeof viteConfig !== 'function') throw new Error('Expected a Vite config factory');
  return viteConfig({ command: 'serve', mode: 'development', ...overrides });
}

async function listen(server: Server): Promise<number> {
  await new Promise<void>((resolve, reject) => server.once('error', reject).listen(0, '127.0.0.1', resolve));
  return (server.address() as AddressInfo).port;
}

function send(port: number, path: string, headers: Record<string, string | string[]> = {}, method = 'GET') {
  return new Promise<{ status: number; body: string; retry: string | undefined }>((resolve, reject) => {
    const req = request({ host: '127.0.0.1', port, path, method, headers }, (res) => {
      const chunks: Buffer[] = [];
      res.on('data', (chunk: Buffer) => chunks.push(chunk));
      res.on('error', reject);
      res.on('end', () => resolve({
        status: res.statusCode ?? 0,
        body: Buffer.concat(chunks).toString(),
        retry: res.headers['retry-after'],
      }));
    });
    req.on('error', reject);
    req.end(method === 'POST' ? '{"question":"Synthetic fixture"}' : undefined);
  });
}

describe('LAN development listener', () => {
  let vite: ViteDevServer | undefined;
  const servers: Server[] = [];

  beforeEach(() => {
    vi.stubEnv('NODE_ENV', 'test');
    vi.stubEnv('MARKETPULSE_LAN_DEV', 'true');
    vi.stubEnv('MARKETPULSE_LAN_IP', host);
    vi.stubEnv('MARKETPULSE_LAN_DEVICE_IP', device);
    vi.stubEnv('MARKETPULSE_LAN_TOKEN', token);
    vi.stubEnv('MARKETPULSE_MACRO_AI_ENABLED', 'true');
  });

  afterEach(async () => {
    await Promise.all(servers.splice(0).map((server) => new Promise<void>((resolve) => server.close(() => resolve()))));
    await vite?.close();
    vite = undefined;
    vi.unstubAllEnvs();
  });

  async function fixture(peer?: string, launcher = false) {
    const forwarded: { method?: string; url?: string; origin?: string; authenticated: boolean; body: string }[] = [];
    const backend = createHttpServer(async (req, res) => {
      const body = await Array.fromAsync(req);
      forwarded.push({ method: req.method, url: req.url, origin: req.headers.origin,
        authenticated: req.headers[authHeader] === token, body: Buffer.concat(body).toString() });
      res.writeHead(200).end('synthetic backend');
    });
    servers.push(backend);
    vi.stubEnv('MARKETPULSE_API_PORT', String(await listen(backend)));

    const options = await config(launcher ? { mode: 'windows-launcher', isPreview: true } : {});
    vite = await createServer({ ...options, configFile: false, logLevel: 'silent',
      server: { ...options.server, middlewareMode: true, ws: false, hmr: false } });
    const gateway = createHttpServer((req, res) => {
      // Model private peers without binding a real network interface during tests.
      if (peer) Object.defineProperty(req.socket, 'remoteAddress', { value: peer });
      vite!.middlewares(req, res);
    });
    servers.push(gateway);
    return { port: await listen(gateway), forwarded };
  }

  it('defaults to loopback even with stale LAN configuration, including a production or public build/serve', async () => {
    vi.stubEnv('MARKETPULSE_LAN_IP', 'invalid');
    for (const overrides of [
      { command: 'build' }, { command: 'build', mode: 'public-demo' }, { isPreview: true },
      { mode: 'public-demo' }, { mode: 'production' },
    ] as const) {
      const options = await config(overrides);
      expect(options.server?.host).toBe('127.0.0.1');
      expect(options.define?.['import.meta.env.MARKETPULSE_LAN_DEV']).toBe('"false"');
    }
    vi.stubEnv('NODE_ENV', 'production');
    expect((await config()).server?.host).toBe('127.0.0.1');
    vi.stubEnv('NODE_ENV', 'test');
    vi.stubEnv('MARKETPULSE_LAN_DEV', undefined);
    const { port, forwarded } = await fixture();
    expect((await send(port, '/api/macro/indicators')).status).toBe(200);
    const editor = await send(port, '/__open-in-editor');
    expect(editor.status).toBe(500);
    expect(editor.body).toContain('required query param');
    const localOrigin = 'http://127.0.0.1:5173';
    for (let index = 0; index < 4; index++) {
      expect((await send(port, analysis, { ...json, Origin: localOrigin }, 'POST')).status).toBe(200);
    }
    expect(forwarded).toHaveLength(5);
    expect(forwarded.every(({ authenticated }) => !authenticated)).toBe(true);
    expect((await send(port, analysis, { ...json, Origin: origin }, 'POST')).status).toBe(403);
  });

  it('refuses an invalid interface, device or proxy token', () => {
    vi.stubEnv('MARKETPULSE_LAN_IP', '192.168.20.12');
    expect(() => config()).toThrow('MARKETPULSE_LAN_IP');
    vi.stubEnv('MARKETPULSE_LAN_IP', host);
    vi.stubEnv('MARKETPULSE_LAN_DEVICE_IP', '192.168.21.11');
    expect(() => config()).toThrow('MARKETPULSE_LAN_DEVICE_IP');
    vi.stubEnv('MARKETPULSE_LAN_DEVICE_IP', device);
    vi.stubEnv('MARKETPULSE_LAN_TOKEN', 'invalid');
    expect(() => config()).toThrow('MARKETPULSE_LAN_TOKEN');
  });

  it('enables subnet admission only for the explicit launcher LAN preview', async () => {
    vi.stubEnv('MARKETPULSE_LAN_DEVICE_IP', undefined);
    const options = await config({ isPreview: true, mode: 'windows-launcher' });
    expect(options.preview).toMatchObject({ host, port: 5173, strictPort: true, cors: false,
      headers: { 'X-Frame-Options': 'DENY' } });
    const lan = resolveLanDev(process.env, true);
    expect(lan.allowsPeer(device)).toBe(true);
    expect(lan.allowsPeer('192.168.20.12')).toBe(true);
    expect(lan.allowsPeer('192.168.21.11')).toBe(false);
    expect(lan.allowsPeer('127.0.0.1')).toBe(false);
    expect(() => config()).toThrow('MARKETPULSE_LAN_DEVICE_IP');
    expect((await config({ isPreview: true })).preview?.host).toBe('127.0.0.1');
  });

  it('blocks cross-origin browser API reads and limits the launcher LAN history budget', async () => {
    vi.stubEnv('MARKETPULSE_LAN_DEVICE_IP', undefined);
    const { port, forwarded } = await fixture(device, true);
    const headers = { Host: authority };
    const history = '/api/macro/indicators/eur-usd/history?start=2026-01-01&end=2026-01-31';
    for (const site of ['cross-site', 'same-site']) {
      expect((await send(port, history, { ...headers, 'Sec-Fetch-Site': site })).status).toBe(403);
    }
    expect((await send(port, history, headers)).status).toBe(403);
    const localHeaders = { ...headers, [launcherReadHeader]: '1' };
    expect((await send(port, history, { ...localHeaders, Origin: 'http://foreign.test' })).status).toBe(403);
    expect((await send(port, history, { ...headers, Origin: 'http://foreign.test',
      'Access-Control-Request-Headers': launcherReadHeader }, 'OPTIONS')).status).toBe(403);
    expect(forwarded).toHaveLength(0);
    for (let index = 0; index < 60; index++) {
      expect((await send(port, history, localHeaders)).status).toBe(200);
    }
    const throttled = await send(port, history, localHeaders);
    expect(throttled.status).toBe(429);
    expect(Number(throttled.retry)).toBeGreaterThan(0);
    expect(forwarded).toHaveLength(60);
    expect((await send(port, '/api/macro/indicators', localHeaders)).status).toBe(200);
  });

  it('denies another private peer before serving the workspace or forwarding requests', async () => {
    const { port, forwarded } = await fixture('192.168.20.12');
    const headers = { Host: authority };
    expect((await send(port, '/', headers)).status).toBe(403);
    expect((await send(port, '/__open-in-editor', headers)).status).toBe(403);
    expect((await send(port, '/api/macro/indicators', { ...headers, 'X-Forwarded-For': device })).status).toBe(403);
    expect(forwarded).toHaveLength(0);
  });

  it('blocks the Vite editor launcher for an authorized LAN peer', async () => {
    const { port, forwarded } = await fixture(device);
    for (const path of [
      '/__open-in-editor', '/__OPEN-IN-EDITOR?file=', '/__open-in-editor/',
      '/__open-in-editor.txt', `${origin}/__open-in-editor?file=`,
    ]) {
      expect((await send(port, path, { Host: authority })).status, path).toBe(404);
    }
    expect(forwarded).toHaveLength(0);
  });

  it('serves the authorized workspace and proxies five histories and throttled same-origin RAG', async () => {
    const options = await config();
    expect(options.server).toMatchObject({ host, port: 5173, cors: false, ws: false, hmr: false });
    expect(options.define?.['import.meta.env.MARKETPULSE_LAN_DEV']).toBe('"true"');
    const { port, forwarded } = await fixture(device);
    const headers = { Host: authority };
    expect((await send(port, '/', { Host: 'other.test:5173' })).status).toBe(403);
    const shell = await send(port, '/', headers);
    expect(shell.status).toBe(200);
    expect(shell.body).toContain('<script type="module" src="/src/main.tsx"></script>');
    const appModule = await send(port, '/src/App.tsx', headers);
    expect(appModule.status).toBe(200);
    expect(appModule.body.includes(token)).toBe(false);
    expect((await send(port, '/api/macro/indicators', headers)).status).toBe(200);
    for (const indicator of ['eur-usd', 'm3-stock', 'deposit-rate', 'real-gdp-qoq', 'hicp-yoy']) {
      expect((await send(port, `/api/macro/indicators/${indicator}/history?start=2026-01-01&end=2026-01-31`, headers)).status).toBe(200);
    }
    expect(forwarded.every(({ authenticated }) => !authenticated)).toBe(true);
    for (const path of ['/api/health', '/api/assets', '/api/macro/indicators/unknown/history', '/api/docs']) {
      expect((await send(port, path, headers)).status).toBe(404);
    }
    const submit = (extra: Record<string, string | string[]> = {}) => send(port, analysis, { ...headers, ...json, ...extra }, 'POST');
    expect((await submit()).status).toBe(403);
    expect((await submit({ Origin: 'http://127.0.0.1:5173' })).status).toBe(403);
    expect((await submit({ Origin: [origin, origin] })).status).toBe(403);
    expect((await submit({ Origin: origin, [authHeader]: token })).status).toBe(403);
    expect((await submit({ Origin: origin, Forwarded: 'for=127.0.0.1' })).status).toBe(403);
    expect((await submit({ Origin: origin, 'X-Forwarded-For': device })).status).toBe(403);
    expect((await submit({ Origin: origin, 'Content-Type': 'text/plain' })).status).toBe(404);
    for (let index = 0; index < 3; index++) {
      expect((await submit({ Origin: origin })).status).toBe(200);
    }
    const throttled = await submit({ Origin: origin });
    expect(throttled.status).toBe(429);
    expect(Number(throttled.retry)).toBeGreaterThan(0);
    const submissions = forwarded.filter(({ method }) => method === 'POST');
    expect(submissions).toEqual(Array(3).fill({ method: 'POST', url: '/macro/analysis', origin,
      authenticated: true, body: '{"question":"Synthetic fixture"}' }));
  });

  it('keeps RAG gated independently of LAN reads', async () => {
    vi.stubEnv('MARKETPULSE_MACRO_AI_ENABLED', undefined);
    const { port, forwarded } = await fixture(device);
    const headers = { Host: authority };
    expect((await send(port, '/api/macro/indicators', headers)).status).toBe(200);
    expect((await send(port, analysis, { ...headers, ...json, Origin: origin }, 'POST')).status).toBe(404);
    expect(forwarded).toHaveLength(1);
  });
});
