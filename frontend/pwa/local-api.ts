import type { IncomingMessage, ServerResponse } from 'node:http';
import { BlockList, isIPv4 } from 'node:net';
import { networkInterfaces } from 'node:os';
import type { Connect, Plugin, ProxyOptions } from 'vite';
import { allowsLocalRead, launcherReadHeader } from './local-read.ts';

export const loopback = '127.0.0.1';
export const port = 5173;
const analysisRoute = '/api/macro/analysis';
const authHeader = 'x-marketpulse-lan-auth';
const quota = 3;
const historyQuota = 60;
const windowMs = 60_000;
const launcherAsset = /^\/(?:index\.html|manifest\.webmanifest|icons\/(?:favicon\.svg|[\w-]+\.png)|assets\/[\w-]+\.(?:js|css))?(?:\?[^#]*)?$/;

function privateIPv4(address: string): boolean {
  return isIPv4(address) && /^(?:10\.|172\.(?:1[6-9]|2\d|3[01])\.|192\.168\.)/.test(address);
}

function takeRequest(submissions: number[], limit: number, response: ServerResponse): boolean {
  const now = performance.now();
  while (submissions.length && now - submissions[0] >= windowMs) submissions.shift();
  if (submissions.length >= limit) {
    response.writeHead(429, {
      'Retry-After': Math.ceil((submissions[0] + windowMs - now) / 1000),
      'Cache-Control': 'no-store',
    }).end();
    return false;
  }
  submissions.push(now);
  return true;
}

export function resolveLanDev(env: NodeJS.ProcessEnv, trustSubnet = false) {
  const { MARKETPULSE_LAN_IP: host = '', MARKETPULSE_LAN_DEVICE_IP: device = '',
    MARKETPULSE_LAN_TOKEN: token = '' } = env;
  const adapter = Object.values(networkInterfaces()).flat().find((address) =>
    address?.family === 'IPv4' && !address.internal && address.address === host);
  if (!privateIPv4(host) || !adapter?.cidr) {
    throw new Error('MARKETPULSE_LAN_IP must be a private IPv4 address assigned to this PC');
  }
  const subnet = new BlockList();
  subnet.addSubnet(host, Number(adapter.cidr.split('/')[1]), 'ipv4');
  if (!trustSubnet && (!privateIPv4(device) || device === host || !subnet.check(device, 'ipv4'))) {
    throw new Error('MARKETPULSE_LAN_DEVICE_IP must be a distinct private IPv4 address on the PC interface subnet');
  }
  if (!/^[a-f0-9]{64}$/.test(token)) {
    throw new Error('MARKETPULSE_LAN_TOKEN must be a 32-byte random token encoded as lowercase hex');
  }
  return { host, token, allowsPeer: (peer: string) => trustSubnet
    ? privateIPv4(peer) && subnet.check(peer, 'ipv4') : peer === device };
}

export function localApi(
  lan: ReturnType<typeof resolveLanDev> | undefined, aiEnabled: boolean, apiPort: string, launcher = false,
) {
  const host = lan?.host ?? loopback;
  const authorized = new WeakSet<IncomingMessage>();
  const submissions: number[] = [];
  const historyReads: number[] = [];

  function guard(middlewares: Connect.Server, options: { host?: string | boolean; port?: number }, mode: string) {
    if (options.host !== host || ((lan || launcher) && options.port !== port)) {
      throw new Error('Use the configured MarketPulse Vite host and port');
    }
    const authority = `${host}:${options.port ?? port}`;
    const allowedOrigin = `http://${authority}`;
    middlewares.use((request, response, next) => {
      const peer = request.socket.remoteAddress ?? '';
      if (!(lan ? lan.allowsPeer(peer) : peer === loopback)
        || ((lan || launcher) && (request.headersDistinct.host?.length !== 1 || request.headers.host !== authority))) {
        response.writeHead(403).end();
        return;
      }

      const url = request.url ?? '';
      const apiRequest = /^\/api(?:\/|\?|$)/.test(url);
      const { origin, 'content-type': contentType } = request.headers;
      if ((apiRequest || launcher) && ((origin !== undefined && origin !== allowedOrigin)
          || (request.headersDistinct.origin?.length ?? 0) > 1
          || Object.keys(request.headers).some((name) => name === 'forwarded'
            || name.startsWith('x-forwarded-') || name === authHeader))) {
        response.writeHead(403).end();
        return;
      }
      if (!apiRequest) {
        if (launcher && (!['GET', 'HEAD'].includes(request.method ?? '') || !launcherAsset.test(url))) {
          response.writeHead(404).end();
          return;
        }
        return next();
      }

      if (request.method === 'GET' && allowsLocalRead(url)) {
        // Cross-origin HTML subresources and no-CORS fetches cannot set this header.
        if (launcher && (request.headersDistinct[launcherReadHeader]?.length !== 1
          || request.headers[launcherReadHeader] !== '1')) {
          response.writeHead(403).end();
          return;
        }
        if (launcher && lan && url.split('?', 1)[0].endsWith('/history')
          && !takeRequest(historyReads, historyQuota, response)) return;
        return next();
      }
      const analysis = request.method === 'POST' && aiEnabled && mode !== 'public-demo'
        && url === analysisRoute && /^application\/json(?:;|$)/i.test(contentType ?? '');
      if (!analysis) {
        response.writeHead(404).end();
        return;
      }
      if (!lan) return next();
      if (origin !== allowedOrigin) {
        response.writeHead(403).end();
        return;
      }

      if (!takeRequest(submissions, quota, response)) return;
      authorized.add(request);
      return next();
    });
  }

  const plugin: Plugin = {
    name: 'marketpulse-local-api',
    configureServer(server) {
      guard(server.middlewares, server.config.server, server.config.mode);
      if (lan) {
        server.httpServer?.on('connection', (socket) => {
          socket.on('error', (error: NodeJS.ErrnoException) => {
            if (error.code !== 'ECONNRESET') server.config.logger.error('LAN client connection failed');
          });
        });
        // Match Vite's Connect route before its middleware can launch a host process.
        server.middlewares.use('/__open-in-editor', (_request, response) => response.writeHead(404).end());
      }
    },
    configurePreviewServer(server) {
      if (launcher) guard(server.middlewares, server.config.preview, server.config.mode);
    },
  };

  const proxy: ProxyOptions = {
    target: `http://${loopback}:${apiPort}`,
    changeOrigin: true,
    rewrite: (path) => path.slice(4),
    configure(proxyServer) {
      proxyServer.on('proxyReq', (proxyRequest, request) => {
        if (launcher) proxyRequest.removeHeader(launcherReadHeader);
        if (lan && authorized.has(request)) proxyRequest.setHeader(authHeader, lan.token);
      });
    },
  };

  return { plugin, proxy };
}
