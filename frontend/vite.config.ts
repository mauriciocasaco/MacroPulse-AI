import { fileURLToPath } from 'node:url';
import { defineConfig, type Plugin } from 'vite';

const host = '127.0.0.1';
const port = 5173;
const apiPort = process.env.MARKETPULSE_API_PORT ?? '8000';
const readRoute = /^\/api\/assets(?:\/[1-9]\d{0,18}\/history)?(?:\?|$)/;
const analysisRoute = /^\/api\/analysis$/;

if (!/^[1-9]\d{0,4}$/.test(apiPort) || Number(apiPort) > 65_535) {
  throw new Error('MARKETPULSE_API_PORT must be an integer from 1 to 65535');
}

const localApi: Plugin = {
  name: 'marketpulse-local-api',
  configureServer(server) {
    server.middlewares.use((request, response, next) => {
      const url = request.url ?? '';
      if (!/^\/api(?:\/|\?|$)/.test(url)) return next();

      const { origin, 'content-type': contentType } = request.headers;
      if (origin && origin !== `http://${host}:${port}`) {
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
    fs: { allow: [fileURLToPath(new URL('.', import.meta.url))] },
    proxy: {
      '^/api/': {
        target: `http://${host}:${apiPort}`,
        rewrite: (path) => path.slice(4),
      },
    },
  },
});
