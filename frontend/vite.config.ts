import { BlockList } from 'node:net';
import { networkInterfaces } from 'node:os';
import { fileURLToPath } from 'node:url';
import { defineConfig, type Plugin } from 'vite';

const loopback = '127.0.0.1';
const lanIp = process.env.MARKETPULSE_LAN_IP;
const host = lanIp ?? loopback;
const port = 5173;
const apiPort = process.env.MARKETPULSE_API_PORT ?? '8000';
const readRoute = /^\/api\/assets(?:\/[1-9]\d{0,18}\/history)?(?:\?|$)/;
const analysisRoute = /^\/api\/analysis$/;

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
      if ((origin && origin !== `http://${host}:${port}`)
        || Object.keys(request.headers).some((name) => name === 'forwarded' || name.startsWith('x-forwarded-'))) {
        response.writeHead(403).end();
        return;
      }

      const read = request.method === 'GET' && readRoute.test(url);
      const analysis = request.method === 'POST' && analysisRoute.test(url)
        && /^application\/json(?:;|$)/i.test(contentType ?? '');
      if (read || analysis) return next();
      response.writeHead(404).end();
    });
  },
};

export default defineConfig({
  plugins: [localApi],
  envDir: false,
  envPrefix: [],
  build: { license: true },
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
