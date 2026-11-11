import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import { marketPulsePwa } from './pwa/build.ts';
import { localApi, loopback, port, resolveLanDev } from './pwa/local-api.ts';

export default defineConfig(({ command, mode, isPreview }) => {
  const apiPort = process.env.MARKETPULSE_API_PORT ?? '8000';
  if (!/^[1-9]\d{0,4}$/.test(apiPort) || Number(apiPort) > 65_535) {
    throw new Error('MARKETPULSE_API_PORT must be an integer from 1 to 65535');
  }

  const launcher = mode === 'windows-launcher' && (command === 'build' || isPreview === true);
  const lan = launcher && isPreview && process.env.MARKETPULSE_LAN_DEV === 'true'
    ? resolveLanDev(process.env, true)
    : command === 'serve' && !isPreview && mode === 'development' && process.env.NODE_ENV !== 'production'
    && process.env.MARKETPULSE_LAN_DEV === 'true' ? resolveLanDev(process.env) : undefined;
  const macroAiEnabled = process.env.MARKETPULSE_MACRO_AI_ENABLED === 'true';
  const api = localApi(lan, macroAiEnabled, apiPort, launcher);
  const proxy = { '^/api/': api.proxy };

  return {
    base: './',
    plugins: launcher ? [api.plugin] : [api.plugin, marketPulsePwa()],
    envDir: false,
    envPrefix: [],
    define: {
      'import.meta.env.MARKETPULSE_DEMO_MACRO_ENABLED': JSON.stringify(process.env.MARKETPULSE_DEMO_MACRO_ENABLED === 'true'),
      'import.meta.env.MARKETPULSE_MACRO_AI_ENABLED': JSON.stringify(macroAiEnabled),
      'import.meta.env.MARKETPULSE_LAN_DEV': JSON.stringify(lan ? 'true' : 'false'),
      'import.meta.env.MARKETPULSE_SAFARI_URL': JSON.stringify(launcher && process.env.MARKETPULSE_LAN_IP
        ? `http://${process.env.MARKETPULSE_LAN_IP}:${port}/` : ''),
    },
    build: { license: true, ...(launcher && { outDir: 'node_modules/.cache/marketpulse-launcher' }) },
    preview: launcher ? {
      host: lan?.host ?? loopback, port, strictPort: true, cors: false, proxy,
      headers: { 'Cache-Control': 'no-store', 'X-Frame-Options': 'DENY' },
    } : { host: loopback, proxy: {} },
    server: {
      host: lan?.host ?? loopback,
      port,
      strictPort: true,
      cors: false,
      ...(lan && { ws: false, hmr: false }),
      fs: { allow: [fileURLToPath(new URL('.', import.meta.url))] },
      proxy,
    },
  };
});
