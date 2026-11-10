import { BlockList } from 'node:net';
import { networkInterfaces } from 'node:os';
import { fileURLToPath } from 'node:url';
import { defineConfig, type Plugin } from 'vite';
import { marketPulsePwa } from './pwa/build.ts';
import { allowsLocalRead } from './pwa/local-read.ts';

const loopback = '127.0.0.1';
const lanIp = process.env.MARKETPULSE_LAN_IP;
const host = lanIp ?? loopback;
const port = 5173;
const apiPort = process.env.MARKETPULSE_API_PORT ?? '8000';
const macroAnalysisRoute = '/api/macro/analysis';
const macroAiEnabled = process.env.MARKETPULSE_MACRO_AI_ENABLED === 'true';

if (!/^[1-9]\d{0,4}$/.test(apiPort) || Number(apiPort) > 65_535) {
  throw new Error('MARKETPULSE_API_PORT must be an integer from 1 to 65535');
}

const lanPeers = new BlockList();
if (lanIp !== undefined) {
  const adapter = Object.values(networkInterfaces()).flat().find((address) =>
    address?.family === 'IPv4' && !address.internal && address.address === lanIp);
  if (!adapter?.cidr || !/^(?:10\.|172\.(?:1[6-9]|2\d|3[01])\.|192\.168\.)/.test(lanIp)) {
    throw new Error('MARKETPULSE_LAN_IP must be a private IPv4 address assigned to this PC');
  }
  const prefix = Number(adapter.cidr.split('/')[1]);
  lanPeers.addSubnet(lanIp, prefix, 'ipv4');
}

const localApi: Plugin = {
  name: 'marketpulse-local-api',
  configureServer(server) {
    if (lanIp !== undefined) {
      server.httpServer?.on('connection', (socket) => {
        socket.on('error', (error: NodeJS.ErrnoException) => {
          if (error.code !== 'ECONNRESET') server.config.logger.error('LAN client connection failed');
        });
      });
    }
    server.middlewares.use((request, response, next) => {
      if (lanIp !== undefined && (request.headers.host !== `${host}:${port}`
        || !lanPeers.check(request.socket.remoteAddress ?? '', 'ipv4'))) {
        response.writeHead(403).end();
        return;
      }
      const url = request.url ?? '';
      if (!/^\/api(?:\/|\?|$)/.test(url)) return next();

      const { origin, 'content-type': contentType } = request.headers;
      if ((origin && origin !== `http://${host}:${server.config.server.port ?? port}`)
        || Object.keys(request.headers).some((name) => name === 'forwarded' || name.startsWith('x-forwarded-'))) {
        response.writeHead(403).end();
        return;
      }

      const read = request.method === 'GET' && allowsLocalRead(url, lanIp !== undefined);
      const analysis = request.method === 'POST'
        && lanIp === undefined && macroAiEnabled
        && server.config.mode !== 'public-demo' && url === macroAnalysisRoute
        && /^application\/json(?:;|$)/i.test(contentType ?? '');
      if (read || analysis) return next();
      response.writeHead(404).end();
    });
  },
};

export default defineConfig({
  base: './',
  plugins: [localApi, marketPulsePwa()],
  envDir: false,
  envPrefix: [],
  define: {
    'import.meta.env.MARKETPULSE_DEMO_MACRO_ENABLED': JSON.stringify(process.env.MARKETPULSE_DEMO_MACRO_ENABLED === 'true'),
    'import.meta.env.MARKETPULSE_MACRO_AI_ENABLED': JSON.stringify(macroAiEnabled),
  },
  build: { license: true },
  preview: { host: loopback, proxy: {} },
  server: {
    host,
    port,
    strictPort: true,
    cors: false,
    ...(lanIp !== undefined && { ws: false, hmr: false }),
    fs: { allow: [fileURLToPath(new URL('.', import.meta.url))] },
    proxy: {
      '^/api/': {
        target: `http://${loopback}:${apiPort}`,
        changeOrigin: true,
        rewrite: (path) => path.slice(4),
      },
    },
  },
});
