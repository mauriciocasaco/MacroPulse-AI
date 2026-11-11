import type { IncomingMessage } from 'node:http';
import { BlockList, isIPv4 } from 'node:net';
import { networkInterfaces } from 'node:os';
import type { Plugin, ProxyOptions } from 'vite';
import { allowsLocalRead } from './local-read.ts';

export const loopback = '127.0.0.1';
export const port = 5173;
const analysisRoute = '/api/macro/analysis';
const authHeader = 'x-marketpulse-lan-auth';
const quota = 3;
const windowMs = 60_000;

function privateIPv4(address: string): boolean {
  return isIPv4(address) && /^(?:10\.|172\.(?:1[6-9]|2\d|3[01])\.|192\.168\.)/.test(address);
}

export function resolveLanDev(env: NodeJS.ProcessEnv) {
  const { MARKETPULSE_LAN_IP: host = '', MARKETPULSE_LAN_DEVICE_IP: device = '',
    MARKETPULSE_LAN_TOKEN: token = '' } = env;
  const adapter = Object.values(networkInterfaces()).flat().find((address) =>
    address?.family === 'IPv4' && !address.internal && address.address === host);
  if (!privateIPv4(host) || !adapter?.cidr) {
    throw new Error('MARKETPULSE_LAN_IP must be a private IPv4 address assigned to this PC');
  }
  const subnet = new BlockList();
  subnet.addSubnet(host, Number(adapter.cidr.split('/')[1]), 'ipv4');
  if (!privateIPv4(device) || device === host || !subnet.check(device, 'ipv4')) {
    throw new Error('MARKETPULSE_LAN_DEVICE_IP must be a distinct private IPv4 address on the PC interface subnet');
  }
  if (!/^[a-f0-9]{64}$/.test(token)) {
    throw new Error('MARKETPULSE_LAN_TOKEN must be a 32-byte random token encoded as lowercase hex');
  }
  return { host, device, token };
}

export function localApi(lan: ReturnType<typeof resolveLanDev> | undefined, aiEnabled: boolean, apiPort: string) {
  const host = lan?.host ?? loopback;
  const authorized = new WeakSet<IncomingMessage>();
  const submissions: number[] = [];

  const plugin: Plugin = {
    name: 'marketpulse-local-api',
    configureServer(server) {
      if (server.config.server.host !== host || (lan && server.config.server.port !== port)) {
        throw new Error('Use the configured MarketPulse Vite host and LAN port');
      }
      if (lan) {
        server.httpServer?.on('connection', (socket) => {
          socket.on('error', (error: NodeJS.ErrnoException) => {
            if (error.code !== 'ECONNRESET') server.config.logger.error('LAN client connection failed');
          });
        });
      }

      const authority = `${host}:${server.config.server.port ?? port}`;
      const allowedOrigin = `http://${authority}`;
      server.middlewares.use((request, response, next) => {
        const peer = request.socket.remoteAddress;
        if (lan ? peer !== lan.device || request.headersDistinct.host?.length !== 1
          || request.headers.host !== authority : peer !== loopback) {
          response.writeHead(403).end();
          return;
        }

        const url = request.url ?? '';
        if (!/^\/api(?:\/|\?|$)/.test(url)) return next();

        const { origin, 'content-type': contentType } = request.headers;
        if ((origin && origin !== allowedOrigin) || (request.headersDistinct.origin?.length ?? 0) > 1
          || Object.keys(request.headers).some((name) => name === 'forwarded'
            || name.startsWith('x-forwarded-') || name === authHeader)) {
          response.writeHead(403).end();
          return;
        }

        if (request.method === 'GET' && allowsLocalRead(url)) return next();
        const analysis = request.method === 'POST' && aiEnabled && server.config.mode !== 'public-demo'
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

        const now = performance.now();
        while (submissions.length && now - submissions[0] >= windowMs) submissions.shift();
        if (submissions.length >= quota) {
          response.writeHead(429, {
            'Retry-After': Math.ceil((submissions[0] + windowMs - now) / 1000),
            'Cache-Control': 'no-store',
          }).end();
          return;
        }
        submissions.push(now);
        authorized.add(request);
        return next();
      });
      if (lan) {
        // Match Vite's Connect route before its middleware can launch a host process.
        server.middlewares.use('/__open-in-editor', (_request, response) => response.writeHead(404).end());
      }
    },
  };

  const proxy: ProxyOptions = {
    target: `http://${loopback}:${apiPort}`,
    changeOrigin: true,
    rewrite: (path) => path.slice(4),
    configure(proxyServer) {
      proxyServer.on('proxyReq', (proxyRequest, request) => {
        if (lan && authorized.has(request)) proxyRequest.setHeader(authHeader, lan.token);
      });
    },
  };

  return { plugin, proxy };
}
